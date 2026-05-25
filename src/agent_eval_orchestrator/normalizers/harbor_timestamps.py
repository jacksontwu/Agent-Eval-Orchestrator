from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_JOB_RESULT_TIME_FIELDS = ("started_at", "updated_at", "finished_at")


def parse_harbor_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def to_harbor_naive_utc_iso(dt: datetime) -> str:
    """Harbor viewer legacy jobs use naive UTC ISO strings without a Z suffix."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat()


def normalize_timestamp_value(value: Any) -> Any:
    if value is None or not isinstance(value, str) or not value.strip():
        return value
    return to_harbor_naive_utc_iso(parse_harbor_timestamp(value))


def normalize_job_result_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    for field in _JOB_RESULT_TIME_FIELDS:
        if field in normalized:
            normalized[field] = normalize_timestamp_value(normalized[field])
    return normalized


def normalize_job_result_file(result_path: Path) -> bool:
    if not result_path.exists():
        return False
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return False
    normalized = normalize_job_result_payload(payload)
    if normalized == payload:
        return False
    result_path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return True


def normalize_jobs_dir(jobs_dir: Path) -> int:
    """Normalize job-level result.json timestamps so Harbor viewer can sort jobs."""
    if not jobs_dir.exists():
        return 0
    changed = 0
    for child in jobs_dir.iterdir():
        if not child.is_dir():
            continue
        result_path = child / "result.json"
        if normalize_job_result_file(result_path):
            changed += 1
    return changed
