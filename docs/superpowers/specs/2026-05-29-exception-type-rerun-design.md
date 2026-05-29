# Exception Type Display and Selective Rerun Design

## Goal

On the Task detail page, distinguish exception cases by their **exception type** (sourced from Harbor trial metadata) and allow operators to rerun a **multi-selected subset of exception types** at task scope. The existing **重跑 Exception** button opens the configuration modal with all types selected by default; operators deselect unwanted types before confirming.

## Non-Goals

This feature will **not**:

- Support individual case checkbox selection (type-level bulk selection only).
- Introduce custom type aliases or grouped categories (e.g. merging all timeouts into one bucket).
- Add a persistent exception-type panel outside the rerun modal (task detail stays compact).
- Re-read trial metadata directly from the jobs directory at request time (uses already-normalized DB records).
- Change worker assignment, merge semantics, or concurrent-rerun rules.
- Rerun non-exception cases (`failed` without `error_text`, succeeded, etc.).

## Requirements Summary

| Decision | Choice |
|----------|--------|
| Type source | `{jobsDir}/{batch_id}/{trial}/result.json` → `exception_info.exception_type` |
| Storage path | Normalized via `normalize_harbor_job()` → `case_runs.metrics_json.errorType` |
| Missing type | Group as `(unknown)` |
| Selection granularity | Exception **type** only (multi-select) |
| Selection scope | **Task-level** across all workers; rerun still assigns cases to original workers |
| UI entry | Existing **重跑 Exception** button |
| Default selection | All types checked when modal opens |
| Case card badge | Show concrete `errorType` instead of generic `exception` |
| API filter | `selectedErrorTypes: string[]` on `POST /api/runs/{runId}/rerun-exceptions` |
| Backward compatibility | Omitting `selectedErrorTypes` = all types (same as today) |

## Chosen Approach

**Extend rerun config modal + API type filter** (recommended over a standalone type panel or CLI-only backend).

1. `GET /api/eval-tasks/{runId}` returns `exceptionSummary.byType` for the modal checklist.
2. Case cards display `errorType` in the status badge.
3. Rerun modal adds a type multi-select section (default all checked) above existing config fields.
4. `RunRerunCoordinator.start_rerun()` filters exception cases by `selectedErrorTypes` before worker grouping.

Alternatives considered:

| Approach | Verdict |
|----------|---------|
| Extend modal + API filter | **Chosen** — minimal change, reuses existing rerun lifecycle |
| Persistent type panel on task detail | Rejected — duplicates modal; larger UI scope |
| API/CLI only, UI later | Rejected — does not meet UI requirement |

## Data Model

### Type extraction

Add a shared helper (Store static method or small util used by Store + coordinator):

```python
def case_error_type(case: dict[str, Any]) -> str:
    metrics = case.get("metrics") or {}
    raw = case.get("errorType") or metrics.get("errorType")
    if raw is None or str(raw).strip() == "":
        return "(unknown)"
    return str(raw).strip()
```

Rules:

- Only cases where `_case_is_errored(case)` is true participate in exception summary and rerun filtering.
- Type strings are stored and displayed verbatim from trial metadata (e.g. `RewardFileNotFoundError`).
- `(unknown)` is a UI/API sentinel for errored cases without `exception_type` in trial metadata.

### Exception summary aggregation

New method: `Store.summarize_exception_types_for_run(run_id: str) -> dict[str, Any]`

```json
{
  "total": 14,
  "byType": [
    { "errorType": "RewardFileNotFoundError", "count": 8 },
    { "errorType": "TimeoutError", "count": 5 },
    { "errorType": "(unknown)", "count": 1 }
  ]
}
```

- `byType` sorted by descending `count`, then ascending `errorType`.
- `total` equals sum of `byType[].count`.

Extend `get_eval_task_detail()` to include `exceptionSummary` from this method.

## API

### `GET /api/eval-tasks/{runId}` extension

Add top-level field:

```json
{
  "exceptionSummary": {
    "total": 14,
    "byType": [{ "errorType": "RewardFileNotFoundError", "count": 8 }]
  },
  "exceptionCount": 14,
  "canRerunExceptions": true
}
```

`exceptionCount` remains the total exception case count (unchanged semantics).

### `POST /api/runs/{runId}/rerun-exceptions` extension

Request body adds optional field:

```json
{
  "selectedErrorTypes": ["RewardFileNotFoundError", "TimeoutError"],
  "datasetPath": "/path/to/dataset",
  "executorConfig": { "nConcurrent": 2 }
}
```

| Condition | Response |
|-----------|----------|
| `selectedErrorTypes` omitted | Treat as all types currently in `exceptionSummary.byType` |
| `selectedErrorTypes: []` | **400** — at least one type required |
| Valid types but zero matching cases | **400** — no matching exception cases |
| Unknown type strings (not in current exceptions) | **400** — invalid error type(s): … |
| Run not finished | **409** (unchanged) |
| Rerun in progress | **409** (unchanged) |

Response `201` adds optional audit field:

```json
{
  "rerunJobId": "rerun-abc123",
  "rerunStatus": "syncing",
  "exceptionCount": 8,
  "selectedErrorTypes": ["RewardFileNotFoundError"],
  "workerShards": { "ecs-worker-0001": 3, "ecs-worker-0002": 5 }
}
```

`exceptionCount` in the response reflects **filtered** case count, not task-wide total.

### Rerun job persistence

Extend `run_rerun_jobs` with optional column `selected_error_types_json TEXT` (migration). Store the resolved type list at job creation for audit/debug.

## Backend Flow

```text
POST /rerun-exceptions { selectedErrorTypes, ...config }
    │
    ├─ Validate run finished, no in-progress rerun
    ├─ all_exceptions = list_exception_cases_for_run(run_id)
    ├─ Resolve types: omitted → all distinct case_error_type values
    ├─ filtered = [e for e in all_exceptions if case_error_type(e.case) in selected_set]
    ├─ 400 if filtered empty
    ├─ grouped = group filtered cases by worker_id
    ├─ Create rerun batches + sync (existing path)
    └─ Record selected_error_types_json on run_rerun_jobs
```

Changes to existing code:

| File | Change |
|------|--------|
| `storage/store.py` | `case_error_type()`, `summarize_exception_types_for_run()`, filter helper, migration, `get_eval_task_detail()` extension |
| `run_rerun_coordinator.py` | Accept and apply `selectedErrorTypes` before `_resolve_worker_shards` |
| `server.py` | Parse/validate `selectedErrorTypes` in rerun POST handler |
| `static.py` | Badge + modal UI (embedded HTML/JS) |

Merge, asset sync, and heartbeat merge paths are unchanged.

## UI Design

### Case card badge

For errored cases (`caseIsErrored(item)`):

- Badge text: `caseErrorType(item)` — concrete type or `(unknown)`.
- CSS: truncate long names with ellipsis; full string in `title` attribute.
- Color: `(unknown)` → `warn`; other types → `err` (existing purple).

Non-exception statuses unchanged.

### Rerun configuration modal

Insert a section **above** the existing summary stats and config form:

**Exception 类型（Task 级）**

- Checkbox per entry in `exceptionSummary.byType`.
- Label format: `{errorType} ({count})`.
- **Default: all checked** on modal open.
- Quick actions: **全选** / **全不选**.
- Live footer: `已选 N / 共 M cases` (N = sum of counts for checked types).
- Disable **确认重跑** when N = 0 or while submitting.

Existing read-only summary updates:

- Subtitle: `{taskName} · 已选 {N} / exception: {M}`.

On submit, include `selectedErrorTypes` (checked type strings) in POST body alongside existing config fields.

### Task detail button

- **重跑 Exception** button: unchanged placement and enable rules (`canRerunExceptions`).
- Click opens modal with types + config (not immediate POST).

## Error Handling

| Scenario | Behavior |
|----------|----------|
| Exception case lacks `exception_type` in trial JSON | Shown and selectable as `(unknown)` |
| Type present in DB but trial dir deleted | Type still shown from DB; rerun uses stored case metadata |
| Partial rerun succeeds, some types remain | User reopens modal; summary reflects remaining types/counts |
| All types deselected | Confirm button disabled client-side; server rejects empty array if bypassed |

## Testing

| Area | Tests |
|------|-------|
| `case_error_type()` | Known type, missing type → `(unknown)`, metrics-only path |
| `summarize_exception_types_for_run()` | Counts, sort order, empty run |
| Coordinator | Filter by types, omitted = all, empty array 400, invalid type 400 |
| API | POST with subset, response `exceptionCount` matches filtered set |
| Regression | `{}` body still reruns all exceptions |

Manual verification:

1. Task with multiple exception types shows distinct badges on case cards.
2. Modal opens with all types checked; deselecting one type reduces N in footer.
3. Confirm rerun only queues cases of selected types on correct workers.

## Relationship to Prior Specs

This spec **extends** [2026-05-27-rerun-exception-config-design.md](./2026-05-27-rerun-exception-config-design.md) and [2026-05-26-run-exception-rerun-design.md](./2026-05-26-run-exception-rerun-design.md):

- Supersedes the non-goal "no manual case subset selection" with **type-level** subset selection.
- Keeps configurable rerun parameters, modal pattern, and merge/sync architecture unchanged.
