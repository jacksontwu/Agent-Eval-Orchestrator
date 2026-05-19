# Controller API Contract

## 1. 任务模板

### `POST /api/task-templates`

请求体：

```json
{
  "owner": "alice",
  "name": "swebench-harbor-gpt5",
  "datasetRef": "datasets/swebench-verified",
  "executorKind": "harbor-docker",
  "executorConfig": {
    "agentName": "codex",
    "modelName": "openai/gpt-5",
    "jobsDirStrategy": "batch-root"
  },
  "modelProfileRef": "model-001",
  "note": ""
}
```

响应体：

```json
{
  "templateId": "tpl-001",
  "status": "created"
}
```

## 2. 运行与批次

### `POST /api/runs`

创建 Run。

### `POST /api/runs/{runId}/batches`

请求体：

```json
{
  "selectedCaseIds": ["django__django-10097"],
  "preferredWorkerId": "worker-a",
  "batchOptions": {
    "concurrency": 4
  }
}
```

响应体：

```json
{
  "batchId": "batch-001",
  "status": "queued"
}
```

## 3. Worker 协议

### `POST /api/workers/register`

worker 上报身份、主机、slots 和 capabilities。

### `POST /api/workers/claim`

worker 领取一个待执行 Batch。

### `POST /api/workers/heartbeat`

worker 上报：

```json
{
  "workerId": "worker-a",
  "batchId": "batch-001",
  "status": "running",
  "currentStep": "executor-running",
  "finished": false,
  "errorText": null
}
```

## 4. 查询接口

### `GET /api/runs/{runId}`

返回 Run 基本信息、最近批次和历史批次索引。

### `GET /api/batches/{batchId}`

返回：

- Batch 汇总信息
- CaseRun 列表
- 日志与原始产物索引
