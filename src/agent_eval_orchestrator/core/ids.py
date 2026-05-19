from __future__ import annotations

from datetime import datetime, timezone
import re
from uuid import uuid4


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def safe_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def sanitize_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    cleaned = cleaned.strip(".-_")
    return cleaned or "unknown"


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"
