# 用户管理、认证与 RBAC 鉴权设计

- 日期：2026-06-07
- 分支：`refactor/full-stack-redesign`
- 状态：设计已确认，待写实施计划

## 1. 背景与目标

当前 Agent Eval Orchestrator 的重构版使用 FastAPI + React SPA。认证仍是全局共享
`AEO_TOKEN`：浏览器、worker、enroll、资产接口都用同一个 token 访问。README 中也明确
当前阶段未引入多用户鉴权。

本次设计目标是把共享 token 替换为统一的用户体系：

- 提供数据库用户管理能力，支持用户 CRUD 语义，其中删除按“禁用用户”处理。
- 支持数据库失效时的应急登录：后台 `.env` 中可配置管理员和 bot 用户。
- 支持按分组做权限控制，首期包含管理员组、普通用户组、bot/机器用户组，并提供组管理。
- worker/enroll 不再依赖全局 `AEO_TOKEN`，改由 bot/机器用户参与认证。
- 普通用户只能管理自己的评测任务；管理员可管理用户、机器和全部任务。

## 2. 已确认决策

| # | 决策点 | 结论 |
|---|---|---|
| 1 | worker/enroll 认证 | 统一用户体系，用 bot/机器用户替代全局 `AEO_TOKEN` |
| 2 | bot 凭据 | bot 用户从 `.env` 平铺用户名/密码读取 |
| 3 | 配置用户展示 | 配置用户不进入用户管理页面 |
| 4 | 普通用户权限 | 创建和查看自己的任务，查看基础机器状态，不能管理机器或用户 |
| 5 | 登录态 | 登录后签发 Bearer token，前端保存在 `localStorage` |
| 6 | 配置格式 | `.env` 平铺字段，而不是 JSON 配置文件 |
| 7 | 密码存储 | DB 用户只存哈希；`.env` 配置用户可用明文密码实时校验 |
| 8 | 权限模型 | 建完整 RBAC 表结构，首期做组管理 UI，但权限码由系统内置 |
| 9 | 用户删除 | 默认禁用用户，不物理删除 |

## 3. 账号来源与认证流程

### 3.1 账号来源

系统有两类账号来源：

- **数据库用户**：存储在 `users` 表中，由管理员通过用户管理页面创建、禁用、修改分组、
  重置密码。密码只保存哈希。
- **配置用户**：由 `.env` 平铺字段声明。配置用户不写入 DB，不展示在用户管理页面，用于
  DB 故障时的应急管理员登录，以及 bot/worker 登录。

配置管理员固定归入 `admin` 组，配置 bot 固定归入 `bot` 组。二者的权限仍通过统一 RBAC
权限码解析，不走单独的全局 token 分支。

首期 `.env` 字段：

```dotenv
AEO_ADMIN_USERNAME=admin
AEO_ADMIN_PASSWORD=change-me
AEO_BOT_USERNAME=worker-bot
AEO_BOT_PASSWORD=change-me
AEO_AUTH_SECRET=replace-with-random-secret
# AEO_ACCESS_TOKEN_TTL_MINUTES=480
```

旧 `AEO_TOKEN` 进入废弃路径：实现阶段应从前端、worker、enroll 和 API 依赖中移除对全局
共享 token 的依赖，并更新 README / `.env.example`。

### 3.2 登录流程

- 浏览器、worker 和运维脚本统一调用 `POST /api/auth/login`，提交用户名和密码。
- 后端先尝试配置用户认证，再尝试数据库用户认证。配置用户路径必须能在数据库不可用时完成。
- 登录成功后签发短期 Bearer token。token payload 至少包含：
  - `sub`：用户名或用户 id。
  - `source`：`config` 或 `db`。
  - `groups`：用户所属组。
  - `permissions`：由组解析出的权限码集合。
  - `exp`：过期时间。
- 前端把 token 存到 `localStorage`，后续请求使用 `Authorization: Bearer <token>`。
- worker 使用 bot 用户名/密码登录，拿 Bearer token 后访问 worker 协议接口。
- enroll 脚本由管理员生成。脚本可携带或读取配置 bot 的用户名/密码，在目标机器上登录后再
  启动 worker；bot 本身不具备生成 enroll 脚本的权限。

## 4. RBAC 数据模型

新增表：

- `users`
  - `user_id`：主键。
  - `username`：唯一用户名。
  - `password_hash`：密码哈希。
  - `display_name`：展示名。
  - `is_active`：禁用用户时置 false。
  - `created_at` / `updated_at` / `last_login_at`。
- `groups`
  - `group_id`、`name`、`display_name`、`description`。
- `permissions`
  - `permission_id`、`code`、`description`。
- `user_groups`
  - `user_id`、`group_id`，支持一个用户属于多个组。
- `group_permissions`
  - `group_id`、`permission_id`，定义组拥有的权限。

首期通过 Alembic 迁移和启动幂等补齐初始化三个内置组：

- `admin`
- `user`
- `bot`

首期做组管理 UI。管理员可以创建自定义组、修改组展示信息、启用/禁用自定义组，并给组分配
系统内置权限码。权限码本身仍由代码和迁移定义，首期不允许在 UI 中新增任意权限码。

内置组保护规则：

- `admin`、`user`、`bot` 三个内置组不能删除。
- `admin` 组不能被禁用，且必须保留 `users.manage` 和 `groups.manage`。
- `bot` 组不能被禁用，且必须保留 `worker_protocol.use` 和 `assets.use`。
- 若某次组权限修改会导致当前管理员失去 `users.manage` 或 `groups.manage`，后端拒绝该操作。

## 5. 默认权限边界

权限码采用字符串，API 依赖按权限码判断。首期权限集合：

- `users.manage`：用户管理。
- `groups.manage`：组管理和组权限分配。
- `workers.read`：查看基础机器状态。
- `workers.manage`：添加、启停、删除机器。
- `tasks.create`：创建任务。
- `tasks.read_own`：查看自己的任务。
- `tasks.manage_own`：操作自己的任务。
- `tasks.read_all`：查看全部任务。
- `tasks.manage_all`：操作全部任务。
- `worker_protocol.use`：worker 注册、心跳、claim、结果上传。
- `assets.use`：worker 资产拉取、结果归档相关传输。
- `enroll.manage`：生成和访问 enroll 脚本。

默认组权限：

| 组 | 权限 |
|---|---|
| `admin` | 所有权限 |
| `user` | `tasks.create`、`tasks.read_own`、`tasks.manage_own`、`workers.read` |
| `bot` | `worker_protocol.use`、`assets.use` |

普通用户的数据隔离基于现有 `owner` 字段落地：

- 创建任务时后端强制 `owner = 当前登录用户名`，忽略或拒绝前端传入的 owner。
- 普通用户只能读取和操作 `owner == 当前用户名` 的任务、批次和 case run。
- 管理员可以读取和操作全部任务，并可按 owner 过滤。

## 6. 后端 API 设计

### 6.1 认证 API

- `POST /api/auth/login`
  - 请求：`username`、`password`。
  - 响应：`accessToken`、`tokenType`、`expiresAt`、`user`。
- `GET /api/auth/me`
  - 需要 Bearer token。
  - 响应当前 principal 的用户名、来源、组、权限。

### 6.2 用户管理 API

仅 `users.manage` 可访问：

- `GET /api/users`
- `POST /api/users`
- `GET /api/users/{user_id}`
- `PATCH /api/users/{user_id}`
- `DELETE /api/users/{user_id}`
- `POST /api/users/{user_id}/reset-password`

`DELETE` 执行禁用用户，不物理删除。禁用用户不能登录，历史任务的 `owner` 字符串保留。

### 6.3 组管理 API

仅 `groups.manage` 可访问：

- `GET /api/groups`
- `POST /api/groups`
- `GET /api/groups/{group_id}`
- `PATCH /api/groups/{group_id}`
- `DELETE /api/groups/{group_id}`
- `GET /api/permissions`
- `PUT /api/groups/{group_id}/permissions`

`DELETE` 对自定义组执行禁用或软删除，不物理删除内置组。`GET /api/permissions` 返回系统内置
权限码，供组管理页面选择。`PUT /api/groups/{group_id}/permissions` 只能选择这些内置权限码，
不能创建新权限码。

### 6.4 现有 API 鉴权迁移

当前 `require_token` 替换为：

- `require_current_principal`：解析 Bearer token，返回统一 principal。
- `require_permission(code)`：检查 principal 是否拥有指定权限。
- `require_task_access(...)` 或等价 service 层校验：处理 own/all 数据隔离。

路由边界：

- `GET /api/health` 继续免认证。
- 用户管理路由需要 `users.manage`。
- 组管理路由需要 `groups.manage`。
- 机器管理写操作需要 `workers.manage`，机器只读需要 `workers.read` 或 `workers.manage`。
- 创建任务需要 `tasks.create`。
- 任务详情、批次、case runs 需要 own/all 判断。
- worker 协议接口需要 `worker_protocol.use`。
- worker 资产和归档传输需要 `assets.use`。
- enroll 脚本生成需要 `enroll.manage`，默认只给管理员。脚本中的 worker 运行阶段使用 bot
  凭据登录并访问 worker 协议；bot 不具备 `enroll.manage`。

## 7. 前端设计

- 新增 `/login` 页面。未登录或 token 失效时跳转登录页。
- `frontend/app/lib/api.ts` 改为发送 `Authorization: Bearer <token>`。
- `apiFetch` 遇到 401 时清理本地 token，并跳转登录。
- 顶部导航显示当前用户和组，提供退出登录。
- 只有管理员看到“用户管理”和“组管理”入口。
- 新建任务页面不再允许用户输入或伪造 owner；前端最多展示“归属：当前用户”。
- 任务列表：
  - 管理员看到全部任务，可按 owner 过滤。
  - 普通用户只看到自己的任务。
- 机器页：
  - 管理员可添加、启停、删除机器。
  - 普通用户只能查看基础状态，隐藏管理按钮。
- 用户管理页只展示 DB 用户，支持创建、禁用、修改分组、重置密码；不展示 `.env` 配置用户。
- 组管理页展示 DB 组和系统内置权限码，支持创建自定义组、修改组展示信息、启用/禁用自定义组、
  给组分配权限。内置组展示保护提示，不提供删除入口。

## 8. 安全与错误处理

- 密码哈希使用成熟库，例如 `passlib[bcrypt]` 或 `argon2-cffi`。数据库永不存明文密码。
- Bearer token 使用 `AEO_AUTH_SECRET` 签名，并支持 `AEO_ACCESS_TOKEN_TTL_MINUTES` 配置过期时间。
- 配置用户的明文密码只从 `.env` 读取，不写入数据库。部署时必须把 `.env` 当敏感文件保护。
- 如果数据库不可用：
  - 配置管理员和配置 bot 仍可登录。
  - 依赖数据库的用户管理、任务和机器 API 返回明确错误。
  - 配置用户不会出现在用户管理 API 中。
- 如果没有任何 DB 管理员，也没有配置管理员，启动时记录高严重度提示；系统不自动创建默认账号。
- 禁用用户已有 token 的即时失效不是首期目标。首期依赖短 token 过期；如需强制下线，后续可加
  token version 或撤销列表。

## 9. 迁移与兼容

- 新增 Alembic 迁移创建 RBAC 表和默认权限数据。
- 应用启动时做幂等默认组/权限补齐，支持后续新增权限码后自动补数据。
- README、`.env.example`、worker 启动脚本和 enroll 模板同步迁移到用户名/密码登录流程。
- 旧 `?token=` 和 `X-AEO-Token` 不再作为主认证机制。实现阶段可选择短期报错提示迁移，但不再授权访问。

## 10. 测试范围

后端测试：

- 密码哈希与校验。
- DB 用户登录成功、失败、禁用用户拒绝登录。
- 配置管理员和配置 bot 登录，且 DB 不可用时配置登录仍可用。
- RBAC 默认数据初始化和权限解析。
- 组管理 API 的创建、更新、禁用和权限分配。
- 内置组保护：不能删除 `admin/user/bot`，不能移除关键权限导致系统不可管理。
- 管理员、普通用户、bot 对关键 API 的允许/拒绝矩阵。
- 普通用户只能访问自己的任务，管理员可访问全部任务。
- 用户删除实际为禁用，不物理删除。

前端测试/验证：

- 登录页、退出登录、401 跳转。
- 管理员可见用户管理和组管理入口，普通用户不可见。
- 组管理页能编辑自定义组权限，并正确禁用内置组的危险操作。
- 新建任务不提交 owner 或提交后被后端覆盖。
- 机器页按权限隐藏管理按钮。
- `pnpm build` 通过。

## 11. 非目标

- 首期不做权限码管理 UI；权限码只能由代码和迁移内置。
- 首期不做多租户组织、项目空间或更复杂的数据域隔离。
- 首期不做 refresh token、强制撤销 token 或单点登录。
- 首期不把 `.env` 配置用户同步到数据库。
