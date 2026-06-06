import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { toast } from "sonner";
import { getJSON, postJSON } from "@/lib/api";
import type { CaseRun, RunDetail } from "@/lib/types";
import { Badge, Button, Card, statusTone } from "@/components/ui";

export default function TaskDetailPage() {
  const { runId } = useParams();
  const detail = useQuery({
    queryKey: ["run", runId],
    queryFn: () => getJSON<RunDetail>(`/api/eval-tasks/${runId}`),
    refetchInterval: 5000,
    enabled: !!runId,
  });
  const cases = useQuery({
    queryKey: ["case-runs", runId],
    queryFn: () => getJSON<{ caseRuns: CaseRun[] }>(`/api/case-runs?runId=${runId}`),
    refetchInterval: 5000,
    enabled: !!runId,
  });

  async function rerunExceptions() {
    try {
      await postJSON(`/api/runs/${runId}/rerun-exceptions`, {});
      toast.success("已创建异常重跑任务");
    } catch (err) {
      toast.error((err as Error).message);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">{detail.data?.displayName ?? runId}</h1>
        <div className="flex gap-2">
          <Button variant="ghost" onClick={rerunExceptions}>重跑异常用例</Button>
        </div>
      </div>

      <Card>
        <h2 className="mb-2 text-sm font-medium text-slate-300">批次</h2>
        <div className="space-y-1 text-sm">
          {detail.data?.batches.map((b) => (
            <div key={b.batchId} className="flex items-center gap-3">
              <Badge tone={statusTone(b.status)}>{b.status}</Badge>
              <span className="text-slate-400">{b.batchId}</span>
              <span className="text-slate-500">@ {b.assignedWorkerId ?? "—"}</span>
              <span className="text-slate-500">{b.selectedCaseIds.length} cases</span>
            </div>
          ))}
          {detail.data?.batches.length === 0 && <span className="text-slate-500">暂无批次</span>}
        </div>
      </Card>

      <Card className="p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-white/[0.04] text-left text-slate-400">
            <tr>
              <th className="px-4 py-2 font-medium">Case</th>
              <th className="px-4 py-2 font-medium">状态</th>
              <th className="px-4 py-2 font-medium">分数</th>
              <th className="px-4 py-2 font-medium">错误</th>
            </tr>
          </thead>
          <tbody>
            {cases.data?.caseRuns.map((c) => (
              <tr key={c.caseRunId} className="border-t border-white/5">
                <td className="px-4 py-2">{c.caseId}</td>
                <td className="px-4 py-2"><Badge tone={statusTone(c.status)}>{c.status}</Badge></td>
                <td className="px-4 py-2 text-slate-300">{c.score ?? "—"}</td>
                <td className="px-4 py-2 text-rose-300/80">{c.errorText ?? ""}</td>
              </tr>
            ))}
            {cases.data?.caseRuns.length === 0 && (
              <tr><td className="px-4 py-3 text-slate-500" colSpan={4}>暂无用例结果</td></tr>
            )}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
