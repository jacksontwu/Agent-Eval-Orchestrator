# Agent Eval Orchestrator

一个面向多机固定 worker 集群的分布式评测平台。

项目目标：

- 以 `controller + worker` 模型管理 Harbor、本地执行器等多种评测后端。
- 将 Harbor 视为可替换执行器，而不是平台内核。
- 一期优先支持固定机器池、Harbor `docker` 执行、统一结果回收与批次归档。
- 为后续弹性 worker、CodeBench 后处理、自定义执行器预留清晰扩展边界。

当前仓库按照 SDD 组织设计文档，见 `.specs/`。

## 目录约定

- 仓库根目录：`/root/projects/agent-eval-orchestrator`
- 默认运行根目录：`/root/projects/agent-eval-orchestrator/runtime`
- 本地 Harbor 仓库：`/root/projects/harbor`
- controller 默认只监听：`127.0.0.1`

本文档中的 IP、端口、用户、token 都使用匿名占位符，请按实际环境替换：

- `<CONTROLLER_HOST>`
- `<CONTROLLER_SSH_PORT>`
- `<CONTROLLER_USER>`
- `<REMOTE_WORKER_HOST>`
- `<REMOTE_SSH_PORT>`
- `<REMOTE_USER>`
- `<AEO_TOKEN>`

## 环境准备

建议两台机器都满足以下条件：

- Python `>=3.10`
- Docker 与 `docker compose`
- `uv`
- 一份可用的 Harbor 仓库

安装本项目依赖最简单的方式：

```bash
cd /root/projects/agent-eval-orchestrator
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

如果你本机已经用系统 Python 直接跑，也可以不建虚拟环境，直接使用：

```bash
cd /root/projects/agent-eval-orchestrator
PYTHONPATH=src python3 -m agent_eval_orchestrator.controller.server --help
```

## 数据集预拉取

当前页面创建任务时只允许选择两个预设数据集：

- `terminal-bench/terminal-bench-2`
- `swe-bench/swe-bench-verified`

要让页面可用，需要把它们先下载到 controller 和 remote worker 的固定路径。

### 本机下载

```bash
mkdir -p /root/projects/agent-eval-orchestrator/datasets
cd /root/projects/agent-eval-orchestrator/datasets

cd /root/projects/harbor
uv run harbor download terminal-bench/terminal-bench-2 -o /root/projects/agent-eval-orchestrator/datasets
uv run harbor download swe-bench/swe-bench-verified -o /root/projects/agent-eval-orchestrator/datasets
```

下载完成后，典型目录应为：

```text
/root/projects/agent-eval-orchestrator/datasets/terminal-bench-2
/root/projects/agent-eval-orchestrator/datasets/swe-bench-verified
```

### 远端 worker 下载

```bash
ssh -p <REMOTE_SSH_PORT> <REMOTE_USER>@<REMOTE_WORKER_HOST>

mkdir -p /home/<REMOTE_USER>/agent-eval-orchestrator/datasets
cd /home/<REMOTE_USER>/harbor
uv run harbor download terminal-bench/terminal-bench-2 -o /home/<REMOTE_USER>/agent-eval-orchestrator/datasets
uv run harbor download swe-bench/swe-bench-verified -o /home/<REMOTE_USER>/agent-eval-orchestrator/datasets
```

## 启动 controller

controller 默认建议只监听本机回环地址，通过 SSH 转发给浏览器访问。

```bash
cd /root/projects/agent-eval-orchestrator
PYTHONPATH=src python3 -u -m agent_eval_orchestrator.controller.server \
  --host 127.0.0.1 \
  --port 7380 \
  --shared-root /root/projects/agent-eval-orchestrator/runtime \
  --auth-token <AEO_TOKEN>
```

如果要后台运行，可以用：

```bash
cd /root/projects/agent-eval-orchestrator
mkdir -p runtime/logs
setsid env PYTHONPATH=src python3 -u -m agent_eval_orchestrator.controller.server \
  --host 127.0.0.1 \
  --port 7380 \
  --shared-root /root/projects/agent-eval-orchestrator/runtime \
  --auth-token <AEO_TOKEN> \
  > runtime/logs/controller-7380.log 2>&1 < /dev/null &
```

## 启动本地 worker

```bash
cd /root/projects/agent-eval-orchestrator
PYTHONPATH=src python3 -u -m agent_eval_orchestrator.worker.daemon \
  --controller-url http://127.0.0.1:7380 \
  --worker-id local-a \
  --display-name local-a \
  --host 127.0.0.1 \
  --shared-root /root/projects/agent-eval-orchestrator/runtime \
  --local-root /root/projects/agent-eval-orchestrator/runtime/workers/local-a/local \
  --slots 1 \
  --poll-interval 3 \
  --auth-token <AEO_TOKEN>
```

## 启动远端 worker

远端 worker 通过 SSH 反向隧道访问本地 controller。推荐分两步。

### 1. 在 controller 所在机器建立反向隧道

```bash
ssh -f -N \
  -p <REMOTE_SSH_PORT> \
  -o StrictHostKeyChecking=no \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -R 17380:127.0.0.1:7380 \
  <REMOTE_USER>@<REMOTE_WORKER_HOST>
```

建立后，远端机器上的 `127.0.0.1:17380` 会转发回 controller 的 `127.0.0.1:7380`。

### 2. 在远端机器启动 worker

```bash
ssh -p <REMOTE_SSH_PORT> <REMOTE_USER>@<REMOTE_WORKER_HOST>

cd /home/<REMOTE_USER>/agent-eval-orchestrator
PYTHONPATH=src python3 -u -m agent_eval_orchestrator.worker.daemon \
  --controller-url http://127.0.0.1:17380 \
  --worker-id remote-a \
  --display-name remote-a \
  --host <REMOTE_WORKER_HOST> \
  --shared-root /home/<REMOTE_USER>/agent-eval-orchestrator/runtime \
  --local-root /home/<REMOTE_USER>/agent-eval-orchestrator/runtime/workers/remote-a/local \
  --slots 1 \
  --poll-interval 3 \
  --auth-token <AEO_TOKEN>
```

## 浏览器访问

controller 默认不直接对公网暴露，建议通过本地 SSH 转发访问：

```bash
ssh -L 7380:127.0.0.1:7380 -p <CONTROLLER_SSH_PORT> <CONTROLLER_USER>@<CONTROLLER_HOST>
```

本地浏览器打开：

```text
http://127.0.0.1:7380/?token=<AEO_TOKEN>
```

## Harbor Viewer

合并后的 jobs 默认放在：

```text
/root/projects/harbor/jobs
```

可以单独启动 Harbor Viewer：

```bash
cd /root/projects/harbor
uv run harbor view --port 7369 --host 127.0.0.1 ./jobs/
```

如需本地浏览器访问：

```bash
ssh -L 7369:127.0.0.1:7369 -p <CONTROLLER_SSH_PORT> <CONTROLLER_USER>@<CONTROLLER_HOST>
```

然后打开：

```text
http://127.0.0.1:7369/
```

## 运行说明

- 页面创建分布式任务时，controller 会按 worker 平均切分 case。
- 每个 worker 的并发度由任务里的 `Per Worker Concurrency` 控制。
- 如果某台 worker 当前 `slots_used == slots_total`，新的 batch 会排队，直到这台 worker 空闲。
- worker 执行完成后，会把 Harbor jobs 回收到 controller，再按 task 名合并成单个 Harbor job。

## 常见检查

查看 controller 健康状态：

```bash
curl http://127.0.0.1:7380/api/health
```

查看 worker 状态：

```bash
curl http://127.0.0.1:7380/api/workers
```

查看当前任务摘要：

```bash
curl http://127.0.0.1:7380/api/dashboard/tasks
```
