# Worker Protocol Contract

## Claim 返回结构

```json
{
  "batch": {
    "batchId": "batch-001",
    "runId": "run-001",
    "executorKind": "harbor-docker",
    "executorConfig": {
      "agentName": "codex",
      "modelName": "openai/gpt-5"
    },
    "datasetRef": "datasets/swebench-verified",
    "selectedCaseIds": ["django__django-10097"],
    "batchRoot": "/shared/users/alice/runs/run-001/batches/batch-001"
  }
}
```

## Worker 上报要求

- worker 必须定期上报批次状态。
- 当执行器结束时，worker 必须附带：
  - `status`
  - `finished`
  - `errorText`
  - `executorMetadata`
  - `resultIndex`

## 执行器边界

- worker 负责启动执行器和回收结果。
- controller 不直接读取 worker 私有临时目录。
- 所有回传结果必须写入批次归档目录后再上报索引。
