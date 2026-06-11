# YAML Bind Asset Sync Design

## Problem

YAML-first eval task creation currently shards and syncs dataset cases, and it has a special path for bitfun assets. It does not handle arbitrary bind-mounted controller-local assets. A Harbor YAML can mount a local binary or directory and reference that same path elsewhere, for example in `agents[].kwargs.binary_path`. On remote workers that controller path may not exist or may be the wrong type, causing Harbor runtime failures such as trying to install a binary from a directory.

## Goal

When AEO creates a distributed task from Harbor YAML, controller-local paths that workers need must be explicitly discovered, validated, synced, and rewritten in the generated worker YAML.

The design covers:

- `datasets[0].path`
- `tasks[].path`
- `environment.mounts[]` entries with `type: bind`
- Other YAML string values that equal one of those discovered source paths, or are under one of those source paths

The design does not scan arbitrary strings as new source paths. Paths are discovered only from explicit Harbor YAML fields.

## Architecture

Introduce a generic YAML asset planning layer in `controller/harbor_yaml.py`. It produces a plan with:

- dataset or task sources already used for sharding
- bind assets discovered from `environment.mounts[]`
- a controller-to-worker rewrite map for each worker

Each bind asset records:

- original controller source path
- whether it is a file or directory
- stable asset name used under the worker sync root
- worker target path for each worker

Worker sync roots keep the existing shape:

```text
<worker sharedRoot>/sync/<run id>/
```

Dataset cases continue to sync to:

```text
<sync root>/dataset/<case id>
```

Bind assets sync to:

```text
<sync root>/assets/<stable asset name>
```

Files remain files. Directories remain directories.

## YAML Rewrite

Worker batch YAML generation starts from the submitted YAML, applies existing batch-level changes, then rewrites string values using the discovered asset map.

Rules:

- Rewrite only YAML string values, not mapping keys.
- Rewrite a string if it exactly equals a discovered source path.
- Rewrite a string if it is a child path under a discovered source path.
- Use longest-prefix matching when multiple discovered paths overlap.
- Do not rewrite strings that merely contain a path as a substring.
- Do not discover new paths during rewrite.

`jobs_dir` remains controlled by AEO and points at the batch Harbor jobs directory.

Example:

```yaml
environment:
  mounts:
    - type: bind
      source: /home/djn/code/codeagentcli
      target: /usr/local/bin/codeagentcli
agents:
  - name: codeagent
    kwargs:
      install_mode: binary
      binary_path: /home/djn/code/codeagentcli
```

becomes, for a worker:

```yaml
environment:
  mounts:
    - type: bind
      source: /home/djn/worker/agent-eval-orchestrator/runtime/sync/<run id>/assets/codeagentcli
      target: /usr/local/bin/codeagentcli
agents:
  - name: codeagent
    kwargs:
      install_mode: binary
      binary_path: /home/djn/worker/agent-eval-orchestrator/runtime/sync/<run id>/assets/codeagentcli
```

## Validation

Task creation validates every `environment.mounts[]` item with `type: bind` before creating the run:

- `source` must be present.
- `source` must be an absolute path.
- `source` must exist on the controller.
- `source` must be a regular file or a directory.

Invalid bind sources return HTTP 400 with a message that identifies the mount index and source.

Non-bind mounts are not synced and are not rewritten.

Dataset and task path validation keeps the existing behavior:

- `datasets[0].path` must be an existing directory.
- Each selected dataset case must exist.
- Each `tasks[].path` must exist.
- Remote workers still require `ssh_host_alias` for asset sync.

## Asset Sync

Extend `AssetSyncer` to sync generic bind assets for each worker:

- local worker file: copy file and preserve executable mode
- local worker directory: replace target directory and copy tree
- remote worker file: create target directory and `scp` file
- remote worker directory: create target directory and `rsync` directory

The asset sync job steps include a generic `sync_assets` step when bind assets are present. The existing dataset step remains `sync_cases`.

After a worker sync succeeds, the store is updated with:

- `datasetPathByWorker`
- `assetPathsByWorker`, a diagnostic map from original controller source path to worker target path
- `mountsByWorker`, kept for existing executor fallback behavior

For YAML-first jobs, `harborYamlByBatchId` is the authoritative execution config, so the generated worker YAML carries the rewritten paths.

## Compatibility

The bitfun-specific sync and rewrite path is replaced by the generic bind asset path. Existing bitfun YAML continues to work because its CLI and config mounts are ordinary bind mounts.

Bitfun-specific structural validation is removed from YAML-first task creation in this change. Generic bind validation is the source of truth for mounted files and directories.

Existing non-YAML task creation can keep its current executor config path and does not need to move to generic YAML asset rewriting in this change.

## Error Handling

If any worker fails to sync an asset, only that worker's batches are marked sync failed, matching current dataset sync behavior.

If validation fails before run creation, no run, batches, or sync job are created.

Executor-level mount validation remains as a last line of defense. If a generated worker YAML or executor config points to a missing mount source, execution should fail early with a clear error instead of surfacing as a Harbor runtime error.

## Tests

Add or update tests for:

- YAML-first task creation rewrites a bind-mounted file source in both `environment.mounts[].source` and `agents[].kwargs.binary_path`.
- YAML-first task creation rewrites a bind-mounted directory source and a child path reference elsewhere in YAML.
- Missing bind source returns HTTP 400.
- Relative bind source returns HTTP 400.
- Overlapping source paths use longest-prefix rewrite.
- Local asset sync copies files and preserves executable mode.
- Local asset sync copies directories.
- Remote asset sync uses `scp` for files and `rsync` for directories.
- Existing dataset sharding and `task_names` rewriting still work.

## Acceptance Criteria

- A YAML using `codeagent` with `install_mode: binary`, a bind-mounted controller-local binary, and `binary_path` pointing to that binary runs on remote workers with both fields rewritten to worker sync paths.
- A YAML bind mount whose source does not exist on the controller is rejected before task creation.
- Bitfun YAML with bind-mounted CLI/config continues to work through the generic asset path.
- Existing YAML dataset sharding behavior remains unchanged.
