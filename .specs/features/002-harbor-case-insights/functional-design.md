# 功能设计说明书: Harbor Case 观测面板增强

**功能分支**: `002-harbor-case-insights`  
**创建日期**: 2026-05-18  
**状态**: 草稿  
**关联需求文档**: [requirements-analysis.md](/root/projects/agent-eval-orchestrator/.specs/features/002-harbor-case-insights/requirements-analysis.md)

---

## 1. 功能概述

该特性增强评测 task 页面中的 case 观测能力，让每个 case 除了状态和路径，还能展示 Harbor trial 的关键调优信息，并通过模态框承载原始 `result/log` 文本。

## 2. 实现思路

### 2.1 数据来源

- `trial/result.json`
  - reward、异常、时间、agent/model 信息、token/cost
- `agent/trajectory.json`
  - step 数、tool call、tool summary
- `trial.log`、`result.json`
  - 原始文本预览

### 2.2 技术方案

1. 扩展 Harbor normalizer：
   - 在 case 归一化结构中增加 timing、token、cost、trajectory summary、agent/model fields
2. 扩展页面：
   - case 卡片展示摘要字段
   - `result/log` 改为模态框
   - 详情表中增加 timing/tool/token 区块

## 3. 实现设计

### 3.1 数据模型补充

每个 case 增加：

- `trialName`
- `startedAt`
- `finishedAt`
- `durationMs`
- `environmentSetupMs`
- `agentSetupMs`
- `agentExecutionMs`
- `verifierMs`
- `inputTokens`
- `cachedInputTokens`
- `outputTokens`
- `costUsd`
- `agentName`
- `agentVersion`
- `modelName`
- `provider`
- `hasTrajectory`
- `stepCount`
- `toolCallCount`
- `toolSummary`
- `errorType`

### 3.2 UI 设计

- case 卡片第一行：
  - case 名
  - 状态 badge
- case 卡片第二行：
  - reward
  - duration
  - tokens
  - trace/steps/tool calls
- 操作区：
  - 查看详情
  - 预览 result
  - 预览 log

### 3.3 模态框设计

模态框字段：

- title
- file path
- large scrollable `<pre>`
- close button
- overlay click close

## 4. 接口设计

现有 `GET /api/eval-tasks/{runId}` 继续使用，但 case 结构增强。

## 5. DFX

- 调试性：toolSummary 和 timing 可以帮助快速定位 case 表现差异
- 可维护性：trajectory 只做摘要，完整 viewer 仍交给 Harbor

## 附录 C：变更历史

| 版本 | 日期 | 作者 | Description |
|------|------|------|-------------|
| 1.0 | 2026-05-18 | Codex | Initial version |
