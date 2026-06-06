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
        <h1 className="text-2xl font-medium tracking-tight">{detail.data?.displayName ?? runId}</h1>
        <div className="flex gap-2">
          <Button variant="ghost" onClick={rerunExceptions}>重跑异常用例</Button>
        </div>
      </div>

      <Card>
        <h2 className="mb-3 text-sm font-medium text-muted-foreground">批次</h2>
        <div className="space-y-1.5 text-sm">
          {detail.data?.batches.map((b) => (
            <div key={b.batchId} className="flex items-center gap-3">
              <Badge tone={statusTone(b.status)}>{b.status}</Badge>
              <span className="text-foreground">{b.batchId}</span>
              <span className="text-muted-foreground">@ {b.assignedWorkerId ?? "—"}</span>
              <span className="text-muted-foreground">{b.selectedCaseIds.length} cases</span>
            </div>
          ))}
          {detail.data?.batches.length === 0 && <span className="text-muted-foreground">暂无批次</span>}
        </div>
      </Card>

      <Card className="p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="border-b border-border text-left text-muted-foreground">
            <tr>
              <th className="px-4 h-11 font-medium">Case</th>
              <th className="px-4 h-11 font-medium">状态</th>
              <th className="px-4 h-11 font-medium">分数</th>
              <th className="px-4 h-11 font-medium">错误</th>
            </tr>
          </thead>
          <tbody>
            {cases.data?.caseRuns.map((c) => (
              <tr key={c.caseRunId} className="border-t border-border transition-colors hover:bg-muted/50">
                <td className="px-4 py-2">{c.caseId}</td>
                <td className="px-4 py-2"><Badge tone={statusTone(c.status)}>{c.status}</Badge></td>
                <td className="px-4 py-2 text-muted-foreground">{c.score ?? "—"}</td>
                <td className="px-4 py-2 text-destructive">{c.errorText ?? ""}</td>
              </tr>
            ))}
            {cases.data?.caseRuns.length === 0 && (
              <tr><td className="px-4 py-3 text-muted-foreground" colSpan={4}>暂无用例结果</td></tr>
            )}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
