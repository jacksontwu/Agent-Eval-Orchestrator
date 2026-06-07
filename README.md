# Agent Eval Orchestrator

分布式 Agent 评测编排平台。`controller`(单一 FastAPI 进程)负责编排 Harbor 等执行器在
机器池上跑评测;`worker` 是轮询守护进程,通过 bot 用户认证的 HTTP 通道拉取资产、回传结果。
前端是 Vite + React 单页应用,生产环境由 FastAPI 直接托管。

## 架构

```
backend/app/
├── core/      # Settings、ids、layout、worker_paths
├── model/     # SQLAlchemy ORM + repo_*(唯一碰 SQL 的层)
├── schema/    # Pydantic 出入参(camelCase)
├── service/   # 业务逻辑 + orchestration(scheduler/reaper/asset_syncer/...)
├── api/       # 薄路由 + 认证依赖
└── worker/    # 重写的 worker daemon(HTTP 拉资产 + 流式上传)
frontend/      # Vite + React + Tailwind v4 + TanStack Query SPA
scripts/       # 运维脚本 + enroll.sh 模板
```

技术栈:Python 3.12、FastAPI、Pydantic v2、SQLAlchemy 2.0、Alembic、uv(后端);
React 19、Vite 7、TypeScript、Tailwind v4、TanStack Query/Table、react-router(前端,pnpm)。

## 快速开始

### 1. 后端

```bash
cd backend
uv venv && uv pip install -e ".[dev]"

# 认证:必须设置 token 签名密钥、应急管理员和 bot 用户(或本地开发用 AEO_ALLOW_NO_AUTH=1)
export AEO_AUTH_SECRET=your-random-secret
export AEO_ADMIN_USERNAME=admin
export AEO_ADMIN_PASSWORD=change-me
export AEO_BOT_USERNAME=worker-bot
export AEO_BOT_PASSWORD=change-me
export AEO_SHARED_ROOT=/path/to/runtime

# 建库 / 升级
uv run alembic upgrade head

# 启动
uv run uvicorn app.main:app --host 0.0.0.0 --port 8790
```

或使用运维脚本:`scripts/start-controller.sh` / `scripts/stop-controller.sh`。

### 2. 前端

```bash
cd frontend
pnpm install
pnpm dev          # 开发,自动代理 /api -> http://127.0.0.1:8790
pnpm build        # 生产构建到 frontend/dist
```

生产单端口托管:构建后让后端托管静态产物:

```bash
cd backend
AEO_FRONTEND_DIST=../frontend/dist uv run uvicorn app.main:app --host 0.0.0.0 --port 8790
# 浏览器访问 http://<controller>:8790/ 后用用户名/密码登录
```

### 3. 数据集准备

数据集仍由各 worker **预先手动准备**并放在固定路径(enroll 脚本不下载数据集)。
controller 只通过 HTTP 分发「选中的 case 子集 + bitfun 配置」。

## 添加机器(去 SSH 自注册)

在 Workers 页点击「添加机器」,复制其中的一行命令到目标机执行即可自注册:

```bash
curl -fsSL "http://<controller>:8790/api/workers/enroll.sh" \
  -H "Authorization: Bearer <admin-token>" | bash
```

脚本会:安装 uv → 从 controller 拉取项目 + Harbor 代码 bundle → `uv sync` → 启动
worker daemon(用配置的 bot 用户登录后自动调用 `/api/workers/register` 注册)。worker 机器需要有外网(uv/PyPI/
Docker 镜像走外网),并能访问 controller。

## 认证

浏览器通过用户名/密码登录 `POST /api/auth/login`,后端返回 Bearer token。前端把 token
保存在 localStorage,并通过 `Authorization: Bearer <token>` 访问 API。仅 `GET /api/health`
免认证。

首期内置三类组:

- `admin`:管理用户、组、机器和全部任务。
- `user`:创建并管理自己的任务,可查看基础机器状态。
- `bot`:worker 机器通信使用,只能访问 worker 协议和资产传输接口。

`.env` 可配置应急管理员和 bot 用户。配置用户不写入数据库,也不展示在用户管理页面。

## 测试

```bash
cd backend && uv run pytest        # 后端
cd frontend && pnpm build          # 前端类型检查 + 构建
```

## 部署假设

跑在可信内网 / VPN 之后;本期不引入 TLS / 反向代理 / 单点登录。
controller 监听网络可达地址,worker 与浏览器都通过 Bearer token 访问。
