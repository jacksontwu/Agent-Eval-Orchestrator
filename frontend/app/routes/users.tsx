import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { del, getJSON, patchJSON, postJSON } from "@/lib/api";
import type { GroupRecord, UserRecord } from "@/lib/types";
import { Badge, Button, Card, Input } from "@/components/ui";

export default function UsersPage() {
  const qc = useQueryClient();
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [group, setGroup] = useState("user");
  const users = useQuery({ queryKey: ["users"], queryFn: () => getJSON<{ users: UserRecord[] }>("/api/users") });
  const groups = useQuery({ queryKey: ["groups"], queryFn: () => getJSON<{ groups: GroupRecord[] }>("/api/groups") });

  const create = useMutation({
    mutationFn: () => postJSON<UserRecord>("/api/users", { username, displayName, password, groups: [group] }),
    onSuccess: () => {
      setUsername("");
      setDisplayName("");
      setPassword("");
      qc.invalidateQueries({ queryKey: ["users"] });
    },
    onError: (error) => toast.error((error as Error).message),
  });
  const disable = useMutation({
    mutationFn: (id: string) => del(`/api/users/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
    onError: (error) => toast.error((error as Error).message),
  });
  const updateGroups = useMutation({
    mutationFn: ({ id, groups }: { id: string; groups: string[] }) =>
      patchJSON<UserRecord>(`/api/users/${id}`, { groups }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
    onError: (error) => toast.error((error as Error).message),
  });

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    create.mutate();
  }

  const activeGroups = groups.data?.groups.filter((item) => item.isActive) ?? [];

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-medium tracking-tight">用户管理</h1>
      <Card>
        <form className="grid gap-3 md:grid-cols-5" onSubmit={onSubmit}>
          <Input value={username} onChange={(event) => setUsername(event.target.value)} placeholder="用户名" />
          <Input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="显示名" />
          <Input value={password} onChange={(event) => setPassword(event.target.value)} placeholder="初始密码" type="password" />
          <select
            className="h-9 rounded-md border border-input bg-background px-3 text-sm"
            value={group}
            onChange={(event) => setGroup(event.target.value)}
          >
            {activeGroups.map((item) => (
              <option key={item.name} value={item.name}>{item.displayName}</option>
            ))}
          </select>
          <Button disabled={!username || !displayName || !password}>创建用户</Button>
        </form>
      </Card>
      <Card className="p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="border-b border-border text-left text-muted-foreground">
            <tr>
              <th className="px-4 h-11 font-medium">用户</th>
              <th className="px-4 h-11 font-medium">组</th>
              <th className="px-4 h-11 font-medium">状态</th>
              <th className="px-4 h-11 font-medium"></th>
            </tr>
          </thead>
          <tbody>
            {users.data?.users.map((user) => (
              <tr key={user.userId} className="border-t border-border">
                <td className="px-4 py-2">
                  {user.displayName}
                  <div className="text-xs text-muted-foreground">{user.username}</div>
                </td>
                <td className="px-4 py-2">
                  <select
                    className="h-8 rounded-md border border-input bg-background px-2 text-sm"
                    value={user.groups[0] ?? ""}
                    onChange={(event) => updateGroups.mutate({ id: user.userId, groups: [event.target.value] })}
                  >
                    {activeGroups.map((item) => (
                      <option key={item.name} value={item.name}>{item.displayName}</option>
                    ))}
                  </select>
                </td>
                <td className="px-4 py-2">
                  <Badge tone={user.isActive ? "green" : "red"}>{user.isActive ? "启用" : "禁用"}</Badge>
                </td>
                <td className="px-4 py-2 text-right">
                  <Button variant="danger" disabled={!user.isActive} onClick={() => disable.mutate(user.userId)}>禁用</Button>
                </td>
              </tr>
            ))}
            {users.data?.users.length === 0 && (
              <tr><td className="px-4 py-3 text-muted-foreground" colSpan={4}>暂无用户</td></tr>
            )}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
