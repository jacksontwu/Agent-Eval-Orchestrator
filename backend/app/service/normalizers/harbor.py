from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from app.core.ids import sanitize_name


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _duration_ms(started_at: str | None, finished_at: str | None) -> int | None:
    if not started_at or not finished_at:
        return None
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, int((end - start).total_seconds() * 1000))


def _timing_duration(payload: dict[str, Any] | None) -> int | None:
    if not isinstance(payload, dict):
        return None
    return _duration_ms(
        payload.get("started_at"),
        payload.get("finished_at"),
    )


def _trajectory_summary(trial_dir: Path) -> dict[str, Any]:
    trajectory_path = trial_dir / "agent" / "trajectory.json"
    if not trajectory_path.exists():
        return {
            "hasTrajectory": False,
            "trajectoryPath": None,
            "stepCount": 0,
            "toolCallCount": 0,
            "toolSummary": {},
        }
    payload = _load_json(trajectory_path) or {}
    steps = payload.get("steps") or []
    step_count = len(steps) if isinstance(steps, list) else 0
    counter: Counter[str] = Counter()
    total_tool_calls = 0
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict):
                continue
            tool_calls = step.get("tool_calls") or []
            if not isinstance(tool_calls, list):
                continue
            total_tool_calls += len(tool_calls)
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                tool_name = str(tool_call.get("function_name") or "unknown")
                counter[tool_name] += 1
    return {
        "hasTrajectory": True,
        "trajectoryPath": str(trajectory_path),
        "stepCount": step_count,
        "toolCallCount": total_tool_calls,
        "toolSummary": dict(counter),
    }


def normalize_harbor_job(job_dir: Path, batch_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    job_result = _load_json(job_dir / "result.json") or {}
    stats = job_result.get("stats") or {}
    total = int(job_result.get("n_total_trials") or 0)
    completed = int(stats.get("n_completed_trials") or 0)
    errored = int(stats.get("n_errored_trials") or 0)
    cancelled = int(stats.get("n_cancelled_trials") or 0)
    pending = int(stats.get("n_pending_trials") or max(total - completed, 0))

    cases: list[dict[str, Any]] = []
    for trial_dir in sorted(job_dir.iterdir()) if job_dir.exists() else []:
        if not trial_dir.is_dir():
            continue
        trial_result = _load_json(trial_dir / "result.json")
        if not trial_result:
            continue
        rewards = ((trial_result.get("verifier_result") or {}).get("rewards") or {})
        reward_value = rewards.get("reward")
        exception_info = trial_result.get("exception_info")
        agent_result = trial_result.get("agent_result") or {}
        agent_info = trial_result.get("agent_info") or {}
        model_info = agent_info.get("model_info") or {}
        trajectory_summary = _trajectory_summary(trial_dir)
        if exception_info:
            status = "errored"
        elif reward_value is None:
            status = "succeeded"
        else:
            status = "succeeded" if float(reward_value) >= 1.0 else "failed"
        trial_name = str(trial_result.get("trial_name") or trial_dir.name)
        inferred_case_id = trial_name.rsplit("__", 1)[0].strip() or trial_dir.name
        case_id = inferred_case_id
        input_tokens = agent_result.get("n_input_tokens")
        cached_input_tokens = agent_result.get("n_cache_tokens")
        output_tokens = agent_result.get("n_output_tokens")
        uncached_input_tokens = None
        if isinstance(input_tokens, int):
            uncached_input_tokens = input_tokens
            if isinstance(cached_input_tokens, int):
                uncached_input_tokens = max(0, input_tokens - cached_input_tokens)
        cases.append(
            {
                "caseId": case_id,
                "status": status,
                "score": reward_value,
                "metrics": rewards,
                "errorText": (exception_info or {}).get("exception_message"),
                "errorType": (exception_info or {}).get("exception_type"),
                "trialName": trial_name,
                "taskName": str(trial_result.get("task_name") or ""),
                "startedAt": trial_result.get("started_at"),
                "finishedAt": trial_result.get("finished_at"),
                "durationMs": _duration_ms(
                    trial_result.get("started_at"),
                    trial_result.get("finished_at"),
                ),
                "environmentSetupMs": _timing_duration(trial_result.get("environment_setup")),
                "agentSetupMs": _timing_duration(trial_result.get("agent_setup")),
                "agentExecutionMs": _timing_duration(trial_result.get("agent_execution")),
                "verifierMs": _timing_duration(trial_result.get("verifier")),
                "inputTokens": uncached_input_tokens,
                "cachedInputTokens": cached_input_tokens,
                "outputTokens": output_tokens,
                "costUsd": agent_result.get("cost_usd"),
                "agentName": agent_info.get("name"),
                "agentVersion": agent_info.get("version"),
                "modelName": model_info.get("name"),
                "provider": model_info.get("provider"),
                **trajectory_summary,
                "artifactIndex": {
                    "trialDir": str(trial_dir),
                    "resultPath": str(trial_dir / "result.json"),
                    "logPath": str(trial_dir / "trial.log"),
                    "agentDir": str(trial_dir / "agent"),
                    "verifierDir": str(trial_dir / "verifier"),
                    "artifactsDir": str(trial_dir / "artifacts"),
                    "trajectoryPath": trajectory_summary["trajectoryPath"],
                },
            }
        )

    succeeded = sum(1 for case in cases if case["status"] == "succeeded")
    failed = sum(1 for case in cases if case["status"] == "failed")
    errored_count = sum(1 for case in cases if case["status"] == "errored")
    summary = {
        "batchId": batch_id,
        "total": total or len(cases),
        "completed": completed or len(cases),
        "succeeded": succeeded,
        "failed": failed,
        "errored": errored_count or errored,
        "cancelled": cancelled,
        "pending": pending if total else 0,
        "rawStats": stats,
    }
    artifact_index = {
        "jobDir": str(job_dir),
        "jobResultPath": str(job_dir / "result.json"),
        "casesDir": [str(Path(case["artifactIndex"]["trialDir"])) for case in cases],
    }
    return summary, cases, artifact_index


def write_normalized_snapshot(batch_root: Path, summary: dict[str, Any], cases: list[dict[str, Any]]) -> None:
    normalized_root = batch_root / "normalized"
    cases_root = normalized_root / "cases"
    cases_root.mkdir(parents=True, exist_ok=True)
    (normalized_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for case in cases:
        case_id = str(case["caseId"])
        (cases_root / f"{sanitize_name(case_id)}.json").write_text(
            json.dumps(case, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
