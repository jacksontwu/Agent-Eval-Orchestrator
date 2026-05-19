# 需求分析说明书: Harbor Case 观测面板增强

**功能分支**: `002-harbor-case-insights`  
**创建日期**: 2026-05-18  
**状态**: 草稿  
**输入**: 用户描述: "增强 case 详情面板，补充 Harbor 的 trace、token、迭代、tool 使用和日志预览能力"

---

## 1. 简介

### 1.1 目的

本特性用于增强评测任务页面中的 case 观测能力，让用户在不离开当前页面的前提下，直接看到 Harbor trial 的关键执行信息、轨迹摘要和日志入口，服务于评测分析与后续调优。

### 1.2 范围

包含：

- case 详情数据面扩展
- Harbor `trial/result.json`、`trajectory.json` 与相关日志的摘要展示
- `result/log` 模态框预览
- 页面信息组织优化

不包含：

- 重写完整 Harbor viewer
- 在线编辑/回放 trajectory
- 远端 Harbor 结果未回收时的跨机原位预览

### 1.3 假设和约束

| 类型 | 描述 |
|------|------|
| 假设 | Harbor trial 目录下存在 `result.json`，部分 agent 还会生成 `trajectory.json` |
| 假设 | controller 对本机已回收的 Harbor 产物有只读访问权限 |
| 约束 | 页面必须优先展示摘要信息，避免把大段原始文本直接塞进详情区域 |
| 约束 | `result/log` 预览应通过模态框呈现，而不是在页面内联展开 |

### 1.4 术语与缩写

| 术语/缩写 | 全称 | 说明 |
|-----------|------|------|
| Trial | Harbor Trial | Harbor 对单题的一次实际执行 |
| Trajectory | Agent Trajectory | Harbor/ATIF 风格的 agent 交互轨迹 |
| Tool Summary | Tool Usage Summary | 从 trajectory 中统计的工具使用汇总 |

---

## 2. 系统上下文

### 2.1 系统定位

该特性属于评测任务详情页的信息增强层，定位于“让一次评测 task 下的 case 结果可读、可比较、可用于调优”，而不是替代 Harbor viewer。

### 2.2 系统边界

平台负责：

- 提取 Harbor 原始 trial 数据
- 归一化成 case 级摘要
- 在页面中组织展示

Harbor 保留：

- 完整 trial 结果格式
- trajectory 原始结构
- 原始日志与详细 viewer

### 2.3 目标展示层级

```text
Run / Eval Task
  -> Worker
    -> Case
      -> Summary
      -> Trace/Tool/Token/Timing
      -> Result/Log modal preview
```

---

## 3. 需求分析概述

### 3.1 需求来源

- 当前页面只展示了 case 的最小状态和路径信息
- 用户需要基于 Harbor trace 做调优
- 现有 `result/log` 内联预览会破坏布局

### 3.2 需求目标

1. 在 case 详情中直接补足 Harbor 关键调优数据。
2. 让用户不必先跳 Harbor viewer，就能快速判断 case 表现。
3. 保持页面布局整洁，长文本通过模态框承载。

### 3.3 关键利益相关者

| 角色 | 职责 | 关注点 |
|------|------|--------|
| 评测使用者 | 查看 case 表现并分析问题 | token、trace、tool 使用、日志可读性 |
| 算法/Agent 调优人员 | 对失败 case 做定位与迭代 | iteration、tool summary、异常信息 |
| 平台开发者 | 保持 UI 可维护性 | 展示层和 Harbor 原始数据的边界 |

---

## 4. 需求场景分析

### 4.1 业务场景

**场景 US-001: 快速判断 case 表现**

- 用户打开一个评测 task
- 切换到某个 worker
- 浏览该 worker 的所有 case
- 直接看到 reward、耗时、tokens、trace 是否存在

**场景 US-002: 深入查看失败 case**

- 用户点击某个 case
- 查看错误信息、tool 使用摘要、轨迹 step 数
- 打开 `result` 或 `log` 模态框看原始文本

### 4.2 关键展示字段

每个 case 至少需要补充：

| 类别 | 字段 |
|------|------|
| 基础 | `trial_name`、状态、reward、错误类型、错误文本 |
| 时间 | `started_at`、`finished_at`、总耗时、environment setup、agent setup、agent execution、verifier |
| 资源 | `input_tokens`、`cached_input_tokens`、`output_tokens`、`cost_usd` |
| 轨迹 | `hasTrajectory`、`stepCount`、`toolCallCount`、`toolSummary` |
| 资产 | `resultPath`、`logPath`、`agentDir`、`verifierDir`、`artifactsDir` |
| Harbor 上下文 | `agent_name`、`agent_version`、`model_name`、`provider` |

---

## 5. 功能性需求分析

### 5.1 功能需求列表

| 需求编号 | 需求名称 | 需求描述 | 优先级 |
|----------|----------|----------|--------|
| FR-001 | Case 摘要增强 | 平台必须补充 Harbor case 的关键摘要字段 | P1 |
| FR-002 | Trajectory 统计 | 平台必须从 `trajectory.json` 统计 step 数与 tool 使用 | P1 |
| FR-003 | 模态框预览 | `result` 和 `log` 必须通过模态框展示 | P1 |
| FR-004 | 页面信息重排 | case 卡片需要展示更丰富但仍可扫描的信息 | P1 |

### 5.2 功能需求详细说明

#### FR-001: Case 摘要增强

- 必须显示：reward、状态、trial_name、开始/结束时间、各阶段耗时、token、cost、错误信息、agent/model 上下文。

#### FR-002: Trajectory 统计

- 若存在 `trajectory.json`，必须统计：
  - `stepCount`
  - `toolCallCount`
  - `toolSummary`（按工具名计数）
- 若不存在，页面明确显示“无 trajectory”。

#### FR-003: 模态框预览

- 点击 `预览 result` 和 `预览 log` 时，使用模态框显示文本。
- 模态框需要显示文件路径，并支持关闭。

#### FR-004: 页面信息重排

- case 卡片应首先突出：case 名、状态、reward、duration
- 次级展示：tokens、tool 数、是否有 trace
- 详细表格与长文本应折叠或放在交互后显示

---

## 6. 非功能性需求分析

### 6.1 可读性

- 首屏必须可扫描，不允许默认展开大段原始文本。

### 6.2 性能

- case 摘要应来自预处理结果，不应在每次页面刷新时重新全量解析大文件。

### 6.3 可维护性

- Harbor 原始字段与页面展示字段之间必须通过明确的摘要映射层连接。

---

## 7. 系统影响分析

| 影响对象 | 影响程度 | 说明 |
|----------|----------|------|
| Harbor normalizer | 高 | 需要补充更多 case 字段 |
| 页面静态脚本 | 高 | 需要重组 case 卡片与模态框 |
| controller 接口 | 中 | 现有接口结构可复用，字段会变多 |

## 附录 B：变更历史

| 版本 | 日期 | 作者 | Description |
|------|------|------|-------------|
| 1.0 | 2026-05-18 | Codex | Initial version |
