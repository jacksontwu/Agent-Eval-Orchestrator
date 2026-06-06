import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { getJSON } from "@/lib/api";
import type { DashboardTask } from "@/lib/types";
import { Badge, Card, statusTone } from "@/components/ui";

export default function TasksPage() {
  const navigate = useNavigate();
  const { data, isLoading, error } = useQuery({
    queryKey: ["dashboard-tasks"],
    queryFn: () => getJSON<{ tasks: DashboardTask[] }>("/api/dashboard/tasks"),
    refetchInterval: 5000,
  });

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-medium tracking-tight">评测任务</h1>
      {error && <Card className="text-destructive">加载失败：{(error as Error).message}</Card>}
      <Card className="p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="border-b border-border text-left text-muted-foreground">
            <tr>
              <th className="px-4 h-11 font-medium">名称</th>
              <th className="px-4 h-11 font-medium">状态</th>
              <th className="px-4 h-11 font-medium">用例统计</th>
              <th className="px-4 h-11 font-medium">更新时间</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr><td className="px-4 py-3 text-muted-foreground" colSpan={4}>加载中…</td></tr>
            )}
            {data?.tasks.length === 0 && (
              <tr><td className="px-4 py-3 text-muted-foreground" colSpan={4}>暂无任务</td></tr>
            )}
            {data?.tasks.map((task) => (
              <tr
                key={task.runId}
                onClick={() => navigate(`/tasks/${task.runId}`)}
                className="cursor-pointer border-t border-border transition-colors hover:bg-muted/50"
              >
                <td className="px-4 py-2.5">{task.displayName}</td>
                <td className="px-4 py-2.5"><Badge tone={statusTone(task.status)}>{task.status}</Badge></td>
                <td className="px-4 py-2.5 text-muted-foreground">
                  {Object.entries(task.counts).map(([k, v]) => `${k}:${v}`).join("  ") || "—"}
                </td>
                <td className="px-4 py-2.5 text-muted-foreground">{task.updatedAt?.slice(0, 19).replace("T", " ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
