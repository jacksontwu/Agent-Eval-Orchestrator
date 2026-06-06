from typing import Any

from app.schema.common import ApiModel


class WorkerRead(ApiModel):
    worker_id: str
    display_name: str
    host: str
    slots_total: int
    slots_used: int
    capabilities: dict[str, Any]
    status: str
    enabled: bool
    note: str
    tags: list[str]
    allocation_weight: float
    last_heartbeat_at: str | None = None


class WorkerSettingsUpdate(ApiModel):
    enabled: bool | None = None
    note: str | None = None
    tags: list[str] | None = None
    allocation_weight: float | None = None
