# 全栈技术架构重构设计（Full-Stack Redesign）

- 日期：2026-06-06
- 分支：`refactor/full-stack-redesign`
- 状态：设计已确认，待写实施计划

## 1. 背景与目标

Agent Eval Orchestrator 是一个 `controller + worker` 分布式评测平台，controller
负责编排 Harbor 等执行器在固定机器池上跑评测。当前实现存在两块技术债：

- **后端**：纯标准库 `http.server`——单文件 `controller/server.py`（约 1479 行）手工分发约 50 个路由；
  `storage/store.py`（约 2663 行）用裸 `sqlite3` + JSON 字段直接读写并承担状态聚合；
  运行时靠 `_ensure_schema_migrations` 用 `ALTER TABLE` 打补丁做 schema 演进。
- **前端**：`controller/static.py`（约 2520 行）内联一整段 `INDEX_HTML`（原生 HTML/CSS/JS），无构建系统。

本次重构的目标态：

- 后端迁到 **FastAPI + Pydantic + SQLAlchemy 2.0 + Alembic**，严格分层 Model / Schema / Service / API。
- 前端迁到 **纯 Vite + React SPA**（pnpm），视觉风格对齐 `harbor` 的 viewer。
- 数据库继续用 **sqlite**，但用 SQLAlchemy ORM 建模、Alembic 管迁移。
- 把加机器方式从「后端 SSH 自动 provision」改为「复制脚本到目标机执行」的自注册模型，**应用层彻底去 SSH**。

参考项目：
- 后端架构与技术选型：`https://github.com/JinnanDuan/d4a-platform`（`backend/app/{model,schema,service,api}`）。
- 前端风格：`harbor` 的 viewer，`https://github.com/harbor-framework/harbor/tree/main/apps/viewer`
  （React 19 + Tailwind v4 + shadcn/ui + TanStack Query/Table）。

## 2. 关键决策（已确认）

| # | 决策点 | 结论 |
|---|---|---|
| 1 | 推进策略 | 前后端一起大重写（big-bang，中间态不保证可运行） |
| 2 | 前端形态 | 纯 Vite + React SPA，构建产物由 FastAPI 托管 |
| 3 | 数据策略 | 不保留旧数据，干净重设计 + Alembic baseline |
| 4 | Worker 协议 | 重写 worker daemon，轮询生命周期(register/claim/heartbeat/job-archive)不变；wire 格式（Pydantic schema、job-archive multipart、claim 资产契约）为协调式 breaking change，不兼容老 worker、无过渡期双协议 |
| 5 | 仓库布局 | `backend/app/{model,schema,service,api}` + `frontend/` |
| 6 | 后台编排 | 同进程后台线程，FastAPI lifespan 拉起 / 优雅退出 |
| 7 | 命令入口 | 不用 Makefile；日常用原生命令（uv/pnpm/alembic），运维封 `scripts/` shell 脚本 |
| 8 | Worker 添加 | 「添加机器」按钮生成 enroll 脚本，目标机执行自注册；彻底删 SSH/provision/tunnel/worker 远程更新；只 direct |
| 9 | controller 暴露 | 监听网络可达地址 + 共享 token，假设可信内网/VPN，不引入 TLS/反代 |

## 3. 目标态仓库布局

```
Agent-Eval-Orchestrator/
├── backend/
│   ├── app/
│   │   ├── main.py              # create_app(): 装路由、lifespan 拉后台线程、挂载前端静态产物
│   │   ├── core/               # settings(Pydantic Settings)、defaults、ids、worker_paths
│   │   ├── model/              # SQLAlchemy 层（唯一碰 SQL 的层）
│   │   │   ├── base.py         # DeclarativeBase
│   │   │   ├── db.py           # engine / SessionLocal / get_db
│   │   │   ├── tables.py       # ORM 表模型
│   │   │   └── repo_*.py       # 仓储：封装查询
│   │   ├── schema/            # Pydantic 出入参 schema
│   │   ├── service/           # 业务逻辑
│   │   │   ├── orchestration/ # 后台编排：scheduler / result_collector / rerun_coordinator /
│   │   │   │                  #   asset_syncer / viewer_manager
│   │   │   ├── executors/     # base.py / harbor.py（基本平移）
│   │   │   └── normalizers/   # harbor / harbor_job_merge / harbor_timestamps（基本平移）
│   │   └── api/
│   │       ├── router.py      # 汇总路由
│   │       ├── deps.py        # get_db、token 认证依赖
│   │       └── routes/        # health/overview/dashboard/templates/runs/batches/
│   │                          #   case_runs/workers/datasets/files/harbor_viewer/worker_protocol/enroll
│   ├── alembic/ + alembic.ini
│   ├── tests/                 # 迁移现有 tests/ 分层
│   └── pyproject.toml         # uv 管理依赖
├── frontend/                  # Vite + React SPA（pnpm）
├── scripts/                   # 运维 shell 脚本（启停、起 worker 等）
└── docs/
```

### 老代码 → 新分层映射

| 现有 | 去向 |
|---|---|
| `controller/server.py` | 拆成 `api/routes/*`（薄路由）+ `service/*`（业务） |
| `storage/store.py` | 拆成 `model/tables.py` + `model/repo_*.py`（持久化）+ `service/*`（状态聚合/派生） |
| `controller/static.py` | **删除**，由 `frontend/` 取代 |
| `controller/run_rerun_coordinator.py`、`asset_syncer.py`、`harbor_viewer.py` | 迁入 `service/orchestration/` |
| `controller/executor_config.py`、`harbor_exceptions.py`、`rerun_artifacts.py` | 迁入 `service/`（按职责归并） |
| `executors/`、`normalizers/`、`core/` | 平移到 `backend/app/service/{executors,normalizers}` 与 `backend/app/core/` |
| `worker/daemon.py` | 重写为新风格、复用 `schema/`，协议语义不变 |
| `controller/provisioner.py`、`ssh_config.py`、`ssh_runner.py`、`worker_updater.py` | **删除**（去 SSH，见 §8） |

### 分层职责红线

- **api**：只做参数校验（Pydantic）、认证依赖、调 service、返回 schema。不写业务、不直接碰 SQL。
- **service**：纯业务逻辑与编排，吃/吐 Pydantic 或领域对象，通过 repo 访问 DB。
- **model/repo**：唯一碰 SQLAlchemy/SQL 的地方。
- **schema**：API 出入参，与 ORM 表解耦（不直接把 ORM 对象当响应体）。

## 4. 数据模型与迁移

### 4.1 表（SQLAlchemy 2.0，`Mapped[]` 类型化）

保留现有领域结构，去掉与 SSH/provision 相关的表：

- `task_templates`
- `runs`（含 `parent_run_id`、`sync_*`、`rerun_*`）
- `batches`（含 `parent_batch_id`、`batch_kind`）
- `case_runs`
- `workers`（**去掉** provision/tunnel 相关列：`ssh_host_alias`、`ssh_bootstrap_host_alias`、
  `tunnel_remote_port`、`provision_status`、`last_provision_error`、`connection_mode`、`controller_internal_ip`；
  保留 `enabled`、`note`、`tags`、`allocation_weight` 等）
- `asset_sync_jobs`
- `run_rerun_jobs`
- **删除**：`provision_jobs`、`worker_update_jobs`

约定：

- 柔性 `*_json` 字段统一用 SQLAlchemy `JSON` 类型（sqlite JSON1），代码里读写直接是 dict/list，不再手动 `json.dumps/loads`。
- 时间戳沿用 ISO 文本（与 worker/harbor 既有约定一致），统一经 `core` 的 `now_iso()` 写入。
- 现有运行时 `_ensure_schema_migrations`（`ALTER TABLE` 打补丁）整体废弃，交给 Alembic。

### 4.2 Alembic

- `DATABASE_URL` 走环境变量，默认 `sqlite:///<runtime>/controller/aeo.db`。
- `alembic/env.py` 从 `app.model.base.Base.metadata` 取 `target_metadata`，支持 autogenerate。
- 一个干净的 baseline 迁移 `0001_init`，由模型 autogenerate，覆盖全部表 + 索引。
- 配 `render_as_batch=True`（batch 模式），保证 sqlite 上后续改列可迁移。
- 启动时**不**自动建表；建库/升级由 `uv run alembic upgrade head` 负责。

## 5. 后端运行时与后台编排

### 5.1 应用入口

- `app/main.py` 的 `create_app()`：配置日志、`include_router(api_router)`、挂载前端静态产物（见 §7）、注册 `lifespan`。
- `lifespan` startup：构造 `OrchestrationManager`，拉起后台 daemon 线程；shutdown：置 stop flag、join 线程、关闭 Harbor viewer 子进程，优雅退出。

### 5.2 后台编排（同进程线程，非异步）

> **明确：不用 asyncio。** 编排循环是真正的 OS 线程（`threading.Thread(daemon=True)`），内部为同步阻塞代码
> （`subprocess`、sqlite、`time.sleep`），无 `async/await`、无协程。`lifespan` 仅作为「启动/停止线程」的挂载点
> （它在语法上是 async 上下文管理器，但只做 `thread.start()` / 置 stop flag + `thread.join()`，不跑任何异步逻辑）。
> API 路由处理函数用普通 `def`（同步），由 Starlette 线程池执行，与同步 SQLAlchemy session 配套。

- **scheduler loop**：扫 queued batch → 按 worker 容量/权重平滑分配 → 标记 assigned（现有 claim/distribute 派活逻辑）。
- **heartbeat reaper loop**：扫 worker 心跳超时 → 置离线、回收其在跑 batch。
- **一次性后台作业**（asset-sync、rerun、结果回收合并）：沿用现有「API 触发后丢线程跑、状态写 `*_jobs` 表、前端轮询」模式。
- 每个循环独立 try/except 兜底，`time.sleep(interval)` 节流。
- 每个后台线程自开/自关 DB session（不复用请求 session）；sqlite engine 配 `check_same_thread=False` + WAL 模式。

## 6. Worker 与协议

- `worker/daemon.py` 用新风格重写，请求/响应体走 Pydantic schema（与 controller 复用 `schema/`）。
- **轮询生命周期不变**：`register → claim → heartbeat → job-archive` 的状态机与交互节奏保持不变。
- **但 wire 格式是协调式 breaking change**：请求/响应体改 Pydantic schema、`job-archive` 改 multipart（§6.1）、`claim`
  增加资产契约（§6.3）。worker 与 controller **同步重写、锁步发布**，不保证与老 worker 兼容，也**不做过渡期双协议**——
  因数据与 worker 均不保留（big-bang），无需向后兼容。
- 只支持 **direct 直连**：worker 通过配置的 controller URL 直接访问（见 §8、§9）。

### 6.1 文件传输（统一走 HTTP，去 SSH 后闭环）

两个方向都收敛到同一条 token 认证的 HTTP 通道，应用层不出现 SSH/rsync。

- **结果回传（worker → controller）**：现状已走 HTTP（worker tar → POST `/api/workers/job-archive`），与 SSH 无关。
  本次把现有「base64 塞 JSON、整包进内存」改为**流式 multipart 上传 tar**，降低大文件下的内存与带宽开销。
- **资产下发（controller → worker）**：现状远端依赖 SSH/rsync（`asset_syncer` 的 `ssh` transport），本次**翻转为 worker 拉**：
  - controller 暴露带 token 的资产接口 `GET /api/workers/assets/<assetManifestId>`（返回 manifest + 可分项流式下载；契约见 §6.3）。
  - worker 在 claim 到 batch、开跑前，按 claim 响应里的资产契约把所需的 case 子集 + bitfun 配置/CLI 拉到自己的 sync 根目录。
  - `asset_syncer` 保留（仍负责挑选 case 子集、组装 sync 根、生成 manifest），但 transport 从 `local`/`ssh`
    改为 `local`（同机直接拷）/`http`（远端 worker 拉），**删除 `ssh` transport 与 `ssh_host_alias` 依赖**。
- **大数据集不走这条通道**：数据集仍由各 worker 预先手动准备并放在固定路径（与 enroll 脚本不下数据集一致）。
  HTTP 通道只传「选中的 case 子集 + 配置」，体量可控。
- 资产拉取支持**流式 + 断点续传**（chunked / Range），大子集传输可恢复。

### 6.2 「数据集很大」的两种含义（界定清楚）

- **(a) 数据集定义文件**（`harbor download` 拉下来的、asset-sync 传的 case 目录：题面/测试/Dockerfile/少量资产）：
  MB 级；且每次任务只传**选中的 case 子集**，不传整份。走 HTTP 完全够用。
- **(b) 每个 case 运行时的 Docker 镜像**（真正的「大」，单个几百 MB～GB，全量可达几十～上百 GB）：
  由 worker 上的 Docker 在运行时从镜像 registry 拉，**不经本系统的 HTTP 通道，也不经 asset-sync**。worker 需能访问 registry（或自备本地 registry 加速），属于本系统之外。

### 6.3 `claim` ↔ 资产契约（新增字段，breaking change）

现有 `claim` 只返回 `datasetRef` / `executorConfig`，worker 随后直接检查本地 dataset 路径。新模型下 worker 要先从
controller 拉资产，因此 `claim` 响应**新增资产同步契约**：

- `assetManifestId`：本 batch 资产清单 id，也是拉取 key。
- `assetUrl`：拉取入口（`GET /api/workers/assets/<assetManifestId>`，返回 manifest JSON + 可分项流式下载）。
- `assetManifest`：清单，每个 entry 含 `path`（相对 sync 根）、`size`、`sha256`、`kind`（`case` / `bitfun` / `cli`）。
- `targetRootRel`：worker 侧落地的相对结构；绝对 sync 根由 worker 用自身 `capabilities.sharedRoot` 推导，controller 不假设 worker 路径。

工作流（claim 之后、执行之前）：

1. worker 据 `assetManifest` 逐项下载到 `targetRoot`，按 `sha256` 校验；单项失败**有限次重试**（chunked / Range 续传）。
2. 全部校验通过 → 进入执行；任一项最终失败 → 通过 `heartbeat` 上报该 batch 进入 **`sync_failed`**（不进入执行），
   controller 据此标记并可重新分配（与现有 `*_jobs` 失败语义一致）。
3. `job-archive` multipart 回传时**附带 `batchId` + 整包 `sha256`**，controller 校验后再入库。

> 说明：这是 §6 标注的协调式 breaking change 的一部分——老 worker 不识别这些字段，但因锁步重写、不保留旧 worker，无需兼容。

## 7. 前端

技术栈对齐 viewer 的风格，但用纯 Vite SPA（不上 react-router framework 模式）：

- React 19 + Vite 7 + TypeScript；路由用 `react-router` 客户端 data router 模式。
- Tailwind v4 + shadcn/ui（Radix + cva）+ lucide-react + sonner。
- TanStack Query 管服务端状态/轮询；TanStack Table 做 dashboard/cases 表格；zod 校验；nuqs 管 URL 查询态。

```
frontend/
├── app/
│   ├── routes/        # tasks(dashboard) / create / task-detail / workers / ...（对齐现有页面）
│   ├── components/ui/ # shadcn 组件
│   ├── components/    # 业务组件
│   ├── lib/           # api.ts(带 token 的 fetch 封装) / types.ts / hooks.ts / utils.ts
│   ├── root.tsx / main.tsx / app.css
├── index.html、vite.config.ts、tsconfig.json、components.json、package.json
```

托管与开发：

- **生产**：`pnpm build` 出静态产物，FastAPI 用 `StaticFiles` 托管 + SPA fallback（未命中 `/api` 的路由回 `index.html`）。单端口。
- **开发**：Vite dev server 跑前端，`vite.config.ts` 里 proxy `/api` → FastAPI，热更新。
- **认证**：共享 token，支持请求头 `X-AEO-Token` 或 query `?token=`（浏览器首次访问落 cookie）。
  **仅 `/api/health` 放行；其余全部接口都必须经 `deps.py` 的认证依赖**——包括 worker 协议（`/api/workers/*`，含
  `register`/`claim`/`heartbeat`/`job-archive`）、`enroll.sh`、assets 拉取。worker 用 header 带 token；
  `enroll.sh` 的 `curl … | bash` 用 query token。（与现有「所有写接口先过 `_is_authorized()`」一致，不放宽。）

## 8. Worker 添加（去 SSH 自注册模型）

替换原来整套 SSH provision：

- Workers 页 **「添加机器」按钮** → 后端 `GET /api/workers/enroll.sh`（带 token）生成一段可复制的 bootstrap 脚本。
- 形式：`curl -fsSL http://<controller>/api/workers/enroll.sh?token=... | bash`，或弹窗里给整段脚本文本复制。
- 脚本动作：装 uv/Docker → **从 Controller 拉**本项目 + Harbor 代码（tar/git-bundle，去 GitHub 依赖）→ `uv sync` 装依赖 → 起 daemon（`nohup`/`systemd`）→ daemon 调 `/api/workers/register` 自注册。
- **不含数据集下载**：数据集仍按现状单独手动准备（README 保留这一节），机器跑评测前需先把数据集放到固定路径。

### 8.1 外部依赖来源（worker 有外网，Controller 只代理代码+资产）

前提：worker 机器**有外网**。Controller 只承担「代码 + case 资产」的分发，其余走外网/OS 源，避免把 Controller 做成全量镜像源（YAGNI）。

| 项 | 来源 |
|---|---|
| 项目 + Harbor 代码 | **Controller**（enroll 接口提供 tar/git-bundle） |
| case 子集 + bitfun 配置 | **Controller**（§6.1 worker 拉） |
| `uv` 二进制 | 外网（astral.sh 官方安装器） |
| Docker 引擎 | OS 包管理器 / OS 镜像源 |
| Python 依赖 | 外网 PyPI（`uv sync`） |
| Docker 运行镜像 | 镜像 registry（运行时 Docker 拉） |

> 受限/气隙网络（Controller 额外托管 uv 二进制、Python wheelhouse、甚至 registry 镜像）为**非本期目标**，留作后续扩展点。

被删除的能力（后端减肥）：

- `provisioner.py`、`ssh_config.py`、`ssh_runner.py`、`TunnelManager`/反向隧道、`connection_mode=tunnel`。
- 数据表 `provision_jobs`。
- `worker_updater.py` 与 `worker_update_jobs` 表——「更新 worker 代码」改为**手动重跑 enroll 脚本 / 重启 daemon**（页面不再做远程更新）。
- 应用代码中**不再出现任何 SSH 调用**。人工 SSH 登录目标机粘贴脚本属于纯运维动作，与代码无关。

## 9. 部署与暴露

- controller 监听**网络可达地址**（而非 loopback-only），worker 与浏览器都带共享 token 直接访问。
- 安全前提：跑在可信内网 / VPN 之后；本期**不引入 TLS / 反向代理**。
- 不再需要旧的 `ssh -L` 浏览器端口转发，也不再需要反向隧道。

## 10. 工具链与命令入口

- 后端依赖（uv / `pyproject.toml`）：`fastapi`、`uvicorn[standard]`、`pydantic`、`pydantic-settings`、`sqlalchemy`、`alembic`、`pytest`、`httpx`。
- 前端：`pnpm`。
- **不用 Makefile**。日常用原生命令，写进 README：
  - 起后端：`uv run uvicorn app.main:app --host <host> --port 7380`
  - 迁移：`uv run alembic upgrade head` / `uv run alembic revision --autogenerate -m "..."`
  - 测试：`uv run pytest`
  - 前端：`pnpm dev` / `pnpm build`
- 运维封 `scripts/` shell 脚本（有副作用、步骤多）：`start-controller.sh`、`stop-controller.sh`、`start-worker.sh`。

## 11. 测试

- 保留并迁移现有 `tests/` 分层（api/controller/core/executors/model/normalizers/schema/storage/worker）到 `backend/tests/`。
- API 层用 FastAPI `TestClient` + `httpx`；DB 测试用临时 sqlite 文件 + `alembic upgrade` 或 `create_all`。
- service 层针对编排逻辑（分配、合并、rerun）写单测，沿用现有用例资产。
- 删除与 SSH/provision/worker-update 相关的旧测试。

## 12. 明确的非目标（YAGNI）

- 不引入除 sqlite 外的数据库。
- 不引入 TLS / 反向代理 / 多用户鉴权（沿用单一共享 token）。
- 不做 react-router framework / SSR。
- 不做旧数据迁移。
- 不保留 SSH provision / 反向隧道 / 页面远程更新 worker。
- enroll 脚本不负责下载数据集。
- Controller 不做全量镜像源：uv/Docker/PyPI/Docker 镜像仍走外网/OS 源；气隙网络支持留作后续。
- 不在本系统内传输 Docker 运行镜像（由 worker 的 Docker 从 registry 拉）。

## 13. 风险与缓解

- **big-bang 重写中间态不可运行**：通过把工作切成可独立验证的纵切片（先打通后端 health + 一条 runs 链路 + 前端骨架，再逐页迁移）降低风险；实施计划里细化。
- **后台线程 + sqlite 并发**：WAL + 每线程独立 session + 写操作集中在 service，避免锁竞争；必要时给写操作加进程内串行。
- **去 SSH 后远端机器接入门槛**：靠 enroll 脚本与清晰 README 弥补；controller 必须网络可达是前提。
- **enroll 脚本跨环境易碎**：脚本保持幂等、可重跑，失败有清晰日志；数据集等重资产排除在脚本之外。
- **大文件走 HTTP**：结果回传与资产下发改流式 tar（multipart / chunked），避免整包进内存；设合理超时与失败重试；
  大数据集不走该通道（预置在 worker），把 HTTP 传输限制在可控体量内。
