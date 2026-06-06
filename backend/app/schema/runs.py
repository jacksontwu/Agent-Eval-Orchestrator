from typing import Any

from app.schema.common import ApiModel


class RunCreate(ApiModel):
    template_id: str
    owner: str = "demo"
    display_name: str


class RunRead(ApiModel):
    run_id: str
    template_id: str
    owner: str
    display_name: str
    bound_worker_id: str | None = None
    latest_batch_id: str | None = None
    parent_run_id: str | None = None
    sync_status: str
    sync_job_id: str | None = None
    sync_manifest: dict[str, Any]
    rerun_status: str
    rerun_job_id: str | None = None
    created_at: str
    updated_at: str
