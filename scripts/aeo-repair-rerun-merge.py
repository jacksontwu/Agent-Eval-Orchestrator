#!/usr/bin/env python3
"""Repair exception-rerun merge for a run using controller imported Harbor jobs.

Backfills primary batch case rows from imported job archives, merges rerun
results into parent batches, and rebuilds the combined Harbor job directory.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_eval_orchestrator.controller.server import (  # noqa: E402
    DEFAULT_IMPORTED_JOBS_DIRNAME,
    _rebuild_merged_job_for_run,
)
from agent_eval_orchestrator.normalizers.harbor import normalize_harbor_job  # noqa: E402
from agent_eval_orchestrator.normalizers.harbor_job_merge import _iter_trial_dirs, copy_trial_dirs  # noqa: E402
from agent_eval_orchestrator.storage.layout import default_layout  # noqa: E402
from agent_eval_orchestrator.storage.store import Store  # noqa: E402


def _resolve_jobs_dir(store: Store, run_id: str) -> Path:
    run = store.get_run(run_id)
    if not run:
        raise RuntimeError(f"run not found: {run_id}")
    template = store.get_task_template(str(run["template_id"]))
    executor_config = dict((template or {}).get("executor_config") or {})
    raw = str(executor_config.get("combinedJobsDir") or os.environ.get("AEO_COMBINED_JOBS_DIR") or "")
    if not raw.strip():
        raw = "/home/djn/code/harbor/jobs"
    return Path(raw).expanduser().resolve()


def _backfill_primary_batch(
    *,
    store: Store,
    batch_id: str,
    imported_root: Path,
    worker_id: str,
) -> int:
    imported_job_dir = imported_root / batch_id
    if not imported_job_dir.exists():
        raise RuntimeError(f"missing imported job dir for primary batch: {imported_job_dir}")
    summary, cases, artifact_index = normalize_harbor_job(imported_job_dir, batch_id)
    store.update_batch_progress(
        batch_id=batch_id,
        worker_id=worker_id,
        status="succeeded",
        current_step="completed",
        finished=True,
        error_text=None,
        summary=summary,
        cases=cases,
        artifact_index=artifact_index,
    )
    return len(cases)


def repair_run(*, store: Store, run_id: str, jobs_dir: Path | None = None) -> dict[str, object]:
    run = store.get_run(run_id)
    if not run:
        raise RuntimeError(f"run not found: {run_id}")
    resolved_jobs_dir = jobs_dir or _resolve_jobs_dir(store, run_id)
    imported_root = store.layout.controller_dir / DEFAULT_IMPORTED_JOBS_DIRNAME

    primary_counts: dict[str, int] = {}
    rerun_merged: list[str] = []
    for batch in store.list_batches_for_run(run_id):
        if str(batch.get("batch_kind") or "") != "exception_rerun":
            continue
        batch_id = str(batch["batch_id"])
        parent_batch_id = str(batch.get("parent_batch_id") or "").strip()
        if not parent_batch_id:
            continue
        rerun_imported = imported_root / batch_id
        parent_imported = imported_root / parent_batch_id
        if rerun_imported.exists() and parent_imported.exists():
            copy_trial_dirs(rerun_imported, parent_imported)
        parent_batch = store.get_batch(parent_batch_id)
        if parent_batch:
            parent_job_dir = Path(str(parent_batch["batch_root"])) / "harbor" / "jobs" / parent_batch_id
            rerun_job_dir = Path(str(batch["batch_root"])) / "harbor" / "jobs" / batch_id
            if rerun_job_dir.exists():
                parent_job_dir.mkdir(parents=True, exist_ok=True)
                copy_trial_dirs(rerun_job_dir, parent_job_dir)
        imported_job_dir = imported_root / batch_id
        if imported_job_dir.exists():
            summary, _, artifact_index = normalize_harbor_job(imported_job_dir, batch_id)
            worker_id = str(batch.get("assigned_worker_id") or batch.get("preferred_worker_id") or "repair")
            store.update_batch_progress(
                batch_id=batch_id,
                worker_id=worker_id,
                status="succeeded",
                current_step="completed",
                finished=True,
                error_text=None,
                summary=summary,
                cases=None,
                artifact_index=artifact_index,
            )
        rerun_merged.append(batch_id)

    for batch in store.list_primary_batches_for_run(run_id):
        batch_id = str(batch["batch_id"])
        worker_id = str(batch.get("assigned_worker_id") or batch.get("preferred_worker_id") or "repair")
        primary_counts[batch_id] = _backfill_primary_batch(
            store=store,
            batch_id=batch_id,
            imported_root=imported_root,
            worker_id=worker_id,
        )

    rerun_job_id = str(run.get("rerun_job_id") or "").strip()
    if rerun_job_id:
        store.update_run_rerun_fields(run_id=run_id, rerun_status="succeeded")
        store.update_run_rerun_job(rerun_job_id, status="succeeded", error_text=None, finished=True)

    _rebuild_merged_job_for_run(store=store, run_id=run_id, jobs_dir=resolved_jobs_dir)

    merged_job_dir = resolved_jobs_dir / str(run["display_name"])
    merged_trials = len(_iter_trial_dirs(merged_job_dir)) if merged_job_dir.exists() else 0
    selected_total = sum(
        len(store.list_case_runs(str(batch["batch_id"])))
        for batch in store.list_primary_batches_for_run(run_id)
    )
    remaining_exceptions = len(store.list_exception_cases_for_run(run_id))

    return {
        "runId": run_id,
        "displayName": run["display_name"],
        "primaryCaseCounts": primary_counts,
        "primaryCasesTotal": sum(primary_counts.values()),
        "rerunBatchesMerged": rerun_merged,
        "mergedJobDir": str(merged_job_dir),
        "mergedTrialCount": merged_trials,
        "selectedCaseRows": selected_total,
        "remainingExceptions": remaining_exceptions,
        "jobsDir": str(resolved_jobs_dir),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id", help="Run id to repair, e.g. run-c0c10d309706")
    parser.add_argument(
        "--shared-root",
        default=os.environ.get("AEO_SHARED_ROOT", str(REPO_ROOT / "runtime")),
        help="Controller shared root containing state.sqlite3",
    )
    parser.add_argument(
        "--jobs-dir",
        default="",
        help="Combined Harbor jobs dir override (defaults to template combinedJobsDir)",
    )
    args = parser.parse_args()

    layout = default_layout(args.shared_root)
    store = Store(layout)
    jobs_dir = Path(args.jobs_dir).expanduser().resolve() if str(args.jobs_dir).strip() else None
    report = repair_run(store=store, run_id=str(args.run_id), jobs_dir=jobs_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if int(report["mergedTrialCount"]) != 731:
        print(
            f"warning: expected 731 merged trials, got {report['mergedTrialCount']}",
            file=sys.stderr,
        )
        return 1
    if int(report["primaryCasesTotal"]) != 731:
        print(
            f"warning: expected 731 primary case rows, got {report['primaryCasesTotal']}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
