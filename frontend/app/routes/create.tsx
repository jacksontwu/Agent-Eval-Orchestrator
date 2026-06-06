import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { getJSON, postJSON } from "@/lib/api";
import type { DatasetInfo } from "@/lib/types";
import { Button, Card, Input } from "@/components/ui";

export default function CreatePage() {
  const navigate = useNavigate();
  const { data } = useQuery({
    queryKey: ["datasets"],
    queryFn: () => getJSON<{ datasets: DatasetInfo[] }>("/api/datasets"),
  });

  const [name, setName] = useState("");
  const [datasetPath, setDatasetPath] = useState("");
  const [bitfunCliPath, setBitfunCliPath] = useState("");
  const [bitfunConfigDir, setBitfunConfigDir] = useState("");
  const [selectedCaseIds, setSelectedCaseIds] = useState("");
  const [perWorkerConcurrency, setConcurrency] = useState(1);
  const [submitting, setSubmitting] = useState(false);

  async function submit() {
    setSubmitting(true);
    try {
      const payload = {
        name,
        datasetPath,
        bitfunCliPath,
        bitfunConfigDir,
        selectedCaseIds: selectedCaseIds.split(/[\s,]+/).filter(Boolean),
        perWorkerConcurrency,
      };
      const res = await postJSON<{ runId: string }>("/api/eval-tasks/create-and-distribute", payload);
      toast.success("任务已创建并分发");
      navigate(`/tasks/${res.runId}`);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="max-w-2xl space-y-4">
      <h1 className="text-2xl font-medium tracking-tight">新建评测任务</h1>
      <Card className="space-y-4">
        <Field label="任务名称">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="my-eval" />
        </Field>
        <Field label="数据集路径 (controller 上的绝对路径)">
          <Input value={datasetPath} onChange={(e) => setDatasetPath(e.target.value)} placeholder="/root/.../datasets/terminal-bench-2" />
          {data && (
            <div className="mt-1 flex flex-wrap gap-2 text-xs text-muted-foreground">
              {data.datasets.map((d) => (
                <button
                  key={d.datasetRef}
                  disabled={!d.available}
                  onClick={() => setDatasetPath(d.path)}
                  className="rounded-md border border-border px-2 py-0.5 transition-colors hover:bg-accent hover:text-accent-foreground disabled:opacity-40"
                >
                  {d.datasetRef} {d.available ? "" : "(缺失)"}
                </button>
              ))}
            </div>
          )}
        </Field>
        <Field label="bitfun CLI 路径">
          <Input value={bitfunCliPath} onChange={(e) => setBitfunCliPath(e.target.value)} />
        </Field>
        <Field label="bitfun 配置目录">
          <Input value={bitfunConfigDir} onChange={(e) => setBitfunConfigDir(e.target.value)} />
        </Field>
        <Field label="选中的 case id (留空=全部，空格/逗号分隔)">
          <Input value={selectedCaseIds} onChange={(e) => setSelectedCaseIds(e.target.value)} />
        </Field>
        <Field label="每机并发">
          <Input
            type="number"
            value={perWorkerConcurrency}
            onChange={(e) => setConcurrency(Number(e.target.value) || 1)}
          />
        </Field>
        <Button onClick={submit} disabled={submitting || !name || !datasetPath}>
          {submitting ? "创建中…" : "创建并分发"}
        </Button>
      </Card>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}
