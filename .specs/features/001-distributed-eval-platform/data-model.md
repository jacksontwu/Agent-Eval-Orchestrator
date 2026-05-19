# 数据模型: 分布式评测平台一期设计

## 核心实体

### TaskTemplate

| 字段 | 类型 | 说明 |
|------|------|------|
| templateId | string | 模板主键 |
| owner | string | 所属用户 |
| name | string | 模板名称 |
| datasetRef | string | 数据集引用 |
| executorKind | string | 执行器类型，如 `harbor-docker` |
| executorConfig | json | 执行器配置快照 |
| modelProfileRef | string | 模型配置引用 |
| note | string | 备注 |

### Run

| 字段 | 类型 | 说明 |
|------|------|------|
| runId | string | Run 主键 |
| templateId | string | 模板引用 |
| displayName | string | 运行名称 |
| boundWorkerId | string? | 固定绑定的 worker |
| latestBatchId | string? | 最近一次执行批次 |

### Batch

| 字段 | 类型 | 说明 |
|------|------|------|
| batchId | string | 批次主键 |
| runId | string | Run 引用 |
| status | enum | `queued/running/succeeded/failed/stopped` |
| preferredWorkerId | string? | 首选 worker |
| assignedWorkerId | string? | 实际执行 worker |
| executorKind | string | 执行器类型 |
| executorMetadata | json | 执行器原始元数据 |
| summary | json | 统一汇总结果 |
| batchRoot | string | 批次归档目录 |
| createdAt | datetime | 创建时间 |
| startedAt | datetime? | 开始时间 |
| finishedAt | datetime? | 结束时间 |
| errorText | string? | 错误文本 |

### CaseRun

| 字段 | 类型 | 说明 |
|------|------|------|
| caseRunId | string | 单题记录主键 |
| batchId | string | 批次引用 |
| caseId | string | 题目/实例标识 |
| status | enum | `pending/running/succeeded/failed/skipped` |
| score | number? | 统一分值 |
| metrics | json | 统一指标 |
| artifactIndex | json | 原始产物索引 |
| errorText | string? | 错误文本 |

### Worker

| 字段 | 类型 | 说明 |
|------|------|------|
| workerId | string | worker 主键 |
| displayName | string | 显示名称 |
| host | string | 主机地址 |
| slotsTotal | int | 总槽位 |
| slotsUsed | int | 已用槽位 |
| capabilities | json | 能力描述 |
| status | enum | `online/unavailable/removed` |
| lastHeartbeatAt | datetime? | 最后心跳时间 |

## 关系

```text
TaskTemplate 1 -> N Run
Run 1 -> N Batch
Batch 1 -> N CaseRun
Worker 1 -> N Batch
```

## 状态迁移

### Batch

```text
queued -> running -> succeeded
queued -> running -> failed
queued -> stopped
running -> stopped
```

### CaseRun

```text
pending -> running -> succeeded
pending -> running -> failed
pending -> skipped
```
