# Task Asset Sync Design

## Goal

When creating a Harbor evaluation task, operators specify a **dataset directory**, **bitfun-cli binary**, and **bitfun config directory** on the Controller host. The Controller distributes only the assets each worker needs — per-worker case shards plus bitfun-cli — so workers do not require pre-deployed datasets or agent binaries.

Sync runs as an **async job** after task creation. Batches remain unclaimable until their worker's sync completes. Synced assets are stored in a **per-run isolated directory** on each worker and **automatically cleaned up** when the run reaches a terminal state.

## Non-Goals

This feature will **not**:

- Replace Harbor dataset download on the Controller (operators still prepare datasets on the Controller).
- Implement cross-run content-hash deduplication or long-lived worker caches.
- Sync Harbor repo, `uv`, or Docker images to workers (workers still need Harbor installed via bootstrap).
- Add automatic re-sync or retry UI for failed sync jobs (operator must create a new run or intervene manually).
- Change the worker claim protocol beyond new batch statuses (`pending_sync`, `sync_failed`).

## Requirements Summary

| Decision | Choice |
|----------|--------|
| Dataset sync granularity | Per-worker shard only — each worker receives the case subdirectories it will execute |
| bitfun-cli specification | Task-level: `bitfunCliPath` + `bitfunConfigDir` on Controller |
| Sync timing | Async job; batches start as `pending_sync`, become `queued` after sync |
| Dataset path input | Free-form Controller path (not preset dropdown only) |
| Same-machine workers | Local `copytree` / rsync; no SSH required |
| Remote workers without SSH | Reject at task creation (400) |
| Worker destination layout | Per-run isolation: `{sharedRoot}/sync/{runId}/` |
| Post-run cleanup | Auto-delete sync directories when run reaches terminal state |

## Chosen Approach

Use a **Run-level AssetSyncJob** (mirroring the existing Provision Job pattern): one background job per run with per-worker steps. Reuse OpenSSH (`ssh`, `scp`, `rsync`) infrastructure from `provisioner.py`.

Alternatives considered:

| Approach | Verdict |
|----------|---------|
| Run-level Sync Job | **Chosen** — matches Provision Job UX; bitfun-cli transferred once per worker; clear status |
| Batch-level lazy sync | Rejected — duplicates bitfun-cli; conflicts with async-at-creation model |
| Worker cache + manifest | Rejected — conflicts with per-run isolation and auto-cleanup |

## Architecture

```text
Create Task UI
    │  datasetPath, bitfunCliPath, bitfunConfigDir, workerIds, caseIds
    ▼
POST /api/eval-tasks/create-and-distribute
    ├─ Validate paths on Controller; reject remote workers without ssh_host_alias
    ├─ create_sharded_batches → status = pending_sync
    ├─ Write sync_manifest_json on run
    └─ Start AssetSyncJob (background thread) → return 201 immediately

AssetSyncer (controller/asset_syncer.py)
    For each worker (parallel threads):
      1. sync_cases  — rsync/copy only that worker's case subdirs
      2. sync_bitfun — rsync/copy bitfun-cli binary + config dir
    Transport:
      - Local worker  → shutil.copytree / local rsync
      - Remote worker → rsync/scp over SSH (reuse provisioner SSH resolution)

On worker sync success:
    - Update template executor_config paths for that worker
    - That worker's batches: pending_sync → queued

Worker claim:
    - Only batches with status = queued are claimable

On run terminal state:
    - CleanupJob deletes {sharedRoot}/sync/{runId}/ on each worker
```

### Worker filesystem layout

```text
{workerSharedRoot}/sync/{runId}/
  dataset/
    case-id-1/
    case-id-2/
  bitfun/
    bitfun-cli       # executable
    config/          # contents of bitfunConfigDir
```

### Local worker detection

```python
def is_local_worker(worker: dict, controller_shared_root: Path) -> bool:
    caps = worker.get("capabilities") or {}
    if caps.get("localToController") is True:
        return True
    shared_root = str(caps.get("sharedRoot") or "").strip()
    if not shared_root:
        return False
    return Path(shared_root).expanduser().exists()
```

- **Local worker**: `transport = local`; `ssh_host_alias` not required.
- **Remote worker without `ssh_host_alias`**: task creation returns 400.

## API Changes

### `POST /api/eval-tasks/create-and-distribute`

**Request body** (changed fields marked):

```json
{
  "name": "swe-bench-bitfun-run",
  "datasetPath": "/root/projects/agent-eval-orchestrator/datasets/swe-bench-verified",
  "bitfunCliPath": "/root/projects/BitFun/target/release/bitfun-cli",
  "bitfunConfigDir": "/root/.config/bitfun",
  "workerIds": ["remote-a", "local-a"],
  "selectedCaseIds": ["django__django-10097"],
  "jobsDir": "/root/projects/harbor/jobs",
  "executorConfig": {
    "nConcurrent": 2
  }
}
```

`datasetRef` is replaced by `datasetPath`. `bitfunCliPath` and `bitfunConfigDir` are required.

**Validation (synchronous, before job start):**

| Field | Rule |
|-------|------|
| `datasetPath` | Must exist on Controller; must contain case subdirectories |
| `bitfunCliPath` | Must exist on Controller; must be executable |
| `bitfunConfigDir` | Must exist on Controller; must be a directory |
| `workerIds` | Non-empty; each remote worker must have non-empty `ssh_host_alias` |
| `selectedCaseIds` | Optional; if empty, enumerate all case dirs under `datasetPath` |

**Response (201):**

```json
{
  "template": { "...": "..." },
  "run": {
    "runId": "run-xxx",
    "syncStatus": "pending"
  },
  "batches": [
    { "batchId": "batch-xxx", "status": "pending_sync", "preferredWorkerId": "remote-a" }
  ],
  "syncJobId": "sync-xxx"
}
```

### New query endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/runs/{runId}/sync` | Sync job status, per-worker steps, log tail |
| `GET` | `/api/sync-jobs/{jobId}` | Lookup by job ID (symmetric with provision jobs) |

## Data Model

### `runs` table — new columns

| Column | Type | Description |
|--------|------|-------------|
| `sync_status` | TEXT | `pending` / `running` / `succeeded` / `failed` / `cleaning` / `cleaned` |
| `sync_job_id` | TEXT | FK to `asset_sync_jobs.job_id` |
| `sync_manifest_json` | TEXT | Source paths, per-worker case lists, target paths |

### `batches.status` — new values

| Status | Meaning |
|--------|---------|
| `pending_sync` | Waiting for asset sync on assigned worker |
| `sync_failed` | Sync failed for this worker's shard; not claimable |
| `queued` | Sync complete; worker may claim (existing) |

### New table `asset_sync_jobs`

```sql
CREATE TABLE asset_sync_jobs (
    job_id       TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL,
    status       TEXT NOT NULL,
    current_step TEXT,
    steps_json   TEXT NOT NULL,
    log_text     TEXT NOT NULL DEFAULT '',
    error_text   TEXT,
    created_at   TEXT NOT NULL,
    finished_at  TEXT
);
```

`steps_json` structure — one entry per worker, two sub-steps each:

```json
[
  {
    "workerId": "remote-a",
    "steps": [
      { "id": "sync_cases", "label": "同步 dataset case", "status": "pending" },
      { "id": "sync_bitfun", "label": "同步 bitfun-cli", "status": "pending" }
    ]
  }
]
```

### `sync_manifest_json` example

```json
{
  "datasetPath": "/root/projects/agent-eval-orchestrator/datasets/swe-bench-verified",
  "bitfunCliPath": "/root/projects/BitFun/target/release/bitfun-cli",
  "bitfunConfigDir": "/root/.config/bitfun",
  "workers": {
    "remote-a": {
      "caseIds": ["django__django-10097", "django__django-11001"],
      "targetRoot": "/home/djn/worker/agent-eval-orchestrator/runtime/sync/run-xxx",
      "transport": "ssh",
      "sshHostAlias": "aeo-ecs-0004"
    },
    "local-a": {
      "caseIds": ["django__django-11019"],
      "targetRoot": "/root/projects/agent-eval-orchestrator/runtime/sync/run-xxx",
      "transport": "local"
    }
  }
}
```

Case IDs per worker come from `create_sharded_batches` output (weighted shard assignment).

### Executor config after sync

Update `task_templates.executor_config_json` (or run-level override) with resolved worker paths:

```json
{
  "datasetPathByWorker": {
    "remote-a": "/home/djn/worker/agent-eval-orchestrator/runtime/sync/run-xxx/dataset"
  },
  "mountsByWorker": {
    "remote-a": [
      {
        "type": "bind",
        "source": "/home/djn/worker/.../sync/run-xxx/bitfun/bitfun-cli",
        "target": "/usr/local/bin/bitfun-cli"
      },
      {
        "type": "bind",
        "source": "/home/djn/worker/.../sync/run-xxx/bitfun/config",
        "target": "/testbed/.config/bitfun"
      }
    ]
  },
  "agentEnvByWorker": {
    "remote-a": { "XDG_CONFIG_HOME": "/testbed/.config" }
  }
}
```

Remove hardcoded `_default_bitfun_mounts` / `_map_dataset_for_worker` path inference for runs that use asset sync. Legacy path mapping may remain as fallback for runs created before this feature.

## Sync Job State Machine

### Per-worker steps

```text
AssetSyncJob (run-xxx)
  ├─ worker: remote-a
  │    ├─ sync_cases   → rsync case subdirs to targetRoot/dataset/
  │    └─ sync_bitfun  → scp binary + rsync config to targetRoot/bitfun/
  └─ worker: local-a
       ├─ sync_cases   → shutil.copytree locally
       └─ sync_bitfun  → local copy
```

Workers run in parallel (one thread each). Within a worker, `sync_cases` then `sync_bitfun` run sequentially.

### State transitions

```text
Task created
  batches → pending_sync
  run.sync_status → pending
       ↓
AssetSyncJob starts
  run.sync_status → running
       ↓
Worker W both steps succeed
  → W's batches: pending_sync → queued
  → executor_config updated for W
       ↓
All workers succeed
  run.sync_status → succeeded
       ↓
Run reaches terminal state (succeeded / failed / stopped)
  run.sync_status → cleaning
  → delete targetRoot on each worker
  run.sync_status → cleaned
```

### Failure handling (partial success)

If any worker fails:

- `run.sync_status = failed`
- Failed worker's batches → `sync_failed`
- Already-synced workers' batches remain `queued` and may execute
- `error_text` on sync job records the failing worker and step
- Operator must investigate logs; no automatic retry in v1

### Transfer commands

| Scenario | Command |
|----------|---------|
| Remote cases | `rsync -az -e "ssh -F {config}" {src}/{caseId}/ {alias}:{target}/dataset/{caseId}/` |
| Remote bitfun binary | `scp -F {config} {bitfunCliPath} {alias}:{target}/bitfun/bitfun-cli` |
| Remote bitfun config | `rsync -az -e "ssh -F {config}" {bitfunConfigDir}/ {alias}:{target}/bitfun/config/` |
| Local | `shutil.copytree` with `dirs_exist_ok=True`; preserve executable bit on binary |

Extract shared SSH helpers from `provisioner.py` into `controller/ssh_runner.py` to avoid duplication.

### Cleanup

Triggered when run status becomes terminal (same detection as existing run summary):

1. Read `sync_manifest_json` for each worker's `targetRoot`
2. Local worker → `shutil.rmtree(targetRoot, ignore_errors=True)`
3. Remote worker → `ssh -F {config} {alias} rm -rf {targetRoot}`
4. Cleanup failure → log warning only; do not block run archival

## UI Changes

### Create Task form

| Before | After |
|--------|-------|
| `Dataset Ref` dropdown (preset) | `Dataset Path` text input (Controller absolute path) |
| (implicit bitfun paths) | `BitFun CLI Path` text input (required) |
| (implicit bitfun paths) | `BitFun Config Dir` text input (required) |
| Subtitle: paths inferred by controller | Subtitle: assets synced to workers after creation |

### Post-submit sync progress

Reuse Provision Job polling pattern:

- Show sync job steps per worker with status badges
- Poll `GET /api/runs/{runId}/sync` every 2–3 seconds until terminal
- On failure, show error and link to log tail

### Run detail

Add sync status badge: `syncing` / `ready` / `sync_failed`.

## Error Handling

| Error | Behavior |
|-------|----------|
| `datasetPath` not found | 400 at creation |
| Case dir missing for a selected case ID | 400 at creation |
| Remote worker missing `ssh_host_alias` | 400 at creation |
| SSH auth failure during sync | Worker step fails; run.sync_status = failed |
| Disk full on worker | rsync/scp fails; step marked failed |
| Cleanup SSH failure | Warning log; run archival continues |

## Testing

| Scenario | Expected |
|----------|----------|
| Remote worker without SSH alias selected | 400 on create |
| Local worker (sharedRoot exists on Controller FS) | Local copy; batches → queued |
| Two workers, 6 cases | Each worker target contains only its shard cases |
| Claim during `pending_sync` | Returns null |
| Claim after sync | Harbor executor uses synced paths and mounts |
| Run terminal | `targetRoot` removed on all workers |
| Partial sync failure | Failed worker batches = sync_failed; others queued |

## Module Layout

```text
src/agent_eval_orchestrator/controller/
  asset_syncer.py      # AssetSyncJob runner (new)
  ssh_runner.py        # Shared ssh/scp/rsync helpers (extracted from provisioner)
  server.py            # API changes, start sync job on create
  static.py            # Form + sync progress UI

src/agent_eval_orchestrator/storage/
  store.py             # Migrations, sync job CRUD, batch status transitions

tests/controller/
  test_asset_syncer.py
  test_create_task_sync_api.py
```

## Relationship to Worker Provision

Worker Provision UI ([2026-05-24-worker-provision-ui-design.md](./2026-05-24-worker-provision-ui-design.md)) explicitly deferred dataset/bitfun sync. This feature completes that gap. Provisioned workers already store `ssh_host_alias`, which AssetSyncer uses for remote transfer.
