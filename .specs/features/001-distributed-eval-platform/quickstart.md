# Quickstart: 分布式评测平台一期设计

## 场景 1：固定 worker 跑 Harbor 评测

1. 启动 controller。
2. 启动两台 worker，确保 Harbor CLI 和 Docker 可用。
3. 创建 `harbor-docker` 类型任务模板。
4. 创建 Run，并发起一个 Batch。
5. 观察 worker 领取任务并执行 Harbor。
6. 在批次目录中查看：
   - `summary.json`
   - `cases/*.json`
   - `harbor/jobs/...`
   - `worker.log`

## 场景 2：重跑同一个 Run

1. 选择一个已完成的 Run。
2. 发起新的 Batch。
3. 验证旧 Batch 仍可查看，新 Batch 独立归档。

## 场景 3：模拟 Harbor 结果回收失败

1. 人为删除 Harbor 结果文件。
2. 触发结果归一化。
3. 确认 Batch 标记失败，保留错误文本和已有日志索引。
