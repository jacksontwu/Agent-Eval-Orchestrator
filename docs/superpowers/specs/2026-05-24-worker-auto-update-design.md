# Worker Auto-Update Design

## Goal

Add a **manual worker code update** flow to the Controller dashboard and API. Operators can remotely `git pull` worker code repositories, sync dependencies, and restart the worker daemon — without re-running full provision/bootstrap.

## Requirements Summary

| Dimension | Decision |
|-----------|----------|
| Trigger | Manual only — Dashboard button + API |
| Repos updated | Configurable `targets`; default both AEO and Harbor |
| Git strategy | Pull current checked-out branch; no branch switching |
| Active batches | Block update when worker has running or queued batches (409) |
| SSH requirement | Required — workers without `ssh_host_alias` cannot be updated (400) |
| Execution model | Async job with step progress (like provision) |
| UI scope | API + Dashboard in this iteration |

## Non-Goals

This feature will **not**:

- Auto-detect remote git changes or schedule periodic updates.
- Update workers without SSH (local or manually registered workers).
- Switch git branches or pin to specific commits/tags.
- Bulk-update multiple workers in one action.
- Destroy or recreate ECS instances.
- Update controller code itself — only remote worker hosts.

## Chosen Approach

**Independent `WorkerUpdater` class + `worker_update_jobs` table**, mirroring the existing `Provisioner` / `provision_jobs` and `AssetSyncer` / `asset_sync_jobs` patterns.

Alternatives considered:

| Approach | Verdict |
|----------|---------|
| Independent `WorkerUpdater` + new job table | **Chosen** — clear boundaries; consistent with existing async job patterns |
| Extend `Provisioner` with `mode=update` | Rejected — mixes provision lifecycle with update; confuses `provision_status` |
| Generic job runner framework | Rejected — over-engineered for a single operation type |

## Architecture

```text
Dashboard (Worker Detail)
    │
    └─ POST /api/workers/{workerId}/update
           │
           ├─ 1. Validate worker exists → 404
           ├─ 2. Validate ssh_host_alias present → 400
           ├─ 3. Validate runningCount=0 and queuedCount=0 → 409
           ├─ 4. Validate no active update/provision job → 409
           ├─ 5. Create worker_update_jobs row
           └─ 6. WorkerUpdater.start_job_async()
                  │
                  ├─ validate_ssh
                  ├─ stop_daemon          (reuse Provisioner.decommission_worker)
                  ├─ pull_aeo             (if targets includes "aeo")
                  ├─ sync_aeo             (uv sync; if targets includes "aeo")
                  ├─ pull_harbor          (if targets includes "harbor")
                  ├─ restart_daemon       (path-aware start command)
                  └─ wait_register
```

### Path Resolution

Derive paths from the worker's `capabilities.sharedRoot` using `worker_paths.py`:

| Path | Function |
|------|----------|
| AEO repo | `repo_root_from_shared_root(sharedRoot)` |
| Harbor repo | `default_harbor_repo_from_shared_root(sharedRoot)` |
| UV binary | `default_uv_binary_from_shared_root(sharedRoot)` |

Refactor `build_daemon_start_command()` to accept dynamic paths instead of hardcoded `DEFAULT_AEO_DIR` / `DEFAULT_UV_BIN`. Fall back to existing defaults when path derivation returns `None`.

### Update Steps (dynamic based on `targets`)

| Step ID | Label | Condition |
|---------|-------|-----------|
| `validate_ssh` | 校验 SSH 连接 | Always |
| `stop_daemon` | 停止 Worker Daemon | Always |
| `pull_aeo` | 更新 AEO 代码 | `targets` includes `"aeo"` |
| `sync_aeo` | 同步 AEO 依赖 (uv sync) | `targets` includes `"aeo"` |
| `pull_harbor` | 更新 Harbor 代码 | `targets` includes `"harbor"` |
| `restart_daemon` | 重启 Worker Daemon | Always |
| `wait_register` | 等待 Worker 注册 | Always |

Steps not in `targets` are omitted from `steps_json` entirely (not marked as skipped).

### Restart Daemon

Rebuild `controller_url` from worker DB fields:

- `connection_mode=direct` → `http://{controller_internal_ip}:{controller_port}`
- `connection_mode=tunnel` → re-establish tunnel if needed, then `http://127.0.0.1:{tunnel_remote_port}`

Pass worker's current `display_name`, `slots_total`, and derived paths to the refactored `build_daemon_start_command()`.

## API Contract

### `POST /api/workers/{workerId}/update`

**Request body (all optional):**

```json
{
  "targets": ["aeo", "harbor"]
}
```

| Field | Default | Validation |
|-------|---------|------------|
| `targets` | `["aeo", "harbor"]` | Array of `"aeo"` and/or `"harbor"`; at least one required |

**Preconditions**

- Worker must exist → `404`.
- Worker must have non-empty `ssh_host_alias` → `400` `{"error": "ssh_host_alias required"}`.
- Worker must have zero running and zero queued batches → `409`.
- No active update job for this worker (`status` in `pending`, `running`) → `409` `{"error": "update already in progress"}`.
- No active provision job for this worker → `409` `{"error": "provision in progress"}`.

**Success response `202`**

```json
{
  "jobId": "upd-xxx",
  "workerId": "ecs-worker-0001",
  "status": "pending",
  "targets": ["aeo", "harbor"]
}
```

### `GET /api/workers/update/{jobId}`

Returns job detail aligned with provision job format:

```json
{
  "jobId": "upd-xxx",
  "workerId": "ecs-worker-0001",
  "status": "running",
  "targets": ["aeo", "harbor"],
  "currentStep": "pull_aeo",
  "steps": [
    {"id": "validate_ssh", "label": "校验 SSH 连接", "status": "succeeded"},
    {"id": "stop_daemon", "label": "停止 Worker Daemon", "status": "succeeded"},
    {"id": "pull_aeo", "label": "更新 AEO 代码", "status": "running"}
  ],
  "logText": "...",
  "errorText": null,
  "createdAt": "...",
  "finishedAt": null
}
```

`status` values: `pending` | `running` | `succeeded` | `failed` | `cancelled`.

### `POST /api/workers/update/{jobId}/cancel`

- Mark job as `cancelled`.
- Call `Provisioner.decommission_worker()` to stop remote daemon.
- Do **not** restart daemon — operator must manually re-trigger update or provision.

**Success response `200`**

```json
{
  "ok": true,
  "jobId": "upd-xxx",
  "status": "cancelled"
}
```

### Remote Commands (executed via SSH)

```bash
# pull_aeo
cd {aeo_repo} && git pull

# sync_aeo
cd {aeo_repo} && {uv_bin} sync

# pull_harbor
cd {harbor_repo} && git pull
```

Any non-zero exit code fails the step and marks the job `failed`. On failure after `stop_daemon`, the daemon remains stopped — operator must retry or manually restart.

## Data Model

### New table `worker_update_jobs`

```sql
CREATE TABLE worker_update_jobs (
    job_id       TEXT PRIMARY KEY,
    worker_id    TEXT NOT NULL,
    targets_json TEXT NOT NULL,
    status       TEXT NOT NULL,
    current_step TEXT,
    steps_json   TEXT NOT NULL,
    log_text     TEXT NOT NULL DEFAULT '',
    error_text   TEXT,
    created_at   TEXT NOT NULL,
    finished_at  TEXT
);
```

### Store additions

```python
def create_worker_update_job(
    self, *, job_id: str, worker_id: str, targets: list[str], steps: list[dict]
) -> dict[str, Any]: ...

def get_worker_update_job(self, job_id: str) -> dict[str, Any] | None: ...

def get_latest_worker_update_job_for_worker(self, worker_id: str) -> dict[str, Any] | None: ...

def update_worker_update_job(self, job_id: str, **fields) -> dict[str, Any]: ...

def append_worker_update_log(self, job_id: str, chunk: str) -> None: ...
```

Worker list/detail APIs add `last_update_job_id` (same pattern as `last_provision_job_id`).

### WorkerUpdater class

New file: `src/agent_eval_orchestrator/controller/worker_updater.py`

```python
class WorkerUpdater:
    def __init__(self, *, store, ssh_config_path, auth_token, controller_port, provisioner): ...

    def initial_steps(self, targets: list[str]) -> list[dict[str, str]]: ...

    def start_job_async(self, **kwargs) -> None: ...

    def run_job(self, *, job_id, worker_id, targets, ssh_host_alias, ...) -> None: ...

    def cancel_job(self, job_id, *, worker_id, ssh_host_alias, connection_mode) -> None: ...
```

`WorkerUpdater` receives a reference to `Provisioner` to reuse `decommission_worker()`, `_establish_tunnel()`, `_wait_for_register()`, and `SshRunner`.

### Server routing

| Method | Path | Handler |
|--------|------|---------|
| POST | `/api/workers/{workerId}/update` | Create and start update job |
| GET | `/api/workers/update/{jobId}` | Poll job status |
| POST | `/api/workers/update/{jobId}/cancel` | Cancel running job |

Reserve `"update"` in worker path routing (alongside existing `"provision"`, `"runtime"`, etc.).

## UI Design

### Entry Point

Workers tab → Worker Detail panel. Add **更新代码** button in the actions row next to **保存配置** / **设为禁用** / **删除 Worker**.

### Button States

| Condition | State |
|-----------|-------|
| No `ssh_host_alias` | Disabled; tooltip: `需要 SSH 配置才能远程更新` |
| `runningCount > 0` or `queuedCount > 0` | Disabled; tooltip: `请先等待运行中的 batch 完成` |
| Update job in progress | Label: `更新中…`; click opens progress modal |
| Otherwise | Enabled |

### Update Modal

**Confirmation phase:**

- Checkboxes for AEO / Harbor (default both checked).
- Warning: `更新将停止 Worker Daemon 并重启，期间该 worker 无法领取新任务`.
- **开始更新** / **取消** buttons.

**Progress phase** (reuse provision modal pattern):

- Step list with current step highlighted.
- Live log area (poll `GET /api/workers/update/{jobId}` every 2s).
- **取消** button while running.

**Completion:**

- Success → **关闭** button; refresh worker list.
- Failure → show `errorText`; **重试** / **关闭** buttons.

### Post-Update Behavior

- On success, worker should re-register with updated `last_heartbeat_at`.
- No change to worker settings (slots, weight, tags, enabled state).

## Error Handling

| Scenario | Behavior |
|----------|----------|
| `git pull` conflict or failure | Step failed; job `failed`; daemon stays stopped |
| `uv sync` failure | Same as above |
| SSH connection timeout | `validate_ssh` step failed |
| Pull succeeds but restart fails | Job `failed`; operator retries |
| `wait_register` timeout (90s) | Job `failed`; error mentions remote log path |
| Cancel mid-job | Stop daemon; no pull; no restart |
| Tunnel mode with dead tunnel | Re-establish tunnel before restart (reuse `_establish_tunnel`) |
| Path derivation returns None | Step failed with clear error (e.g. `cannot derive aeo repo from sharedRoot`) |

Sensitive values in SSH log output are redacted using existing `redact_sensitive_log()`.

## Testing

| Test | Type | Assertion |
|------|------|-----------|
| `test_build_daemon_start_command_dynamic_paths` | Unit | Start command uses derived paths, not hardcoded defaults |
| `test_updater_initial_steps_aeo_only` | Unit | Steps omit harbor when targets=`["aeo"]` |
| `test_updater_initial_steps_harbor_only` | Unit | Steps omit aeo/sync when targets=`["harbor"]` |
| `test_update_worker_not_found` | Integration | Returns 404 |
| `test_update_worker_no_ssh` | Integration | Returns 400 |
| `test_update_worker_active_batches` | Integration | Returns 409 with counts |
| `test_update_worker_already_updating` | Integration | Returns 409 |
| `test_update_worker_success` | Integration | Mock SSH; steps succeed; job status `succeeded` |
| `test_update_worker_git_pull_failure` | Integration | Job `failed`; daemon not restarted |
| `test_update_worker_cancel` | Integration | Job `cancelled`; decommission called |

## Implementation Notes

- Refactor `build_daemon_start_command()` in `provisioner.py` to accept optional path parameters (`aeo_dir`, `uv_bin`, `log_dir`). Existing provision flow passes defaults; updater passes derived paths.
- `WorkerUpdater._resolve_paths(worker)` reads `capabilities.sharedRoot` from the worker record and calls `worker_paths` functions.
- Job ID prefix: `upd-` (via `new_id("upd")`).
- Delete worker should also cancel any active update job (mirror provision cancel behavior).
- No changes to worker protocol (register/claim/heartbeat) — daemon restart uses existing registration flow.
