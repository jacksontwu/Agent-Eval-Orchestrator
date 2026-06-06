from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def case_is_errored(case: dict[str, Any]) -> bool:
    status = str(case.get("status") or "")
    if status == "errored":
        return True
    return status == "failed" and bool(case.get("error_text"))


def case_is_failed(case: dict[str, Any]) -> bool:
    return str(case.get("status") or "") == "failed" and not case.get("error_text")


def overall_status_from_batch_counts(status_counts: dict[str, int], has_primary_batches: bool) -> str:
    if status_counts["running"] > 0:
        return "running"
    if status_counts["pending_sync"] > 0:
        return "syncing"
    if status_counts["failed"] > 0 or status_counts["sync_failed"] > 0:
        return "failed"
    if status_counts["queued"] > 0:
        return "queued"
    if status_counts["succeeded"] > 0 and sum(status_counts.values()) == status_counts["succeeded"]:
        return "finished"
    if has_primary_batches:
        return "mixed"
    return "idle"


def case_error_type(case: dict[str, Any]) -> str:
    metrics = case.get("metrics") or {}
    raw = case.get("errorType") or metrics.get("errorType")
    if raw is None or str(raw).strip() == "":
        return "(unknown)"
    return str(raw).strip()


def harbor_trial_case_id(trial_dir: Path) -> str:
    result_path = trial_dir / "result.json"
    if result_path.exists():
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        for key in ("task_name", "instance_id", "case_id"):
            value = str(payload.get(key) or "").strip()
            if value:
                return value
    name = trial_dir.name
    return name.rsplit("__", 1)[0].strip() if "__" in name else name.strip()


def exception_type_from_text(text: str) -> str:
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        candidate = stripped.split(":", 1)[0].strip()
        if candidate and candidate.replace("_", "").replace(".", "").isalnum():
            return candidate.rsplit(".", 1)[-1]
    return "UnknownException"
