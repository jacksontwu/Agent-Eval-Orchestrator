# 任务清单: Harbor Case 观测面板增强

**输入**: 来自 `/.specs/002-harbor-case-insights/` 的设计文档  
**约束**: 任务拆分必须覆盖 Harbor normalizer、case 页面展示和模态框交互

---

## 阶段 1: 规格与数据面

- [ ] T001 [US1] 扩展 Harbor case 归一化字段，位于 `src/agent_eval_orchestrator/normalizers/harbor.py`
- [ ] T002 [US1] 调整 case 数据映射与推断逻辑，位于 `src/agent_eval_orchestrator/storage/store.py`

## 阶段 2: 页面增强

- [ ] T003 [US1] 在 case 卡片中增加 reward、duration、token、trace 摘要，位于 `src/agent_eval_orchestrator/controller/static.py`
- [ ] T004 [US1] 将 `result/log` 预览改成模态框交互，位于 `src/agent_eval_orchestrator/controller/static.py`
- [ ] T005 [US1] 调整 case 详情区域布局，位于 `src/agent_eval_orchestrator/controller/static.py`

## 阶段 3: 验证

- [ ] T006 [US1] 用真实 Harbor case 验证增强字段在页面上可见
- [ ] T007 [US1] 验证 `result/log` 模态框打开、关闭、路径展示和滚动行为
