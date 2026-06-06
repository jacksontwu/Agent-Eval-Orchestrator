import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { del, getJSON, getToken, postJSON } from "@/lib/api";
import type { Worker } from "@/lib/types";
import { Badge, Button, Card, statusTone } from "@/components/ui";

export default function WorkersPage() {
  const qc = useQueryClient();
  const [showDialog, setShowDialog] = useState(false);
  const { data } = useQuery({
    queryKey: ["workers"],
    queryFn: () => getJSON<{ workers: Worker[] }>("/api/workers"),
    refetchInterval: 5000,
  });

  const toggle = useMutation({
    mutationFn: (w: Worker) => postJSON(`/api/workers/${w.workerId}/settings`, { enabled: !w.enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["workers"] }),
    onError: (e) => toast.error((e as Error).message),
  });
  const remove = useMutation({
    mutationFn: (id: string) => del(`/api/workers/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["workers"] }),
    onError: (e) => toast.error((e as Error).message),
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-medium tracking-tight">机器</h1>
        <Button onClick={() => setShowDialog(true)}>添加机器</Button>
      </div>

      <Card className="p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="border-b border-border text-left text-muted-foreground">
            <tr>
              <th className="px-4 h-11 font-medium">名称</th>
              <th className="px-4 h-11 font-medium">状态</th>
              <th className="px-4 h-11 font-medium">槽位</th>
              <th className="px-4 h-11 font-medium">启用</th>
              <th className="px-4 h-11 font-medium"></th>
            </tr>
          </thead>
          <tbody>
            {data?.workers.map((w) => (
              <tr key={w.workerId} className="border-t border-border transition-colors hover:bg-muted/50">
                <td className="px-4 py-2">{w.displayName}<div className="text-xs text-muted-foreground">{w.host}</div></td>
                <td className="px-4 py-2"><Badge tone={statusTone(w.status)}>{w.status}</Badge></td>
                <td className="px-4 py-2 text-muted-foreground">{w.slotsUsed}/{w.slotsTotal}</td>
                <td className="px-4 py-2">
                  <Button variant="ghost" onClick={() => toggle.mutate(w)}>
                    {w.enabled ? "已启用" : "已禁用"}
                  </Button>
                </td>
                <td className="px-4 py-2 text-right">
                  <Button variant="danger" onClick={() => remove.mutate(w.workerId)}>删除</Button>
                </td>
              </tr>
            ))}
            {data?.workers.length === 0 && (
              <tr><td className="px-4 py-3 text-muted-foreground" colSpan={5}>暂无机器</td></tr>
            )}
          </tbody>
        </table>
      </Card>

      {showDialog && <AddMachineDialog onClose={() => setShowDialog(false)} />}
    </div>
  );
}

function AddMachineDialog({ onClose }: { onClose: () => void }) {
  const token = getToken();
  const command = `curl -fsSL "${window.location.origin}/api/workers/enroll.sh?token=${token}" | bash`;
  return (
    <div className="fixed inset-0 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <Card className="max-w-2xl space-y-3 shadow-lg" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-base font-medium">添加机器</h2>
        <p className="text-sm text-muted-foreground">在目标机器上执行下面的命令完成自注册：</p>
        <pre className="overflow-x-auto border border-border bg-muted p-3 text-xs font-mono">{command}</pre>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => { navigator.clipboard.writeText(command); toast.success("已复制"); }}>
            复制命令
          </Button>
          <Button onClick={onClose}>关闭</Button>
        </div>
      </Card>
    </div>
  );
}
