import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { del, getJSON, postJSON, putJSON } from "@/lib/api";
import type { GroupRecord, PermissionRecord } from "@/lib/types";
import { Badge, Button, Card, Input } from "@/components/ui";

export default function GroupsPage() {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
  const groups = useQuery({ queryKey: ["groups"], queryFn: () => getJSON<{ groups: GroupRecord[] }>("/api/groups") });
  const permissions = useQuery({
    queryKey: ["permissions"],
    queryFn: () => getJSON<{ permissions: PermissionRecord[] }>("/api/permissions"),
  });

  const create = useMutation({
    mutationFn: () => postJSON<GroupRecord>("/api/groups", { name, displayName, description }),
    onSuccess: () => {
      setName("");
      setDisplayName("");
      setDescription("");
      qc.invalidateQueries({ queryKey: ["groups"] });
    },
    onError: (error) => toast.error((error as Error).message),
  });
  const disable = useMutation({
    mutationFn: (id: string) => del(`/api/groups/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["groups"] }),
    onError: (error) => toast.error((error as Error).message),
  });
  const setPermissions = useMutation({
    mutationFn: ({ id, permissions }: { id: string; permissions: string[] }) =>
      putJSON<GroupRecord>(`/api/groups/${id}/permissions`, { permissions }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["groups"] }),
    onError: (error) => toast.error((error as Error).message),
  });

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    create.mutate();
  }

  function togglePermission(group: GroupRecord, code: string) {
    const next = group.permissions.includes(code)
      ? group.permissions.filter((item) => item !== code)
      : [...group.permissions, code];
    setPermissions.mutate({ id: group.groupId, permissions: next });
  }

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-medium tracking-tight">组管理</h1>
      <Card>
        <form className="grid gap-3 md:grid-cols-4" onSubmit={onSubmit}>
          <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="组标识，例如 reviewer" />
          <Input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="显示名" />
          <Input value={description} onChange={(event) => setDescription(event.target.value)} placeholder="描述" />
          <Button disabled={!name || !displayName}>创建组</Button>
        </form>
      </Card>
      <div className="space-y-3">
        {groups.data?.groups.map((group) => (
          <Card key={group.groupId} className="space-y-3">
            <div className="flex items-center gap-3">
              <div>
                <div className="font-medium">{group.displayName}</div>
                <div className="text-xs text-muted-foreground">{group.name}</div>
              </div>
              {group.isBuiltin && <Badge tone="blue">内置</Badge>}
              {!group.isActive && <Badge tone="red">禁用</Badge>}
              <div className="ml-auto">
                <Button variant="danger" disabled={group.isBuiltin || !group.isActive} onClick={() => disable.mutate(group.groupId)}>
                  禁用
                </Button>
              </div>
            </div>
            <div className="grid gap-2 md:grid-cols-2">
              {permissions.data?.permissions.map((permission) => (
                <label key={permission.code} className="flex items-start gap-2 border border-border p-2 text-sm">
                  <input
                    type="checkbox"
                    checked={group.permissions.includes(permission.code)}
                    onChange={() => togglePermission(group, permission.code)}
                  />
                  <span>
                    <span className="font-mono text-xs">{permission.code}</span>
                    <span className="block text-xs text-muted-foreground">{permission.description}</span>
                  </span>
                </label>
              ))}
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
