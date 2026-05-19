# 技术研究: 分布式评测平台一期设计

## 研究结论

### 决策 1：平台核心与 Harbor 解耦

- Decision: 平台核心只建模 `TaskTemplate/Run/Batch/CaseRun`，Harbor 通过 `HarborExecutor` 接入。
- Rationale: 这样可以避免未来支持自定义执行器、CodeBench、动态 worker 时反复推翻核心模型。
- Alternatives considered:
  - 直接把 Harbor `Job/Trial` 暴露为核心模型
  - 在现有 `stage1/stage2` 模型上硬映射 Harbor

### 决策 2：一期采用固定 worker + Harbor `docker`

- Decision: 一期只支持固定 worker 集群，worker 本机调用 Harbor CLI 并使用本机 Docker。
- Rationale: 该方案最稳定，镜像缓存收益明显，也最利于排障和结果复现。
- Alternatives considered:
  - 直接实现 Harbor 华为云 Environment
  - 让 controller 远程 SSH 到各节点执行 Harbor

### 决策 3：云弹性通过动态 worker 生命周期扩展

- Decision: 二期优先通过 `WorkerProvisioner` 动态创建/销毁云 worker，而不是先侵入 Harbor Environment。
- Rationale: worker 协议复用率高，且不会把平台生命周期和某个云 API 强耦合。
- Alternatives considered:
  - 为每个云厂商直接写 Harbor Environment
  - 在 controller 内直接编排 Harbor trial 到云 API

### 决策 4：结果采用“原始结果 + 统一结果”双轨保存

- Decision: Harbor 原始 `jobs/` 目录和平台统一结果同时保存。
- Rationale: 平台层展示需要稳定结构，排障和回放又需要 Harbor 原始结果。
- Alternatives considered:
  - 只保存 Harbor 原始结果
  - 只保存平台归一化结果
