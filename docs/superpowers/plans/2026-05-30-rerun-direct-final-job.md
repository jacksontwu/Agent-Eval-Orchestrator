# Rerun Direct Final Job Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make derived exception reruns create and maintain a normal Harbor job under the configured jobs root, without copying the entire jobs tree or mutating the source job.

**Architecture:** On rerun start, clone only the source Harbor job directory into a final rerun job directory named `<source-job>-rerun-<run-id>` and leave all original trials intact. Worker results are first imported into the existing controller imported-jobs staging area; merge then replaces matching case trials in the final rerun job directory and refreshes the job summary. The database remains responsible for run/batch status and worker routing, while Harbor job files are the source for final viewer artifacts.

**Tech Stack:** Python, pytest, existing controller/store/normalizer modules, Harbor job result normalization.

---

### Task 1: Rerun Artifact Targeting

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/rerun_artifacts.py`
- Modify: `src/agent_eval_orchestrator/controller/run_rerun_coordinator.py`
- Test: `tests/controller/test_run_rerun_coordinator.py`

- [ ] **Step 1: Write failing tests**

Add tests that start a derived rerun from `combinedJobsDir=/tmp/jobs` and source display name `swe-p-0001`, then assert:
- only `/tmp/jobs/swe-p-0001` is copied
- destination is `/tmp/jobs/swe-p-0001-rerun-<derived-run-id>`
- unrelated jobs under `/tmp/jobs` are not copied
- exception trials are not deleted during startup
- derived template `combinedJobsDir` remains `/tmp/jobs`

- [ ] **Step 2: Verify RED**

Run:

```bash
/usr/bin/env PYTHONPATH=src uv run pytest tests/controller/test_run_rerun_coordinator.py::test_start_rerun_clones_only_source_job_to_final_rerun_job_without_pruning -q
```

Expected: FAIL because current code copies the whole jobs root into `runtime/archives/.../harbor/jobs` and prunes immediately.

- [ ] **Step 3: Implement artifact helpers**

Add helpers that resolve the source job dir from `combinedJobsDir` plus sanitized run display name and derive the final rerun job dir as `<source-job-name>-rerun-<derived-run-id>` under the same jobs root. Replace `copy_jobs_tree` usage with a single-job clone helper that refuses overlapping source/target paths.

- [ ] **Step 4: Wire startup**

Update `RunRerunCoordinator._set_derived_template_jobs_dir` so the derived run keeps `combinedJobsDir` equal to the original jobs root. Update `_copy_and_prune_source_jobs` to clone the single source job to the final rerun job and not call `delete_trials_for_cases`.

- [ ] **Step 5: Verify GREEN**

Run:

```bash
/usr/bin/env PYTHONPATH=src uv run pytest tests/controller/test_run_rerun_coordinator.py -q
```

Expected: PASS.

### Task 2: Merge Into Final Rerun Job

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/server.py`
- Modify: `src/agent_eval_orchestrator/normalizers/harbor_job_merge.py`
- Test: `tests/controller/test_rerun_exceptions_api.py`

- [ ] **Step 1: Write failing tests**

Add a heartbeat/import test for a derived exception rerun where the final rerun job already contains `ok__old` and `exc-a__old`. After rerun batch heartbeat imports `exc-a__new`, assert:
- final rerun job contains `ok__old`
- final rerun job contains `exc-a__new`
- final rerun job no longer contains `exc-a__old`
- imported-jobs staging directory still exists
- source job is unchanged

- [ ] **Step 2: Verify RED**

Run:

```bash
/usr/bin/env PYTHONPATH=src uv run pytest tests/controller/test_rerun_exceptions_api.py::test_heartbeat_merges_derived_exception_rerun_into_final_rerun_job -q
```

Expected: FAIL because current merge rebuilds a merged job from batch/imported sources instead of replacing trials in the pre-cloned final job.

- [ ] **Step 3: Implement case replacement helper**

Create or extend a normalizer helper to copy trial dirs from a rerun job into a target job by case id. For each incoming trial, read `result.json.task_name`, delete existing target trial dirs for the same case, then copy the incoming trial.

- [ ] **Step 4: Wire heartbeat merge**

Update `_apply_exception_rerun_merge` so derived rerun batches merge staged worker results into the final rerun job directory from executor metadata, then refresh that job result. Keep the existing DB case merge and imported-jobs staging behavior.

- [ ] **Step 5: Verify GREEN**

Run:

```bash
/usr/bin/env PYTHONPATH=src uv run pytest tests/controller/test_rerun_exceptions_api.py tests/normalizers/test_harbor_job_merge.py -q
```

Expected: PASS.

### Task 3: Full Regression

**Files:**
- No new files

- [ ] **Step 1: Run related tests**

Run:

```bash
/usr/bin/env PYTHONPATH=src uv run pytest tests/controller/test_run_rerun_coordinator.py tests/controller/test_rerun_exceptions_api.py tests/storage/test_exception_type_store.py tests/normalizers/test_harbor_job_merge.py -q
```

Expected: PASS.

- [ ] **Step 2: Run full test suite**

Run:

```bash
/usr/bin/env PYTHONPATH=src uv run pytest -q
```

Expected: PASS.

- [ ] **Step 3: Commit**

Commit the implementation after tests pass:

```bash
git add src tests docs/superpowers/plans/2026-05-30-rerun-direct-final-job.md
git commit -m "fix: merge reruns into final harbor job"
```

### Self-Review

- Covers startup copy granularity, final job naming, staged collection, replacement merge, and no source mutation.
- No placeholders remain.
- Function naming is intentionally left to implementation context, but all behaviors and file boundaries are explicit.
