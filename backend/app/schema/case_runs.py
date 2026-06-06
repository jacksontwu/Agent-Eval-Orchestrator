from typing import Any

from app.schema.common import ApiModel


class CaseRunRead(ApiModel):
    case_run_id: str
    batch_id: str
    case_id: str
    status: str
    score: float | None = None
    metrics: dict[str, Any]
    artifact_index: dict[str, Any]
    error_text: str | None = None
    created_at: str
    updated_at: str
