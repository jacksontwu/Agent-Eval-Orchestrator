import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { X } from "lucide-react";
import { del, getJSON, patchJSON, postJSON, putJSON } from "@/lib/api";
import type { GroupRecord, PermissionRecord } from "@/lib/types";
import { Badge, Button, Card, Input } from "@/components/ui";
import { cn } from "@/lib/utils";

type GroupDraft = {
  name: string;
  displayName: string;
  description: string;
  permissions: string[];
};

const emptyDraft: GroupDraft = {
  name: "",
  displayName: "",
  description: "",
  permissions: [],
};

export default function GroupsPage() {
  const qc = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [viewGroup, setViewGroup] = useState<GroupRecord | null>(null);
  const [editGroup, setEditGroup] = useState<GroupRecord | null>(null);
  const [createDraft, setCreateDraft] = useState<GroupDraft>(emptyDraft);
  const [editDraft, setEditDraft] = useState<GroupDraft>(emptyDraft);
  const groups = useQuery({ queryKey: ["groups"], queryFn: () => getJSON<{ groups: GroupRecord[] }>("/api/groups") });
  const permissions = useQuery({
    queryKey: ["permissions"],
    queryFn: () => getJSON<{ permissions: PermissionRecord[] }>("/api/permissions"),
  });

  const create = useMutation({
    mutationFn: async () => {
      const group = await postJSON<GroupRecord>("/api/groups", {
        name: createDraft.name,
        displayName: createDraft.displayName,
        description: createDraft.description,
      });
      if (createDraft.permissions.length === 0) return group;
      return putJSON<GroupRecord>(`/api/groups/${group.groupId}/permissions`, {
        permissions: createDraft.permissions,
      });
    },
    onSuccess: () => {
      setCreateDraft(emptyDraft);
      setCreateOpen(false);
      qc.invalidateQueries({ queryKey: ["groups"] });
    },
    onError: (error) => toast.error((error as Error).message),
  });
  const update = useMutation({
    mutationFn: async () => {
      if (!editGroup) throw new Error("missing group");
      await patchJSON<GroupRecord>(`/api/groups/${editGroup.groupId}`, {
        displayName: editDraft.displayName,
        description: editDraft.description,
      });
      return putJSON<GroupRecord>(`/api/groups/${editGroup.groupId}/permissions`, {
        permissions: editDraft.permissions,
      });
    },
    onSuccess: () => {
      setEditGroup(null);
      setEditDraft(emptyDraft);
      qc.invalidateQueries({ queryKey: ["groups"] });
    },
    onError: (error) => toast.error((error as Error).message),
  });
  const disable = useMutation({
    mutationFn: (id: string) => patchJSON<GroupRecord>(`/api/groups/${id}`, { isActive: false }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["groups"] }),
    onError: (error) => toast.error((error as Error).message),
  });
  const remove = useMutation({
    mutationFn: (id: string) => del(`/api/groups/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["groups"] }),
    onError: (error) => toast.error((error as Error).message),
  });

  function onCreateSubmit(event: FormEvent) {
    event.preventDefault();
    create.mutate();
  }

  function onEditSubmit(event: FormEvent) {
    event.preventDefault();
    update.mutate();
  }

  function openEdit(group: GroupRecord) {
    setEditGroup(group);
    setEditDraft({
      name: group.name,
      displayName: group.displayName,
      description: group.description,
      permissions: group.permissions,
    });
  }

  function togglePermission(draft: GroupDraft, code: string, setDraft: (value: GroupDraft) => void) {
    const permissions = draft.permissions.includes(code)
      ? draft.permissions.filter((item) => item !== code)
      : [...draft.permissions, code];
    setDraft({ ...draft, permissions });
  }

  const permissionList = permissions.data?.permissions ?? [];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-medium tracking-tight">用户组管理</h1>
        <Button onClick={() => setCreateOpen(true)}>创建用户组</Button>
      </div>

      <Card className="overflow-x-auto p-0">
        <table className="w-full min-w-[56rem] text-sm">
          <thead className="border-b border-border text-left text-muted-foreground">
            <tr>
              <th className="px-4 h-11 font-medium whitespace-nowrap">用户组名</th>
              <th className="px-4 h-11 font-medium whitespace-nowrap">用户组展示名称</th>
              <th className="px-4 h-11 font-medium whitespace-nowrap">是否内置</th>
              <th className="px-4 h-11 font-medium whitespace-nowrap">是否启用</th>
              <th className="px-4 h-11 font-medium">描述</th>
              <th className="px-4 h-11 font-medium whitespace-nowrap">操作</th>
            </tr>
          </thead>
          <tbody>
            {groups.data?.groups.map((group) => (
              <tr key={group.groupId} className="border-t border-border">
                <td className="px-4 py-3 font-mono text-xs">{group.name}</td>
                <td className="px-4 py-3 font-medium">{group.displayName}</td>
                <td className="px-4 py-3">
                  {group.isBuiltin ? (
                    <Badge tone="blue">是</Badge>
                  ) : (
                    <span className="text-muted-foreground">否</span>
                  )}
                </td>
                <td className="px-4 py-3">
                  <Badge tone={group.isActive ? "green" : "red"}>{group.isActive ? "是" : "否"}</Badge>
                </td>
                <td className="px-4 py-3 text-muted-foreground">{group.description || "-"}</td>
                <td className="px-4 py-3">
                  <div className="flex flex-wrap gap-2">
                    <Button className="h-8 px-3" variant="ghost" onClick={() => setViewGroup(group)}>
                      查看
                    </Button>
                    <Button className="h-8 px-3" variant="ghost" onClick={() => openEdit(group)}>
                      编辑
                    </Button>
                    <Button
                      className="h-8 px-3"
                      variant="ghost"
                      disabled={group.isBuiltin || !group.isActive}
                      onClick={() => disable.mutate(group.groupId)}
                    >
                      禁用
                    </Button>
                    <Button
                      className="h-8 px-3"
                      variant="danger"
                      disabled={group.isBuiltin || !group.isActive}
                      onClick={() => remove.mutate(group.groupId)}
                    >
                      删除
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
            {groups.data?.groups.length === 0 && (
              <tr>
                <td className="px-4 py-3 text-muted-foreground" colSpan={6}>
                  暂无用户组
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>

      {createOpen && (
        <Dialog title="创建用户组" onClose={() => setCreateOpen(false)}>
          <form className="space-y-4" onSubmit={onCreateSubmit}>
            <div className="grid gap-3 md:grid-cols-3">
              <Input
                value={createDraft.name}
                onChange={(event) => setCreateDraft({ ...createDraft, name: event.target.value })}
                placeholder="用户组标识，例如 reviewer"
              />
              <Input
                value={createDraft.displayName}
                onChange={(event) => setCreateDraft({ ...createDraft, displayName: event.target.value })}
                placeholder="用户组名字"
              />
              <Input
                value={createDraft.description}
                onChange={(event) => setCreateDraft({ ...createDraft, description: event.target.value })}
                placeholder="描述"
              />
            </div>
            <PermissionPicker
              draft={createDraft}
              permissions={permissionList}
              onToggle={(code) => togglePermission(createDraft, code, setCreateDraft)}
            />
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => setCreateOpen(false)}>
                取消
              </Button>
              <Button disabled={!createDraft.name || !createDraft.displayName || create.isPending}>创建用户组</Button>
            </div>
          </form>
        </Dialog>
      )}

      {editGroup && (
        <Dialog title="编辑用户组" onClose={() => setEditGroup(null)}>
          <form className="space-y-4" onSubmit={onEditSubmit}>
            <div className="grid gap-3 md:grid-cols-2">
              <Input
                value={editDraft.displayName}
                onChange={(event) => setEditDraft({ ...editDraft, displayName: event.target.value })}
                placeholder="用户组名字"
              />
              <Input
                value={editDraft.description}
                onChange={(event) => setEditDraft({ ...editDraft, description: event.target.value })}
                placeholder="描述"
              />
            </div>
            <PermissionPicker
              draft={editDraft}
              permissions={permissionList}
              onToggle={(code) => togglePermission(editDraft, code, setEditDraft)}
            />
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => setEditGroup(null)}>
                取消
              </Button>
              <Button disabled={!editDraft.displayName || update.isPending}>保存</Button>
            </div>
          </form>
        </Dialog>
      )}

      {viewGroup && (
        <Dialog title="查看用户组" onClose={() => setViewGroup(null)}>
          <div className="space-y-4">
            <div>
              <div className="text-lg font-medium">{viewGroup.displayName}</div>
              <div className="text-sm text-muted-foreground">{viewGroup.name}</div>
              <div className="mt-2 text-sm text-muted-foreground">{viewGroup.description || "无描述"}</div>
            </div>
            <div className="grid gap-2 md:grid-cols-2">
              {permissionList.map((permission) => (
                <div
                  key={permission.code}
                  className={cn(
                    "border border-border p-2 text-sm",
                    viewGroup.permissions.includes(permission.code) ? "bg-accent/40" : "text-muted-foreground",
                  )}
                >
                  <div className="font-mono text-xs">{permission.code}</div>
                  <div className="text-xs">{permission.description}</div>
                </div>
              ))}
            </div>
            <div className="flex justify-end">
              <Button variant="ghost" onClick={() => setViewGroup(null)}>
                关闭
              </Button>
            </div>
          </div>
        </Dialog>
      )}
    </div>
  );
}

function Dialog({
  children,
  title,
  onClose,
}: {
  children: React.ReactNode;
  title: string;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6">
      <div className="max-h-[85vh] w-full max-w-4xl overflow-y-auto border border-border bg-card text-card-foreground shadow-lg">
        <div className="flex h-12 items-center justify-between border-b border-border px-4">
          <h2 className="text-base font-medium">{title}</h2>
          <button
            className="inline-flex size-8 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
            type="button"
            onClick={onClose}
            aria-label="关闭"
          >
            <X className="size-4" />
          </button>
        </div>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}

function PermissionPicker({
  draft,
  permissions,
  onToggle,
}: {
  draft: GroupDraft;
  permissions: PermissionRecord[];
  onToggle: (code: string) => void;
}) {
  return (
    <div className="grid gap-2 md:grid-cols-2">
      {permissions.map((permission) => (
        <label key={permission.code} className="flex items-start gap-2 border border-border p-2 text-sm">
          <input
            type="checkbox"
            checked={draft.permissions.includes(permission.code)}
            onChange={() => onToggle(permission.code)}
          />
          <span>
            <span className="font-mono text-xs">{permission.code}</span>
            <span className="block text-xs text-muted-foreground">{permission.description}</span>
          </span>
        </label>
      ))}
    </div>
  );
}
