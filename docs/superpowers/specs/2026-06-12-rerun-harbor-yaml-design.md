# Exception Rerun Harbor YAML Design

## Goal

Change the **Rerun Exception** flow from an AEO-specific parameter form to a
Harbor YAML-first flow.

Operators should first choose exception types, then review and edit a Harbor
YAML parameter template, then confirm execution. The actual rerun case scope is
always derived by the backend from the selected exception types. YAML task
lists are shown as context only and do not decide which cases rerun.

## Non-Goals

This change will not:

- Let operators rerun arbitrary non-exception cases by editing YAML.
- Let operators reassign exception cases to different workers.
- Change derived rerun run semantics. Reruns still create a visible derived run.
- Change result merge semantics for exception reruns.
- Remove support for older runs that were created before YAML-first task
  creation.

## Requirements Summary

| Decision | Choice |
| --- | --- |
| UI entry | Existing **Rerun Exception** button on task detail |
| Scope control | Exception type checkboxes only |
| YAML role | Editable Harbor parameter template |
| YAML task fields | Displayed, but ignored for rerun scope |
| Actual rerun cases | Backend-selected from exception records and selected types |
| YAML source, YAML-first run | Original `template.executor_config.harborYaml` |
| YAML source, legacy run | Backend-generated equivalent Harbor YAML |
| Confirm payload | `selectedErrorTypes` plus edited `harborYaml` |
| Execution config | Backend generates final `harborYamlByBatchId` |
| Worker assignment | Original worker ownership from exception records |
| Job ownership | Derived run owns rerun status, batches, and artifacts |

## Chosen Approach

Use a YAML-first rerun flow for both YAML-first and legacy source runs.

The modal keeps the existing exception type selection UI and replaces the old
field grid with a Harbor YAML editor. The YAML editor is a parameter template:
operators may change agent, model, timeout, retry, environment, mounts, and
other Harbor settings, but they cannot change the rerun case scope through YAML.

Alternatives considered:

| Approach | Verdict |
| --- | --- |
| Full YAML-first rerun | Chosen. Matches the create-task boundary and removes old BitFun-specific UI from the rerun path. |
| YAML editor only for YAML-first runs | Rejected. It preserves two rerun configuration models and keeps legacy form debt. |
| Generate YAML in the browser | Rejected. It duplicates backend Harbor YAML parsing and sharding rules in frontend code. |

## UI Design

### Modal Layout

The existing modal remains the entry point. It contains:

1. Exception type selection.
2. Summary cards for selected exception count, total exception count, unique
   cases, merged Harbor errors, and worker count.
3. A Harbor YAML editor.
4. Confirm and cancel actions.

The old fields are removed from the modal:

- Executor
- Agent Name
- Per Worker Concurrency
- Timeout multiplier fields
- Dataset Path
- BitFun CLI Path
- BitFun config dir
- Jobs Dir

Those values are now edited through Harbor YAML when they are Harbor-native
settings. AEO-controlled fields are still overwritten by the backend during
final batch YAML generation.

### YAML Preview Behavior

When the modal opens, frontend requests a preview:

```http
POST /api/runs/{runId}/rerun-exceptions/harbor-yaml-preview
```

Request:

```json
{
  "selectedErrorTypes": ["stderr", "AgentTimeoutError"]
}
```

Response:

```json
{
  "harborYaml": "job_name: ...\n...",
  "source": "original_yaml",
  "exceptionCount": 93,
  "workerShards": {
    "worker-a": 50,
    "worker-b": 43
  }
}
```

`source` is one of:

- `original_yaml`: sourced from `template.executor_config.harborYaml`.
- `generated_legacy_yaml`: generated from legacy `executor_config`.

Changing exception type checkboxes refreshes the count and shard preview. It
does not rewrite the YAML editor content. If the operator has unsaved edits,
the frontend should preserve the editor text and only update the preview stats.

### Scope Contract

The modal should make the model clear:

- Exception type checkboxes decide rerun scope.
- YAML edits decide Harbor runtime parameters.

YAML task ranges are not trusted as scope input. The preview may show original
`datasets[0].task_names` or original `tasks`, but editing those fields will not
change the final rerun cases.

## API Design

### Preview Endpoint

`POST /api/runs/{runId}/rerun-exceptions/harbor-yaml-preview`

Request:

```json
{
  "selectedErrorTypes": ["stderr", "AgentTimeoutError"]
}
```

Behavior:

1. Validate the source run exists and is eligible for rerun.
2. Resolve selected exception types using current exception records.
3. Resolve the matching exception items and original worker shards.
4. Return the YAML template and preview stats.

This endpoint does not create a run, batch, sync job, or artifact.

### Confirm Endpoint

Keep the existing endpoint:

```http
POST /api/runs/{runId}/rerun-exceptions
```

New YAML-first request:

```json
{
  "selectedErrorTypes": ["stderr", "AgentTimeoutError"],
  "harborYaml": "edited Harbor YAML"
}
```

Compatibility:

- Empty body `{}` keeps current all-exceptions behavior for existing callers.
- Old structured config bodies may remain supported during transition, but the
  UI should submit YAML-first payloads only.

Response keeps the derived rerun run shape:

```json
{
  "runId": "run-derived",
  "parentRunId": "run-original",
  "rerunJobId": "rerun-abc123",
  "rerunStatus": "syncing",
  "exceptionCount": 93,
  "selectedErrorTypes": ["stderr", "AgentTimeoutError"],
  "workerShards": {
    "worker-a": 50,
    "worker-b": 43
  }
}
```

## Backend Data Flow

Confirm execution follows this order:

1. Validate source run state.
2. Resolve selected exception types.
3. List exception items from Harbor job artifacts or normalized DB records.
4. Filter exception items by selected type.
5. Group filtered items by original worker.
6. Parse the submitted Harbor YAML as a parameter template.
7. Validate that the YAML has exactly one supported task mode: `datasets` or
   `tasks`.
8. Validate dataset or task sources and bind assets.
9. Create the derived template and derived run.
10. Copy and prune source Harbor jobs for the derived run.
11. Clone primary batches and case rows into the derived run.
12. Create exception rerun batches under the derived run.
13. Generate final `harborYamlByBatchId` for each rerun batch.
14. Persist the derived template executor config with YAML-first metadata.
15. Start scoped asset sync for the derived rerun run.

The original run and original template remain unchanged.

## YAML Generation Rules

### Preview YAML

For YAML-first source runs:

- Use `template.executor_config.harborYaml` as the preview text.
- Do not rewrite `datasets[0].task_names` or `tasks` based on selected
  exception types.

For legacy source runs:

- Generate equivalent Harbor YAML from legacy `executor_config`.
- Include `datasets[0].path` from the source template dataset reference.
- Include original source run case ids as `datasets[0].task_names` for context.
- Map legacy fields to Harbor YAML where possible:
  - `agentName` to `agents[0].name`
  - `modelName` to `agents[0].model_name`
  - `timeoutMultiplier` to `timeout_multiplier`
  - `agentTimeoutMultiplier` to `agent_timeout_multiplier`
  - `verifierTimeoutMultiplier` to `verifier_timeout_multiplier`
  - `environmentBuildTimeoutMultiplier` to
    `environment_build_timeout_multiplier`
  - environment type and mounts to `environment`
  - `nConcurrent` to `n_concurrent_trials`

Legacy generation should be conservative. If a field cannot be represented
cleanly in Harbor YAML, omit it rather than inventing non-Harbor syntax.

### Final Batch YAML

Final batch YAML starts from the submitted template, then the backend applies
AEO-controlled overrides:

- `job_name`: derived rerun job name.
- `jobs_dir`: the batch-local Harbor jobs directory.
- Dataset mode:
  - `datasets[0].task_names`: selected exception case ids for that batch.
  - `datasets[0].n_tasks`: removed.
- Tasks mode:
  - `tasks`: selected exception tasks for that batch.

The backend ignores user-submitted task range values when deciding scope.

### Dataset Path Changes

If the operator edits the YAML dataset or task paths:

- The backend accepts the changed source paths as the new execution source.
- Every backend-selected exception case must exist at the new source path.
- If any selected exception case cannot be resolved, the request fails before
  creating the derived run.

## Asset Sync

YAML-first rerun should reuse the YAML-first create-task asset planning path:

- Dataset or task sources are validated and synced.
- `environment.mounts[]` bind assets are discovered, validated, synced, and
  rewritten.
- Generated worker YAML rewrites controller-local paths to worker sync paths.

BitFun-specific paths are not separate rerun form fields. If BitFun binaries or
config directories are needed, they should be represented in the Harbor YAML as
bind mounts or Harbor-supported agent configuration.

## Error Handling

| Scenario | Behavior |
| --- | --- |
| Preview run not found | 404 |
| Source run not terminal | 409 |
| Rerun already active | 409 |
| No selected exception type | 400 |
| Invalid selected exception type | 400 |
| Submitted YAML is invalid | 400, keep modal open |
| YAML has neither `datasets` nor `tasks` | 400 |
| YAML has both `datasets` and `tasks` | 400 |
| Dataset path does not exist | 400 |
| Edited dataset path misses selected exception case | 400 |
| Bind mount source missing or relative | 400 |
| Asset sync fails after derived run creation | Derived run marked failed; original run unchanged |

## Testing

### Unit Tests

- Preview for YAML-first run returns original `harborYaml`.
- Preview for legacy run returns generated Harbor YAML.
- Selected exception types affect preview `exceptionCount` and `workerShards`.
- Submitted YAML task ranges do not affect actual rerun case ids.
- Final batch YAML overwrites `job_name`, `jobs_dir`, and task range fields.
- Edited dataset path is validated against selected exception case ids.
- Invalid YAML task mode returns `RerunValidationError`.

### API Tests

- `POST /rerun-exceptions/harbor-yaml-preview` happy path.
- Preview rejects invalid selected error types.
- YAML-first confirm creates a derived run and rerun batches.
- Confirm preserves editable Harbor parameters in generated batch YAML.
- Confirm rejects YAML that cannot resolve selected exception cases.
- Empty body compatibility keeps existing all-exceptions behavior.

### UI Tests

- Rerun modal contains Harbor YAML editor and no old parameter grid fields.
- Opening the modal loads YAML preview.
- Changing exception type selection updates selected count without replacing
  edited YAML text.
- Confirm submits `selectedErrorTypes` and `harborYaml`.
- API errors render inside the modal and preserve editor text.

### Executor Tests

- Rerun batch using `harborYamlByBatchId` writes `harbor-config.yaml`.
- Executor command uses `harbor run -c <config> -y`.
- Legacy reconstructed CLI flags are not appended when YAML-first config is
  present.

## File Touch List

| File | Change |
| --- | --- |
| `src/agent_eval_orchestrator/controller/static.py` | Replace rerun form fields with YAML editor, preview loading, and YAML-first submit payload |
| `src/agent_eval_orchestrator/controller/server.py` | Add preview route; route YAML-first confirm payload to coordinator |
| `src/agent_eval_orchestrator/controller/run_rerun_coordinator.py` | Add preview builder and YAML-first rerun application |
| `src/agent_eval_orchestrator/controller/harbor_yaml.py` | Reuse and extend YAML parse/build helpers for rerun templates |
| `src/agent_eval_orchestrator/controller/asset_syncer.py` | Ensure rerun sync can use YAML-first manifest with bind assets |
| `tests/controller/test_rerun_exceptions_api.py` | Preview and confirm API coverage |
| `tests/controller/test_run_rerun_coordinator.py` | YAML-first rerun unit coverage |
| `tests/controller/test_static_auth_token.py` | Static UI assertions |
| `tests/executors/test_harbor_executor.py` | YAML-first executor regression coverage |

## Relationship to Existing Specs

This design updates these earlier decisions:

- It supersedes the old field-based rerun configuration UI from
  `2026-05-27-rerun-exception-config-design.md`.
- It keeps exception type selection from
  `2026-05-29-exception-type-rerun-design.md`.
- It keeps derived rerun run semantics from
  `2026-05-30-derived-rerun-run-design.md`.
- It aligns rerun configuration with YAML-first create-task behavior from
  `2026-06-10-harbor-yaml-create-design.md`.
- It reuses bind asset sync behavior from
  `2026-06-11-yaml-bind-asset-sync-design.md`.
