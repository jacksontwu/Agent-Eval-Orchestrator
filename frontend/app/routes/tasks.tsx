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
      <h1 className="text-lg font-semibold">评测任务</h1>
      {error && <Card className="text-rose-300">加载失败：{(error as Error).message}</Card>}
      <Card className="p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-white/[0.04] text-left text-slate-400">
            <tr>
              <th className="px-4 py-2 font-medium">名称</th>
              <th className="px-4 py-2 font-medium">状态</th>
              <th className="px-4 py-2 font-medium">用例统计</th>
              <th className="px-4 py-2 font-medium">更新时间</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr><td className="px-4 py-3 text-slate-400" colSpan={4}>加载中…</td></tr>
            )}
            {data?.tasks.length === 0 && (
              <tr><td className="px-4 py-3 text-slate-400" colSpan={4}>暂无任务</td></tr>
            )}
            {data?.tasks.map((task) => (
              <tr
                key={task.runId}
                onClick={() => navigate(`/tasks/${task.runId}`)}
                className="cursor-pointer border-t border-white/5 hover:bg-white/[0.03]"
              >
                <td className="px-4 py-2.5">{task.displayName}</td>
                <td className="px-4 py-2.5"><Badge tone={statusTone(task.status)}>{task.status}</Badge></td>
                <td className="px-4 py-2.5 text-slate-300">
                  {Object.entries(task.counts).map(([k, v]) => `${k}:${v}`).join("  ") || "—"}
                </td>
                <td className="px-4 py-2.5 text-slate-400">{task.updatedAt?.slice(0, 19).replace("T", " ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
