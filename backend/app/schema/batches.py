from typing import Any

from app.schema.common import ApiModel


class BatchRead(ApiModel):
    batch_id: str
    run_id: str
    owner: str
    status: str
    current_step: str | None = None
    preferred_worker_id: str | None = None
    assigned_worker_id: str | None = None
    executor_kind: str
    executor_metadata: dict[str, Any]
    selected_case_ids: list[str]
    batch_options: dict[str, Any]
    summary: dict[str, Any]
    artifact_index: dict[str, Any]
    batch_root: str
    parent_batch_id: str | None = None
    batch_kind: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error_text: str | None = None
