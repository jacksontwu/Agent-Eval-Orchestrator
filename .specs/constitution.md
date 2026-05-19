<!--
Sync Impact Report
- Version change: 1.0.0 -> 1.0.0
- Modified principles: initial adoption
- Added sections: none
- Removed sections: none
- Templates requiring updates:
  - ✅ /root/projects/agent-eval-orchestrator/.specs/templates/requirements-analysis-template.md
  - ✅ /root/projects/agent-eval-orchestrator/.specs/templates/functional-design-template.md
  - ✅ /root/projects/agent-eval-orchestrator/.specs/templates/tasks-template.md
- Follow-up TODOs:
  - 无
-->

# 项目宪法

**项目名称**: Agent Eval Orchestrator  
**版本**: 1.0.0  
**Ratification Date**: 2026-05-17  
**Last Amended Date**: 2026-05-17

## 1. 平台定位

本项目是分布式评测平台，而不是某个具体评测框架的包装层。平台负责任务模型、调度、权限、归档、观测与运维约束；具体评测动作由执行器承担。

## 2. 核心原则

### 原则一：平台内核必须与执行器解耦

平台核心数据模型、API、调度逻辑、前端视图不得直接绑定 Harbor、CodeBench 或其他单一框架的专有语义。执行器相关字段必须封装在 `executorKind` 和 `executorMetadata` 之下，核心模型只暴露平台统一语义。

### 原则二：任务模型必须以 Run/Batch/Case 为一等公民

平台必须使用统一的逻辑对象描述评测执行：

- `TaskTemplate`：逻辑任务模板
- `Run`：一个可多次触发的运行实体
- `Batch`：一次实际执行批次
- `CaseRun`：批次内单题执行记录

不得要求所有执行器都适配 `stage1/stage2` 这类特定框架语义。

### 原则三：控制平面与执行平面必须分离

`controller` 负责 API、调度、用户与资源管理、状态持久化；`worker` 负责领取任务、执行评测、上报结果。任何执行器不得绕过 controller 直接改写平台状态库。

### 原则四：结果归档必须可回放、可追责、不可覆盖

每次 `Batch` 完成后必须固化：

- 运行配置快照
- 题目级执行结果
- 关键日志与产物索引
- 汇总指标

后续重跑不得覆盖旧批次快照，只能产生新的批次记录。

### 原则五：固定 worker 集群是一期开箱能力

项目的一期交付必须优先支持固定几台机器组成的 worker 池，并稳定运行 Harbor `docker` 执行器。弹性云 worker、自定义 Harbor Environment 属于后续扩展，不得阻塞一期架构收敛。

### 原则六：云弹性扩容通过 worker 生命周期实现，而非侵入平台内核

未来华为云、腾讯云、Kubernetes、E2B 等弹性能力，应优先通过“动态创建/回收 worker”接入，而不是把平台核心绑定到某个云厂商 API。只有当执行器自身必须掌控 sandbox 生命周期时，才允许新增环境后端。

### 原则七：可观测性和恢复能力为默认要求

每个运行对象必须有明确状态机、超时策略、错误文本、重试记录和执行主机记录。worker 或 controller 重启后，系统必须能从持久化状态恢复可观测性，而不是依赖内存状态猜测。

## 3. 技术决策约束

- 核心平台首选 Python 实现，便于复用现有 controller-worker 资产。
- 数据持久化一期允许使用 SQLite，但接口和仓储层必须为后续切换 PostgreSQL 预留边界。
- 共享产物目录必须保留可本地部署能力，不得强制依赖单一云存储服务。
- Harbor 作为一期推荐执行器，但必须以插件方式接入。

## 4. 交付约束

- 所有说明性文档使用中文。
- 新功能先产出 `.specs` 文档，再进入实现。
- 任何影响长期方向的变更，先更新本宪法，再更新功能设计。

## 5. 治理

- 宪法变更使用语义化版本号：
  - MAJOR：原则删除、原则含义逆转、架构边界重定义
  - MINOR：新增原则、新增治理章节、约束显著扩展
  - PATCH：措辞澄清、示例完善、无语义变化的修订
- 每次设计评审必须包含宪法一致性检查。
- 若实现与宪法冲突，必须先提交宪法修订并获得评审确认。
