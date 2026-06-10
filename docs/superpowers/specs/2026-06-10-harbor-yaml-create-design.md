# Harbor YAML Create Task Design

## Goal

Refactor the Create Distributed Eval Task flow so operators paste the YAML content they would pass to `harbor run -c`. AEO should stop exposing agent-specific fields such as BitFun, OpenCode, Anthropic, agent kwargs, model, and timeout multipliers in the Create page.

AEO remains responsible for only three things:

- Split the selected Harbor task set across selected workers using the existing distribution logic.
- Generate a run/job name from agent, model, dataset/task, and a timestamp suffix.
- Pass all other Harbor YAML parameters through to workers without interpretation.

## Non-Goals

This change will not:

- Redesign exception rerun configuration.
- Remove support for historical tasks created with the older `executor_config` shape.
- Add multi-dataset Harbor YAML support in the first version.
- Sync agent binaries or config directories such as `bitfun-cli` or `.config/bitfun`.
- Reinterpret retry, environment, verifier, artifacts, agent env, or agent kwargs.

## Requirements Summary

| Decision | Choice |
|----------|--------|
| Create input | One Harbor YAML textarea plus worker selection |
| Harbor modes | Support `datasets[0]` and `tasks[*]` |
| Dataset selection | `task_names` first; otherwise enumerate task dirs; apply `n_tasks` globally before worker split |
| Worker split | Existing `Store.create_sharded_batches()` weighted split |
| Job naming | `{agent}-{model}-{dataset_or_task}-{timestamp}` after sanitization |
| YAML mutation | Only `job_name`, `jobs_dir`, and per-worker `datasets`/`tasks` subset |
| Harbor execution | Worker writes generated YAML and runs `uv run harbor run -c <yaml> -y` |
| Backward compatibility | Keep old executor path for existing runs and tests |

## Chosen Approach

Add a **YAML-first create path** under the existing create-and-distribute flow.

Alternatives considered:

| Approach | Verdict |
|----------|---------|
| Map pasted YAML back to legacy `executorConfig` | Rejected. AEO would still understand and rebuild agent-specific Harbor flags. |
| YAML-first create path with minimal structured edits | **Chosen.** Matches the desired AEO boundary while preserving distribution and merge behavior. |
| Fully opaque YAML broadcast to every worker | Rejected. It cannot distribute work; every worker would run the same task set. |

## UI Design

The Create page keeps the worker selection area and replaces the current parameter grid with one large `Harbor YAML` textarea.

Removed fields:

- Task Name
- Executor
- Agent Name
- Model
- Per Worker Concurrency
- Timeout multipliers
- Dataset Path
- BitFun CLI Path
- BitFun Config Root
- Jobs Dir
- Selected Case IDs
- Agent Env
- Agent Kwargs

The submit payload becomes:

```json
{
  "harborYaml": "job_name: my-test-job\njobs_dir: jobs\n...",
  "workerIds": ["worker-a", "worker-b"]
}
```

The page copy should state that AEO parses the YAML only to find the task set, split it across workers, and generate names. Other Harbor parameters are passed through.

## Harbor YAML Contract

The backend accepts a top-level YAML mapping. It supports exactly one of:

```yaml
datasets:
  - path: examples/tasks
    task_names:
      - hello-world
    n_tasks: 1
```

or:

```yaml
tasks:
  - path: examples/tasks/hello-world
```

For `datasets` mode:

1. Only one dataset entry is supported in this first version.
2. `datasets[0].path` is required and must exist.
3. If `task_names` is present, it defines the global task set.
4. If `task_names` is absent, AEO enumerates task directories under `path`.
5. If `n_tasks` is present, AEO truncates the global task set before worker splitting.
6. Per-worker YAML preserves the dataset entry, replaces `task_names` with that worker's shard, and removes `n_tasks`.

For `tasks` mode:

1. `tasks` must be a non-empty list of mappings with `path`.
2. Each path must exist.
3. AEO uses each path basename as the task ID.
4. Per-worker YAML replaces `tasks` with that worker's shard.

If both `datasets` and `tasks` are present, the API returns 400 and asks the user to choose one mode.

## Name Generation

The controller derives a base run name from the submitted YAML:

- Agent: `agents[0].name`, fallback `agent`.
- Model: `agents[0].model_name`, fallback `agents[0].model_info.name`, fallback `agents[0].model`, fallback `model`.
- Dataset/task: basename of `datasets[0].path`, or basename of `tasks[0].path`.
- Timestamp: controller creation time in a compact format such as `YYYYMMDD-HHMMSS`.

The raw format is:

```text
{agent}-{model}-{dataset_or_task}-{timestamp}
```

The result is passed through the existing name sanitizer and length limits. The user's submitted `job_name` is intentionally ignored for AEO-created runs.

All batches in one run use the same run display name for final merged output. Individual worker Harbor jobs may append or use the batch ID internally if needed to avoid collisions, but result merging should still produce the generated run name as the combined Harbor job.

## Backend Flow

`POST /api/eval-tasks/create-and-distribute` accepts the new YAML-first request shape.

Processing order:

1. Validate `workerIds` is non-empty.
2. Parse `harborYaml` as YAML.
3. Validate the top-level mapping and supported Harbor task mode.
4. Resolve the global task set from `datasets` or `tasks`.
5. Generate the AEO run name.
6. Create the task template with:
   - `dataset_ref` set to the dataset path for `datasets` mode, or a stable descriptive value for `tasks` mode.
   - `executor_kind="harbor-docker"`.
   - `executor_config` containing the original YAML, generated name, and task mode metadata.
7. Create the run with the generated display name.
8. Call `create_sharded_batches()` with the resolved task IDs, selected workers, and concurrency metadata.
9. Build each batch's generated YAML from its `selected_case_ids`.
10. Update the template with `executor_config.harborYamlByBatchId`, keyed by `batch_id`.
11. Queue batches directly. There is no asset-sync phase for YAML-first tasks.

The legacy request shape can remain available for compatibility, but the Create page should only submit YAML-first payloads.

## Worker Execution

`HarborExecutor.prepare()` gains a YAML-first branch.

When the executor config contains YAML-first data:

1. Load the generated YAML from `executor_config.harborYamlByBatchId[batch_id]`.
2. Write it under the batch root, for example `harbor-config.yaml`.
3. Run:

```text
uv run harbor run -c <batch-root>/harbor-config.yaml -y
```

4. Set metadata so existing result collection still knows:
   - job name
   - jobs directory
   - selected task IDs
   - command
   - collect/merge settings

The YAML-first branch must not rebuild Harbor flags from AEO-specific fields. It should not add `-a`, `-m`, `--ae`, `--ak`, timeout flags, environment flags, mounts, retries, or artifacts from separate AEO config keys.

The existing non-YAML branch remains for older tasks and rerun paths.

## Error Handling

| Scenario | HTTP | Behavior |
|----------|------|----------|
| Missing or empty `harborYaml` | 400 | Ask user to paste Harbor YAML |
| Invalid YAML | 400 | Return parser error summary |
| YAML top-level is not a mapping | 400 | Require a mapping |
| Missing both `datasets` and `tasks` | 400 | Require one supported task mode |
| Both `datasets` and `tasks` present | 400 | Require exactly one task mode |
| More than one dataset entry | 400 | Multiple datasets are not supported yet |
| Dataset path missing | 400 | Return missing path |
| Task path missing | 400 | Return missing path |
| `task_names` references missing tasks | 400 | List the first few missing task names |
| No selected workers | 400 | Require at least one worker |
| Missing agent/model fields | 201 | Use fallback strings in generated name |

The API must make clear that `job_name` and `jobs_dir` from the pasted YAML are controlled by AEO for distributed execution.

## Testing Plan

Add focused tests for:

- YAML parsing and splitting:
  - `datasets + task_names`
  - `datasets + n_tasks`
  - `tasks`
  - invalid YAML
  - missing task names
  - both `datasets` and `tasks`
- Create API:
  - `harborYaml + workerIds` creates a run and sharded batches.
  - The generated task name uses agent, model, dataset/task, and timestamp.
  - Template executor config stores YAML-first data.
  - Batches are queued directly without asset sync.
- Harbor executor:
  - YAML-first prepare writes a config file.
  - Command contains `harbor run -c`.
  - Command does not contain legacy reconstructed `-a`, `-m`, `--ae`, `--ak`, timeout, or mount flags.
- Static UI:
  - Create form contains `harborYaml`.
  - Create form no longer contains agent/model/BitFun/timeout/env/kwargs fields.

Existing tests for the legacy executor path should keep passing unless they explicitly assert the old Create UI.

## Implementation Notes

The project currently does not declare a YAML parser dependency. The implementation should either add a small dependency such as PyYAML or use an existing available parser if the environment already provides one. Because the input is user-supplied, use a safe loader only.

The YAML split layer should be isolated from `server.py`, for example in a controller helper module, so it can be unit-tested without HTTP setup.
