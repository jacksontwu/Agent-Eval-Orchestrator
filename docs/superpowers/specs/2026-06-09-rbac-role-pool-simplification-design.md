# 权限体系简化：角色 + 资源池设计

- 日期：2026-06-09
- 分支：`refactor/full-stack-redesign`
- 状态：设计已确认，待写实施计划
- 取代：`docs/superpowers/specs/2026-06-07-user-auth-rbac-design.md`（原完整 RBAC 设计）

## 1. 背景与目标

`2026-06-07` 的设计落地了一套完整 RBAC：`users` / `groups` / `permissions` /
`user_groups` / `group_permissions` 五张表，12 个权限码，外加可在 UI 里给任意组自由
勾选权限码的组管理页。实际需求只有三种固定角色，这套机制偏重，理解成本高。

本设计在满足现有功能的前提下做两件事：

1. **砍掉可配置权限码体系**，把能力收敛为三个写死的系统角色。
2. **新增资源池（machine pool）维度**，实现"按池划分机器、池内用户只能用池内机器"。

设计目标：满足现有功能需求、易于理解、为未来保留扩展空间。

## 2. 已确认决策

| # | 决策点 | 结论 |
|---|---|---|
| 1 | 权限粒度 | 只要三种固定角色（admin/user/bot），删除权限码和组权限编辑 UI |
| 2 | 资源隔离 | 本期完整实现"按池划分机器、池内用户只能用池内机器" |
| 3 | 模型统一 | 万物皆 role：pool 直接是一行 `type=pool` 的 role，不另起 pool 表 |
| 4 | 用户↔池 | 多对多，一个用户可挂多个池角色 |
| 5 | 系统角色基数 | 每个人类用户必须正好有一个系统角色，池角色 0~多个 |
| 6 | 任务可见性 | 维持 owner 隔离，普通用户只看自己创建的任务 |
| 7 | 任务调度范围 | 任务绑定一个 pool，调度落在该 pool 的机器内 |
| 8 | worker 池归属 | 在 enroll 时确定，写入 `workers.role_id` |
| 9 | 普通用户机器页 | 普通用户完全看不到机器管理页 |

## 3. 核心模型：一张 roles 表统一表达能力与资源

```
roles 表
  role_id · name · type · display_name · created_at
  ├─ type=system : admin / user / bot      初始化幂等写入，不可删、不可改 type
  └─ type=pool   : 管理员在机器页新建的资源池，一池一行

user_roles 表   (user_id, role_id)          用户↔role 多对多
workers.role_id                              机器归属某个 type=pool 的 role
```

要点：

- pool **就是** role 的一种，不存在独立 pool 表，因此没有"pool 与 role 必须 1:1 对齐"
  的同步不变量——只有一个实体。
- `roles` / `user_roles` 由原 `groups` / `user_groups` 改造而来（`roles` 增加 `type` 列）。
- `workers.role_id` 必须指向一个 `type=pool` 的 role（约束校验，禁止指向 system 角色）。
- 每个人类用户在 `user_roles` 中有且仅有一个 `type=system` 角色，外加 0~多个
  `type=pool` 角色。该约束在用户表单与后端写入时一并保证。

**删除**：`permissions`、`group_permissions` 两张表，`app/core/permissions.py` 中的 12 个
权限码与 `DEFAULT_GROUP_PERMISSIONS` 等结构。

## 4. 鉴权：按系统角色判断，不再使用权限码

去掉权限码后，API 守卫直接看 principal 的系统角色，由一个 `require_role(...)` 依赖实现。
能力矩阵：

| 能力 | admin | user | bot |
|------|:---:|:---:|:---:|
| 用户管理页 / 用户管理接口 | ✅ | ✘ | ✘ |
| 机器管理页 / 资源池增删改 | ✅ | ✘ | ✘ |
| 创建任务 | ✅ | ✅ | ✘ |
| 查看 / 操作任务 | 全部 | 仅自己 owner | ✘ |
| worker 协议（注册/心跳/claim/结果） | ✅ | ✘ | ✅ |
| 资产拉取 / 结果归档传输 | ✅ | ✘ | ✅ |
| enroll 脚本生成 | ✅ | ✘ | ✘ |

- 普通用户**完全看不到机器管理页**（需求②）。创建任务时只在下拉里选自己的资源池，
  无需进入机器页。
- bot 是机器通信账号，不登录 UI，只用于 worker 协议与资产传输。

## 5. 资源隔离与任务调度

- 用户的池角色集合 ∩ 目标机器的池角色 → 任务只能调度到交集内的机器。
- 任务在创建时绑定一个 pool，落到 `runs.role_id`（沿用现有 run/owner 结构，新增 `role_id`）：
  - 用户有多个池角色时，创建页提供"资源池"下拉，必选其一。
  - 用户只有一个池角色时，自动带上，不展示选择。
  - 用户没有任何池角色时，无法创建可调度任务（前端提示联系管理员分配资源池）。
- 调度器（`service/orchestration` 的 scheduler）按 `runs.role_id` 过滤候选机器：
  只在 `workers.role_id == runs.role_id` 的在线机器中挑选。
- admin 跳过 pool 过滤，可调度到任意机器；admin 创建任务时池下拉为可选（含"全部/不限"）。

## 6. 账号来源与 bot / worker 注册

沿用 `2026-06-07` 设计的账号来源与登录流程（DB 用户 + `.env` 配置用户、Bearer token、
DB 不可用时配置用户应急登录），仅做如下调整：

- 配置管理员固定系统角色 `admin`，配置 bot 固定系统角色 `bot`；不再解析权限码集合，
  token payload 用 `role`（系统角色）+ `pools`（池角色名列表）替代原 `permissions`。
- 全局一个 bot 系统角色账号，worker 用它登录通信。
- worker 的 pool 在 **enroll 时**确定：管理员在机器页为某个资源池生成 enroll 脚本
  （脚本带 `--pool <role-name>`）；机器注册后 `workers.role_id` 写入该池角色。
- bot 账号本身不挂池角色，机器的池归属由 `workers.role_id` 承载。

## 7. 后端 API 设计

### 7.1 认证

- `POST /api/auth/login`：响应 `accessToken` / `tokenType` / `expiresAt` / `user`，
  其中 `user` 含 `role`（系统角色）与 `pools`（池角色名）。
- `GET /api/auth/me`：返回当前 principal 的用户名、来源、系统角色、池角色。

### 7.2 用户管理（仅 admin）

- `GET /api/users`、`POST /api/users`、`GET /api/users/{id}`、`PATCH /api/users/{id}`、
  `DELETE /api/users/{id}`（禁用，非物理删除）、`POST /api/users/{id}/reset-password`。
- 创建 / 更新请求体含 `role`（系统角色，单值，必填）与 `pools`（池角色名列表，可空）。

### 7.3 资源池与机器（仅 admin）

资源池即 `type=pool` 的 role，归在机器域下：

- `GET /api/pools`：列出所有 `type=pool` 角色（含每池机器数、成员数）。
- `POST /api/pools`：新建资源池（创建一行 `type=pool` role）。
- `PATCH /api/pools/{id}`：重命名 / 改展示名。
- `DELETE /api/pools/{id}`：删除资源池。删除前校验：无机器归属、无任务绑定、
  无用户挂该池角色（或级联清理 `user_roles` 中对应记录并提示）。
- 机器列表 / 指派池 / 生成 enroll 脚本沿用现有 workers 路由，新增 `role_id` 字段与
  `--pool` 参数。

### 7.4 移除

- 删除 `GET /api/groups`、`POST /api/groups`、`PATCH/DELETE /api/groups/{id}`、
  `GET /api/permissions`、`PUT /api/groups/{id}/permissions` 等组与权限码路由。
- `rbac_service.py` 中按权限码保护内置组的逻辑全部移除，改为按 `type=system` 保护。

### 7.5 守卫迁移

- `require_current_principal`：解析 Bearer token，返回含 `role` + `pools` 的 principal。
- `require_role(*allowed)`：检查系统角色是否在允许集合内。
- 任务 own/all 隔离沿用现有 owner 逻辑：admin 看全部，user 限 `owner == 当前用户名`。

## 8. 前端设计

- **users 页**：用户表单 = 系统角色单选（admin/user/bot）+ 资源池多选框；列表展示用户的
  系统角色与所属池。
- **workers 页（仅 admin）**：新增"资源池"管理区——新建 / 重命名 / 删除池、给机器指派池、
  按池生成 enroll 脚本；机器列表展示所属池。
- **groups 页**：删除，导航去掉入口。
- **create 页**：多池用户增加"资源池"下拉（单选必填）；单池自动带上不展示；无池时提示。
- **任务列表 / task-detail**：维持现状的 owner 隔离，普通用户只看自己创建的任务。
- 顶部展示当前用户、系统角色；只有 admin 看到用户管理与机器管理入口。

## 9. 迁移与兼容

- Alembic 迁移：
  - `groups` → `roles`，新增 `type` 列（system / pool），删除 `is_builtin`/`is_active`
    等组专用列中本设计不再需要的部分（保留 `name` / `display_name`）。
  - `user_groups` → `user_roles`。
  - 删除 `permissions`、`group_permissions` 表。
  - `workers` 新增 `role_id`（可空，指向 `type=pool` 角色）。
  - `runs` 新增 `role_id`（可空，任务绑定的 pool）。
- 启动幂等补齐三个 `type=system` 角色 admin / user / bot；不再补齐权限码数据。
- 同步更新 README、`.env.example`、worker 启动脚本与 enroll 模板（`--pool` 参数）。
- 旧 `2026-06-07` 文档保留作历史，状态标注被本设计取代。

## 10. 测试范围

后端：

- roles 表初始化幂等补齐三个 system 角色；system 角色不可删、不可改 type。
- 创建用户必须带且仅带一个 system 角色，可带多个 pool 角色；缺失或多个 system 角色被拒。
- `workers.role_id` 只能指向 `type=pool` 角色。
- admin / user / bot 对关键 API 的允许/拒绝矩阵（按 `require_role`）。
- 普通用户只能访问 `owner == 自己` 的任务，admin 可访问全部。
- 调度按 `runs.role_id` 过滤候选机器，只落在同池在线机器；admin 不受限。
- 资源池删除前置校验（有机器/任务/成员归属时拒绝或级联）。
- 配置管理员 / 配置 bot 登录，DB 不可用时配置登录仍可用，token payload 含 role + pools。

前端 / 验证：

- 用户表单系统角色单选 + 资源池多选正确提交。
- 机器页资源池增删改、机器指派池、按池生成 enroll 脚本。
- 普通用户看不到用户管理与机器管理入口。
- 多池用户创建任务需选池，单池自动带上，无池时受阻提示。
- `pnpm build` 通过。

## 11. 非目标

- 不做权限码管理 UI；能力固定在三个 system 角色。
- 不做 pool 内细分角色（如池成员 / 池管理员）。
- 不做跨 pool 的任务共享或团队级任务可见性。
- 不做禁用用户已签发 token 的即时失效（沿用短 token 过期）。
- 不做多租户组织、refresh token、SSO。
