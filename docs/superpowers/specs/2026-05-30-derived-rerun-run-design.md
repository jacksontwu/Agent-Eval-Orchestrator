# Derived Rerun Run Design

## Goal

Refactor exception rerun so each rerun creates a new, normal, visible run instead of mutating the original run. The original run stays an immutable baseline: no rerun status updates, no case merge writes, and no Harbor jobs directory rewrites. The new run records where it came from, starts from a copied snapshot of the original run, reruns selected exception cases, and owns the final merged results.

## Non-Goals

This design will not:

- Hide rerun runs from the run list or task detail UI.
- Add a reverse pointer such as `latest_rerun_run_id` to the original run.
- Merge final Harbor artifacts back into the original run's jobs directory.
- Change exception type selection semantics.
- Support concurrent reruns from the same original run beyond the current single active-rerun guard unless explicitly changed later.

## Requirements Summary

| Decision | Choice |
|----------|--------|
| Rerun unit | A new normal run visible in UI/list |
| Lineage | New run has `parent_run_id = original_run_id` |
| Original run writes | None during rerun creation, execution, or final merge |
| Initial new run state | Full copy of original run primary batches, case runs, summaries, and Harbor job artifacts |
| Execution batches | `exception_rerun` batches under the new run |
| Merge target | New run's cloned primary batches and copied jobs directory |
| Final result | New run is a self-contained complete result run |
| Jobs directory | New run-owned path under its archive directory |

## Chosen Approach

Use a complete derived run model.

When operators trigger exception rerun from an original run, the controller creates a new task template and run. The new run has `parent_run_id` pointing to the original run. Controller clones original primary batches and their case rows into the new run, copies original Harbor jobs into a new run-owned jobs directory, deletes selected exception trial directories from the copied jobs, then creates rerun batches for only the selected exceptions.

Rerun execution and asset sync happen only for the new run. When rerun batches complete, controller merges their results into the new run's cloned primary batches and new jobs directory. The original run remains unchanged.

Alternatives considered:

| Approach | Verdict |
|----------|---------|
| Complete derived run | Chosen. The new run is self-contained, visible, auditable, and exportable. |
| Rerun subset run with dynamic parent overlay | Rejected. UI, stats, export, and Harbor viewer would depend on joining two runs at read time. |
| Rerun subset DB with full merged artifact only | Rejected. DB and artifact state would disagree, making task detail and stats fragile. |

## Data Model

### `runs`

Add:

| Column | Type | Description |
|--------|------|-------------|
| `parent_run_id` | TEXT NULL | Original run ID when this run was created by rerun; NULL for normal runs |

`parent_run_id` is a one-way lineage pointer. The original run does not store a reverse pointer.

Existing `rerun_status` and `rerun_job_id` continue to describe the currently viewed run's rerun workflow. For a derived rerun run, these fields are set on the new run only.

### `run_rerun_jobs`

Continue to use `run_rerun_jobs`, but store jobs against the derived run:

- `run_id` is the new derived run ID.
- Do not add a duplicate parent pointer to this table; derive lineage from `runs.parent_run_id`.
- `rerun_batches_json` maps workers to rerun batch IDs under the new run.
- `selected_error_types_json` remains unchanged.

### Batches

The new run contains:

- cloned primary batches copied from the original run;
- new `exception_rerun` batches for selected exception cases.

Each `exception_rerun.parent_batch_id` points to the cloned primary batch in the new run, not the original batch.

## API

### `POST /api/runs/{runId}/rerun-exceptions`

`runId` is the original run ID.

Behavior:

1. Validate the original run exists.
2. Validate original primary batches are terminal.
3. Validate no active derived rerun run for the original run has `rerun_status` in `syncing` or `running`.
4. Resolve selected exception types and cases using original run data.
5. Create the derived run.
6. Return the new run ID and rerun job status.

Response:

```json
{
  "runId": "run-derived",
  "parentRunId": "run-original",
  "rerunJobId": "rerun-abc123",
  "rerunStatus": "syncing",
  "exceptionCount": 8,
  "selectedErrorTypes": ["TimeoutError"],
  "workerShards": {
    "worker-a": 5,
    "worker-b": 3
  }
}
```

Existing clients that only need `rerunJobId`, `rerunStatus`, `exceptionCount`, and `workerShards` can continue to work, but the UI should navigate or offer a link to the new run.

### `GET /api/eval-tasks/{runId}`

Include lineage:

```json
{
  "run": {
    "run_id": "run-derived",
    "parent_run_id": "run-original"
  },
  "parentRun": {
    "runId": "run-original",
    "name": "baseline run"
  }
}
```

For normal runs, `parent_run_id` is null and `parentRun` is omitted.

### Run List

Derived rerun runs appear as normal runs. UI can show a small "rerun of ..." lineage indicator using `parent_run_id`.

## Derived Run Creation Flow

`RunRerunCoordinator.start_rerun(original_run_id, config)` changes from "create rerun batches on the original run" to "create a derived run and rerun batches under it".

Steps:

1. Load original run and template.
2. Resolve exception cases and selected error types from original run case rows.
3. Resolve worker shards using original batch worker assignment and dataset case ID rules.
4. Create a copied task template:
   - start from original template fields;
   - apply submitted rerun dataset/executor config only to the copied template;
   - set copied executor config `combinedJobsDir` to the new run-owned jobs directory.
5. Create the derived run with `parent_run_id = original_run_id`.
6. Clone original primary batches into the derived run:
   - copy selected case IDs, preferred/assigned worker, batch options, summaries, executor metadata, artifact indexes, and finished status;
   - copy `case_runs` from original batch to cloned batch;
   - keep a mapping of `original_batch_id -> cloned_batch_id`.
7. Copy Harbor artifacts from the original run into the derived run jobs area.
8. Delete selected exception trial directories from the copied jobs area.
9. Create `exception_rerun` batches under the derived run, grouped by worker; each points to the cloned parent batch.
10. Create `run_rerun_jobs` row for the derived run and update the derived run's rerun fields.
11. Start scoped asset sync for the derived run.

Creation should be transactional for DB rows. Artifact copy happens after DB validation and before rerun execution; if artifact copy fails, mark the derived run/rerun job failed rather than modifying the original run.

## Jobs Directory Strategy

The new run owns its jobs directory:

```text
{layout.archives}/{owner}/runs/{newRunId}/harbor/jobs
```

This path becomes the copied template's `executor_config.combinedJobsDir`.

The controller copies the original run's effective combined jobs into this new directory. If the original run also has imported jobs under controller storage or batch-local jobs under batch roots, copy the corresponding source artifacts into the new run's equivalent locations so existing normalizers and Harbor viewer rebuild paths can operate without reading from the original run.

After copying, delete only selected exception trial directories from the new run's copied jobs. Matching should reuse the current trial case ID logic (`task_name` first, then trial name stem) so truncated or suffixed Harbor trial names are handled consistently.

The original jobs directory is never deleted, edited, or rebuilt by rerun.

## Execution and Merge Flow

Workers claim and execute only the derived run's `exception_rerun` batches.

When a rerun batch finishes:

1. Store the rerun batch's own result, cases, executor metadata, and artifact index.
2. Merge rerun cases into the cloned primary parent batch in the derived run.
3. Recompute the cloned primary batch summary.
4. Copy rerun trial directories into the derived run's copied jobs directory, replacing matching stale trials there.
5. Rebuild the derived run's combined Harbor job/result.
6. Mark the rerun batch complete and update the derived run's rerun job when all rerun batches are terminal.

If a rerun case still fails or errors, the new failed/error case replaces the copied baseline exception case for that trial. If a rerun batch fails before producing cases, the copied baseline cases remain in the cloned primary batch and the rerun batch records the failure.

## State Rules

- Original run:
  - no `rerun_status` update;
  - no `rerun_job_id` update;
  - no case row update;
  - no batch summary update;
  - no jobs directory update.

- Derived run:
  - visible as a normal run;
  - has `parent_run_id`;
  - owns rerun status/job fields;
  - owns copied primary result state;
  - owns rerun batches and final merged artifacts.

For list/detail status, primary cloned batches remain terminal from the baseline. Rerun workflow progress is shown via derived run `rerun_status` and rerun batch rows.

## UI

Triggering rerun from the original run returns the derived run ID. UI should either navigate to the derived run detail or show a clear link to it.

Run list and task detail should display lineage for derived runs:

- "Rerun of `<parent run name>`" in a compact metadata location.
- Parent run detail can remain unchanged; no reverse list is required for this design.

Existing exception type selection UI stays the same, but it now creates a new run instead of mutating the current run.

## Error Handling

| Scenario | Behavior |
|----------|----------|
| Original run not terminal | Reject with 409 |
| No matching exception cases | Reject with 400 |
| Invalid selected error type | Reject with 400 |
| Artifact copy fails | Derived run/rerun job marked failed; original run unchanged |
| Asset sync fails | Derived run rerun status failed; copied baseline remains visible |
| Partial rerun failure | Successful rerun cases merge into derived run; failed rerun batches remain as audit; original run unchanged |
| Harbor rebuild fails | Record failure on derived run/rerun job; original jobs untouched |

## Testing

Add or update tests for:

- `runs.parent_run_id` schema and serialization.
- Rerun creation creates a visible new run with `parent_run_id`.
- Original run rows are unchanged after rerun creation and completion.
- Original jobs directory remains byte-for-byte or entry-for-entry unchanged.
- New run contains cloned primary batches and copied case rows before rerun execution.
- Rerun batches point to cloned primary batches, not original batches.
- New template has rerun config changes while original template is unchanged.
- New jobs directory starts as a copy of the original jobs directory.
- Selected exception trial directories are deleted only from the new jobs copy.
- Finished rerun merges cases and trial directories only into the new run.
- Partial rerun failure preserves copied baseline cases in the new run.
- UI/API responses include `runId` for the derived run and `parentRunId`.
