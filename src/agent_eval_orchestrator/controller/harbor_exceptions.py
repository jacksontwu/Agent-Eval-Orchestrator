from __future__ import annotations

import json
from pathlib import Path


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
