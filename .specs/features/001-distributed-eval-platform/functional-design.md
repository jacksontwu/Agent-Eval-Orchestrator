# 功能设计说明书: 分布式评测平台一期设计

**功能分支**: `001-distributed-eval-platform`  
**创建日期**: 2026-05-17  
**状态**: 草稿  
**关联需求文档**: [requirements-analysis.md](/root/projects/agent-eval-orchestrator/.specs/features/001-distributed-eval-platform/requirements-analysis.md)

---

## 1. 功能概述

### 1.1 功能简介

本设计定义一个与 Harbor 解耦的分布式评测平台一期实现。平台以 `controller + fixed workers` 为主结构，以 Harbor `docker` 作为首个执行器，负责任务编排、worker 调度、Harbor 命令执行、结果回收、归一化和批次归档。

### 1.2 设计目标

| 目标类型 | 目标描述 | 衡量标准 |
|----------|----------|----------|
| 功能目标 | 支持固定 worker 集群执行 Harbor 评测任务 | Batch 可被成功调度和执行 |
| 功能目标 | 平台核心与 Harbor 解耦 | 核心模型中无 Harbor 主状态字段 |
| 性能目标 | 在 2-10 台固定 worker 范围内稳定并发 | worker slots 生效，无重复领取 |
| 质量目标 | 结果可追溯、可重跑、不可覆盖 | 每次执行生成独立 Batch 快照 |

### 1.3 设计原则

| 原则 | 描述 | 应用场景 |
|------|------|----------|
| 平台内核稳定 | 核心模型不被 Harbor 或云厂商语义侵入 | 模型设计、数据库设计 |
| 执行器可替换 | Harbor 只是 `Executor` 插件之一 | 新增执行器、未来演进 |
| 原始结果保真 | Harbor 原始结果与平台统一结果双轨保存 | 排障、回放、审计 |

---

## 2. 实现思路

### 2.1 整体方案

整体采用三层：

1. **控制平面**：controller API、调度器、状态仓储、结果归档索引。
2. **执行平面**：worker 守护进程、执行器插件、结果回收器。
3. **存储平面**：共享归档目录与本地 worker 临时目录。

一期执行链路：

```text
Batch queued
  -> worker claim
  -> HarborExecutor.prepare()
  -> HarborExecutor.start()
  -> Harbor 原始 jobs 结果生成
  -> HarborExecutor.collect()
  -> HarborResultNormalizer.normalize()
  -> Batch/CaseRun snapshot 持久化
```

### 2.2 技术架构

```text
UI / CLI
  -> Controller API
      -> Auth Scope / Template Service / Run Service
      -> Scheduler
      -> Worker Registry
      -> Batch Snapshot Service
      -> Executor Registry

Workers
  -> Worker Daemon
      -> Claim Loop
      -> HarborExecutor
      -> Result Collector
      -> Heartbeat Reporter

Storage
  -> controller state db
  -> shared archive root
  -> worker local tmp
```

### 2.3 核心流程

```text
用户创建模板
  -> 创建 Run
  -> 发起 Batch
  -> controller 校验并入队
  -> worker 领取 Batch
  -> HarborExecutor 构造并执行 harbor run
  -> worker 回收 Harbor jobs 目录
  -> 解析 Harbor result.json + trial results
  -> 写入统一 summary/cases
  -> controller 更新 Batch 完成状态
```

### 2.4 关键技术点

| 技术点 | 描述 | 解决方案 |
|--------|------|----------|
| 执行器边界 | Harbor 结果与平台结果模型不一致 | 定义统一 `Executor` 接口和 `ResultNormalizer` |
| 结果归一化 | Harbor 的 job/trial 输出需要转换成 Batch/CaseRun | 为 Harbor 单独实现 normalizer 和 schema version |
| 调度一致性 | 多 worker 并发 claim 时防止重复领取 | 数据库事务更新 Batch 状态 |
| 目录治理 | 同时保存 Harbor 原始结果和平台统一快照 | `batchRoot/harbor` 与 `batchRoot/normalized` 双目录布局 |

### 2.5 技术选型

| 技术领域 | 选型方案 | 选型理由 | 备选方案 |
|----------|----------|----------|----------|
| 控制器语言 | Python | 与现有 controller-worker 资产接近，便于快速落地 | Go |
| 持久化 | SQLite + repository 抽象 | 一期部署简单，便于本地运行 | PostgreSQL |
| 执行器 | Harbor CLI + Docker | 复用 Harbor benchmark/agent 生态 | 直接内嵌 Harbor Python API |
| 共享存储 | 本地/NFS 共享目录 | 简单、透明、可私有化 | 对象存储 |

---

## 3. 实现设计

### 3.1 模块设计

#### 3.1.1 模块划分

```text
src/
  controller/
  worker/
  core/
  executors/
    base.py
    harbor_executor.py
  normalizers/
    harbor_normalizer.py
  storage/
  api/
```

#### 3.1.2 模块职责

| 模块名称 | 职责描述 | 关键类/组件 | 依赖模块 |
|----------|----------|-------------|----------|
| `controller` | API、调度、状态更新 | `RunService`, `BatchService`, `Scheduler` | `storage`, `core` |
| `worker` | claim、执行、回收、上报 | `WorkerDaemon`, `BatchRunner` | `executors`, `normalizers` |
| `executors` | 屏蔽具体执行后端差异 | `Executor`, `HarborExecutor` | `core` |
| `normalizers` | 把原始结果转换成统一结果 | `HarborResultNormalizer` | `storage` |
| `storage` | 目录布局、快照写入、索引查询 | `Layout`, `SnapshotWriter` | `core` |

### 3.2 类设计

#### 3.2.1 核心类图

```text
TaskTemplate -> Run -> Batch -> CaseRun
WorkerDaemon -> Executor
HarborExecutor -> HarborResultNormalizer
Scheduler -> BatchRepository
```

#### 3.2.2 类详细设计

**类名: `Executor`**

| 项目 | 描述 |
|------|------|
| 所属模块 | `executors.base` |
| 类描述 | 定义执行器统一协议 |
| 设计模式 | Strategy |

| 方法名 | 参数 | 返回值 | 描述 |
|--------|------|--------|------|
| `prepare` | `batchContext` | `PreparedBatch` | 预处理数据集和目录 |
| `start` | `preparedBatch` | `RunningHandle` | 启动执行 |
| `poll` | `runningHandle` | `ExecutionState` | 查询执行状态 |
| `collect` | `runningHandle` | `CollectedArtifacts` | 回收结果和日志 |
| `stop` | `runningHandle` | `None` | 停止执行 |

**类名: `HarborExecutor`**

| 项目 | 描述 |
|------|------|
| 所属模块 | `executors.harbor_executor` |
| 类描述 | 构造 Harbor CLI 命令并管理 Harbor 结果目录 |
| 设计模式 | Strategy 实现 |

### 3.3 流程设计

#### 3.3.1 主流程

```text
worker claim batch
  -> resolve executor
  -> prepare batchRoot/localRoot
  -> build harbor run command
  -> spawn subprocess
  -> watch process and heartbeat
  -> collect harbor jobs dir
  -> normalize results
  -> persist snapshots
```

#### 3.3.2 异常流程

| 异常场景 | 处理流程 | 错误码 | 错误信息 |
|----------|----------|--------|----------|
| Harbor CLI 缺失 | 启动前预检失败 -> Batch failed | `EXECUTOR_PRECHECK_FAILED` | Harbor CLI not available |
| Harbor result 缺失 | 记录缺失状态 -> 保留原始日志 -> Batch failed | `RESULT_COLLECTION_FAILED` | Harbor result file missing |
| worker 中断 | 保留最后状态 -> 标记 worker unavailable | `WORKER_LOST` | Worker heartbeat timeout |

### 3.4 状态设计

#### 3.4.1 状态机

```text
Batch: queued -> running -> succeeded|failed|stopped
CaseRun: pending -> running -> succeeded|failed|skipped
```

#### 3.4.2 状态说明

| 状态名称 | 状态描述 | 进入条件 | 退出条件 | 允许转换 |
|----------|----------|----------|----------|----------|
| queued | 等待 worker 领取 | Batch 创建成功 | 被 worker claim | running, stopped |
| running | 执行中 | worker 已启动执行器 | 执行结束或停止 | succeeded, failed, stopped |
| succeeded | 执行成功并归档完成 | normalizer 完成 | 无 | 无 |
| failed | 执行失败或结果回收失败 | 任一步骤失败 | 无 | 无 |

---

## 4. 接口设计

### 4.1 调用接口

#### 4.1.1 外部依赖接口

| 接口名称 | 提供方 | 接口描述 | 调用场景 |
|----------|--------|----------|----------|
| Harbor CLI | Harbor | 执行 `harbor run` | worker 启动 Harbor Batch |
| Docker CLI | Docker | Harbor `docker` 后端依赖 | worker 本机执行 |

#### 4.1.2 接口详细定义

**接口: `harbor run`**

| 项目 | 描述 |
|------|------|
| 接口名称 | Harbor CLI |
| 接口地址 | 本地命令行 |
| 请求方式 | 子进程调用 |
| 接口描述 | 在 worker 本机执行 Harbor job |

调用示例：

```bash
uv run harbor run \
  -p /data/datasets/swebench-verified \
  -a codex \
  -m openai/gpt-5 \
  -e docker \
  -n 4 \
  --jobs-dir /shared/batches/batch-001/harbor/jobs \
  --job-name batch-001
```

### 4.2 提供接口

平台对外接口见 [contracts/controller-api.md](/root/projects/agent-eval-orchestrator/.specs/features/001-distributed-eval-platform/contracts/controller-api.md)。

### 4.3 子系统/模块间接口基线

#### 4.3.1 模块间接口关系图

```text
Controller -> BatchRepository
Controller -> WorkerRegistry
WorkerDaemon -> Controller API
WorkerDaemon -> Executor
Executor -> Normalizer
Normalizer -> SnapshotWriter
```

---

## 5. 周边依赖关系

### 5.1 网络依赖

| 依赖 | 说明 | 异常处理 |
|------|------|----------|
| controller -> worker | 仅通过 worker 主动请求 controller API 协作 | worker 重试注册和心跳 |
| worker -> Harbor dataset path | 本地或共享路径访问 | 预检失败直接报错 |

### 5.2 UX 依赖

一期可仅提供 API 和轻量管理界面，前端展示重点是：

- Batch 队列状态
- CaseRun 结果表
- Harbor 原始产物入口
- 错误文本与日志入口

### 5.3 外部 SDK 依赖

| 依赖 | 用途 | 兼容性说明 |
|------|------|------------|
| Harbor | 执行评测 | 通过适配层封装版本差异 |
| Docker | 容器执行 | 仅要求 worker 本机可用 |

---

## 6. 安全隐私设计

### 6.1 安全架构

```text
user scope
  -> controller auth scope
  -> batch artifact namespace
  -> worker limited write paths
```

### 6.2 安全机制设计

- 用户资源按 owner 隔离。
- worker 不直接写数据库，只通过 controller API 上报。
- 执行器凭据不写入公开快照，只出现在受控环境变量中。

### 6.3 隐私保护设计

- 原始 Harbor 结果若包含敏感环境变量回显，回收时需过滤索引展示内容。
- 错误文本展示默认截断，原始日志通过受控下载接口访问。

### 6.4 安全审计

- 记录 Batch 发起人、实际执行 worker、执行器种类、开始/结束时间。
- 关键状态转换写入审计日志。

---

## 7. 性能功耗设计

### 7.1 性能指标

| 指标 | 目标 |
|------|------|
| claim 周期 | 10s 级别内完成调度 |
| Batch 汇总读取 | 常见场景下 < 1s 返回汇总 JSON |
| 归档开销 | 原始结果复制和归一化不阻塞下一批次领取超过可接受阈值 |

### 7.2 性能优化设计

- 共享目录按 `batchRoot` 分层，避免全目录扫描。
- 汇总信息单独缓存为 `summary.json`。
- Harbor 原始大目录只做索引，不在主查询链路全量展开。

### 7.3 功耗设计

一期以固定 worker 为主，不单独做功耗调优。后续动态 worker 扩展时通过机器生命周期治理优化资源占用。

---

## 8. 本地数据库设计

### 8.1 数据库概述

一期使用 SQLite，存放于 controller 私有目录：

```text
controller/state.sqlite3
```

### 8.2 表结构设计

建议核心表：

- `task_templates`
- `runs`
- `batches`
- `case_runs`
- `workers`
- `audit_events`

说明：

- 执行器扩展字段使用 `executor_metadata_json`
- 汇总字段使用 `summary_json`

### 8.3 数据迁移设计

- 为 Batch 和 CaseRun 定义结果 schema version。
- 当 normalizer 升级时，只新增迁移器，不重写旧快照。

---

## 9. DFX分析

### 9.1 可靠性

- worker 与 controller 分离，worker 失联不会损坏历史批次。
- 批次归档采用先写临时文件后原子重命名。

### 9.2 安全性

- 共享目录写入边界清晰，worker 只写自己被分配的批次目录。
- 执行器命令参数和环境变量来源可审计。

### 9.3 模块隔离设计

- 执行器和 normalizer 各自通过接口接入。
- 云动态 worker 通过 `WorkerProvisioner` 扩展，不侵入 Batch 核心模型。

### 9.4 可配置性

可配置项：

- worker slots
- claim interval
- executor defaults
- archive root
- result retention policy

### 9.5 兼容性

- Harbor 版本兼容通过 `HarborExecutor` 适配。
- 平台结果兼容通过 schema version 控制。

### 9.6 可测试性

- Executor 命令构造单元测试
- Harbor 结果样本回放测试
- claim 与状态流转集成测试

---

## 10. 其它功能性设计

### 10.1 全球化设计

一期仅面向中文内部团队，文档和界面文案优先中文。后续如需国际化，优先从前端文案和 API 错误码开始抽象。

### 10.2 扩展性设计

预留扩展点：

- `ExecutorRegistry`
- `ResultNormalizerRegistry`
- `WorkerProvisioner`
- `DatasetResolver`

---

## 附录 A：宪法一致性检查

- 已遵守“平台内核与执行器解耦”
- 已遵守“固定 worker 为一期默认能力”
- 已为未来动态 worker 提供扩展接口而非硬编码 Harbor Environment

## 附录 B：输出文件

- [research.md](/root/projects/agent-eval-orchestrator/.specs/features/001-distributed-eval-platform/research.md)
- [data-model.md](/root/projects/agent-eval-orchestrator/.specs/features/001-distributed-eval-platform/data-model.md)
- [quickstart.md](/root/projects/agent-eval-orchestrator/.specs/features/001-distributed-eval-platform/quickstart.md)
- [contracts/](/root/projects/agent-eval-orchestrator/.specs/features/001-distributed-eval-platform/contracts)

## 附录 C：变更历史

| 版本 | 日期 | 作者 | Description |
|------|------|------|-------------|
| 1.0 | 2026-05-17 | Codex | Initial version |
