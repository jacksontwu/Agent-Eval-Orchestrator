#!/usr/bin/env python3
"""Regenerate a Harbor job result.json from on-disk trial directories.

Uses the same aggregation path as ``harbor.job.Job.run()`` when a job finishes.
Intended to be invoked via ``uv run`` from the Harbor repository.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from harbor.job import Job
from harbor.models.job.config import JobConfig
from harbor.models.job.result import JobResult, JobStats
from harbor.models.trial.result import TrialResult
from harbor.utils.pass_at_k import compute_pass_at_k_by_evals


def _iter_trial_dirs(job_dir: Path) -> list[Path]:
    return sorted(
        child
        for child in job_dir.iterdir()
        if child.is_dir() and (child / "result.json").exists()
    )


def _load_trial_results(job_dir: Path) -> list[TrialResult]:
    return [
        TrialResult.model_validate_json((trial_dir / "result.json").read_text(encoding="utf-8"))
        for trial_dir in _iter_trial_dirs(job_dir)
    ]


def _existing_job_id(job_dir: Path) -> UUID | None:
    result_path = job_dir / "result.json"
    if not result_path.exists():
        return None
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    raw_id = payload.get("id")
    if not raw_id:
        return None
    try:
        return UUID(str(raw_id))
    except ValueError:
        return None


async def _build_job_stats(
    config: JobConfig,
    trial_results: list[TrialResult],
) -> JobStats:
    final_rewards: dict[str, list[object | None]] = defaultdict(list)

    for trial_result in trial_results:
        evals_key = JobStats.format_agent_evals_key(
            trial_result.agent_info.name,
            (
                trial_result.agent_info.model_info.name
                if trial_result.agent_info.model_info
                else None
            ),
            trial_result.source or "adhoc",
        )
        if trial_result.verifier_result is not None:
            final_rewards[evals_key].append(trial_result.verifier_result.rewards)
        else:
            final_rewards[evals_key].append(None)

    final_stats = JobStats.from_trial_results(
        trial_results,
        n_total_trials=len(trial_results),
        n_retries=0,
    )

    metrics_by_dataset = await Job._resolve_metrics(config, [])
    for evals_key, rewards in final_rewards.items():
        dataset_name = evals_key.split("__")[-1]
        for metric in metrics_by_dataset[dataset_name]:
            final_stats.evals[evals_key].metrics.append(metric.compute(rewards))

    for evals_key, pass_at_k in compute_pass_at_k_by_evals(trial_results).items():
        final_stats.evals[evals_key].pass_at_k = pass_at_k

    return final_stats


async def _finalize_job_dir(job_dir: Path) -> None:
    config_path = job_dir / "config.json"
    if not config_path.exists():
        raise RuntimeError(f"missing job config: {config_path}")

    config = JobConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
    trial_results = _load_trial_results(job_dir)
    if not trial_results:
        raise RuntimeError(f"no Harbor trial results found in {job_dir}")

    stats = await _build_job_stats(config, trial_results)

    started_values = [trial.started_at for trial in trial_results if trial.started_at]
    finished_values = [trial.finished_at for trial in trial_results if trial.finished_at]
    now = datetime.now()
    started_at = min(started_values) if started_values else now
    updated_at = max(finished_values or started_values) if (finished_values or started_values) else now
    finished_at = max(finished_values) if len(finished_values) == len(trial_results) else None

    job_result = JobResult(
        id=_existing_job_id(job_dir) or uuid4(),
        started_at=started_at,
        updated_at=updated_at,
        finished_at=finished_at,
        n_total_trials=len(trial_results),
        stats=stats,
    )
    (job_dir / "result.json").write_text(
        job_result.model_dump_json(indent=2, exclude={"trial_results"}),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-dir", required=True, type=Path)
    args = parser.parse_args()
    job_dir = args.job_dir.expanduser().resolve()
    if not job_dir.is_dir():
        raise SystemExit(f"job dir not found: {job_dir}")
    asyncio.run(_finalize_job_dir(job_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
