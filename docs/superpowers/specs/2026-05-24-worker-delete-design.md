# Worker Delete Design

## Goal

Add a **Delete Worker** flow to the Controller dashboard and API. Operators can remove stale or decommissioned workers from the list, optionally stop remote daemon and SSH tunnel processes, and free the `worker_id` for reuse. ECS instances are **not** destroyed.

## Requirements Summary

| Dimension | Decision |
|-----------|----------|
| Primary purpose | Clean up worker list + remote decommission (stop daemon/tunnel) |
| ECS | Do not destroy cloud instances |
| Active batches | Block delete when worker has running or queued batches |
| SSH cleanup | Attempt when `ssh_host_alias` exists; skip otherwise with UI warning |
| `worker_id` reuse | Allowed after delete — hard-delete DB row, historical batches keep string references |

## Non-Goals

This feature will **not**:

- Destroy ECS instances or call cloud APIs.
- Delete historical `batches`, `case_runs`, or other eval artifacts.
- Remove remote directories (`harbor`, `runtime`, logs, etc.).
- Support bulk delete of multiple workers in one action.
- Require SSH for delete — workers without SSH alias can still be removed locally.

## Chosen Approach

**Synchronous delete with optional SSH cleanup** via `DELETE /api/workers/{workerId}`.

Alternatives considered:

| Approach | Verdict |
|----------|---------|
| Sync delete + optional SSH cleanup | **Chosen** — simple, matches existing `cancel_job` pattern, immediate feedback |
| Async decommission job | Rejected — over-engineered; complicates `worker_id` release timing |
| Soft delete (`status=removed`) | Rejected — conflicts with `worker_id` reuse; historical batches do not need the workers row |

## Architecture

```text
Dashboard (Worker Detail)
    │
    └─ DELETE /api/workers/{workerId}
           │
           ├─ 1. Validate worker exists
           ├─ 2. Validate runningCount=0 and queuedCount=0 → else 409
           ├─ 3. If provision_status=provisioning → cancel active provision job
           ├─ 4. Remote cleanup (optional):
           │      ├─ Has ssh_host_alias → kill_tunnel + SSH pkill daemon
           │      └─ No ssh_host_alias → skip, mark remoteCleanup=skipped
           ├─ 5. DB: DELETE workers row + DELETE provision_jobs for worker
           └─ 6. Response { ok, remoteCleanup, warnings? }
```

### Data Strategy

- **Hard delete** the `workers` row — worker disappears from all lists.
- **Keep** `batches.assigned_worker_id`, `preferred_worker_id`, and `runs.bound_worker_id` as historical string references. Task detail views continue to show the original `workerId` even after the worker row is gone.
- **Delete** all `provision_jobs` rows for the worker (no audit requirement; `worker_id` must be reusable).
- After delete, the same `worker_id` can be used again via `POST /api/workers/provision` or `POST /api/workers/register`.

### Remote Cleanup Scope

Reuse logic from `Provisioner.cancel_job()`:

1. **Controller side**: `TunnelManager.kill_tunnel(worker_id)` — SIGTERM the SSH reverse-tunnel process recorded in `tunnels.json`.
2. **Worker side** (via SSH): `pkill -f 'worker.daemon.*--worker-id {worker_id}' || true`

Not performed:

- Cloud instance termination
- Remote filesystem cleanup
- Stopping unrelated processes on the host

Extract a shared `Provisioner.decommission_worker(worker_id, ssh_host_alias)` method used by both provision cancel and worker delete.

## API Contract

### `DELETE /api/workers/{workerId}`

**Preconditions**

- Worker must exist → `404` if not found.
- Worker must have zero running and zero queued batches (including batches where this worker is `assigned_worker_id`, `preferred_worker_id`, or the run's `bound_worker_id`) → `409` if blocked.

**Processing order**

1. Load worker; return `404` if missing.
2. Compute active batch counts via shared runtime logic; return `409` if any running or queued.
3. If an active provision job exists (`status` in `pending`, `running`), cancel it using existing `cancel_job` flow.
4. Run remote cleanup when `ssh_host_alias` is non-empty.
5. Delete `workers` row and all `provision_jobs` for this `worker_id`.
6. Return success payload.

**Success response `200`**

```json
{
  "ok": true,
  "workerId": "ecs-worker-0004",
  "remoteCleanup": "done"
}
```

`remoteCleanup` values:

| Value | Meaning |
|-------|---------|
| `done` | SSH cleanup attempted and no fatal errors |
| `skipped` | No `ssh_host_alias`; DB delete only |
| `partial` | One or more cleanup steps failed; DB delete still completed |

When `remoteCleanup` is `partial`, include:

```json
{
  "warnings": ["failed to kill tunnel: ...", "ssh pkill failed: ..."]
}
```

**Error responses**

| Status | Scenario | Body |
|--------|----------|------|
| `404` | Worker not found | `{"error": "worker not found"}` |
| `409` | Active batches | `{"error": "worker has active batches", "runningCount": 1, "queuedCount": 2}` |
| `500` | DB failure | `{"error": "..."}` |

### Store Additions

```python
def worker_has_active_batches(self, worker_id: str) -> dict[str, int]:
    """Return {runningCount, queuedCount} using same rules as list_worker_runtime_status."""

def delete_worker(self, worker_id: str) -> bool:
    """Hard-delete workers row and provision_jobs. Return False if worker not found."""
```

### Provisioner Addition

```python
def decommission_worker(
    self,
    *,
    worker_id: str,
    ssh_host_alias: str | None,
) -> dict[str, object]:
    """Kill tunnel and remote daemon. Return {remoteCleanup, warnings}."""
```

### Server Routing

Add `do_DELETE` handler (or route within existing handler) for `DELETE /api/workers/{workerId}`. Require auth token same as other mutating endpoints.

## UI Design

### Entry Point

Workers tab → Worker Detail panel. Add a red **删除 Worker** button in the actions row next to **保存配置** / **设为禁用**.

### Confirmation Modal

- **Title**: `删除 Worker "{displayName}"？`
- **Body** (with SSH alias): `将停止远程 daemon 和 SSH 隧道，并从列表移除。ECS 实例不会被销毁。`
- **Body** (without SSH alias): `该 worker 无 SSH 配置，仅会从 controller 移除，不会执行远程清理。`
- **Provisioning note**: If `provision_status=provisioning`, add: `将取消进行中的部署任务。`
- **Confirm** / **取消** buttons.

### Disabled State

When `runningCount > 0` or `queuedCount > 0`, the delete button is disabled with hint: `请先等待或停止运行中的 batch`.

### Post-Delete Behavior

- Close modal, clear `selectedWorkerId`, reload dashboard.
- Toast messages:
  - `remoteCleanup=skipped` → `Worker 已删除（未执行远程清理）`
  - `remoteCleanup=partial` → `Worker 已删除，远程清理部分失败` (show warnings if present)
  - otherwise → `Worker 已删除`

### Edge Cases

| State | Behavior |
|-------|----------|
| `provision_status=provisioning` | Delete allowed; auto-cancel provision job first |
| `provision_status=failed` | Delete allowed; normal cleanup path |
| Manually registered worker (no SSH) | Delete allowed; `remoteCleanup=skipped` |
| Worker offline with no active batches | Delete allowed |

## Error Handling

- **Remote cleanup failure does not block DB delete.** Operator goal is list cleanup; warnings surface via `partial` response.
- SSH connect timeout: 10 seconds (match existing `ConnectTimeout=10`).
- `pkill` finding no process: treat as success (`|| true`).
- Race with daemon re-register after delete: daemon may INSERT a new workers row. Acceptable; operator can delete again or stop the remote process manually.

## Testing

| Test | Assertion |
|------|-----------|
| `test_delete_worker_not_found` | Returns 404 |
| `test_delete_worker_with_running_batch` | Returns 409 with counts |
| `test_delete_worker_with_queued_batch` | Returns 409 with counts |
| `test_delete_worker_success_no_ssh` | Row deleted, `remoteCleanup=skipped` |
| `test_delete_worker_success_with_ssh` | Mock SSH; decommission called |
| `test_delete_worker_cancels_provision_job` | Active provision cancelled before delete |
| `test_delete_worker_id_reusable` | Same ID can provision after delete |
| `test_historical_batch_keeps_worker_id` | Batch detail still shows original workerId |

## Implementation Notes

- Refactor tunnel kill + remote pkill from `Provisioner.cancel_job` into `decommission_worker` to avoid duplication.
- `worker_has_active_batches` should delegate to the same batch-to-worker assignment logic in `list_worker_runtime_status` rather than duplicating rules.
- No schema migration required — uses existing tables and columns.
