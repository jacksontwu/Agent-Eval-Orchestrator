from typing import Any

from app.schema.common import ApiModel


class RunCreate(ApiModel):
    template_id: str
    owner: str = "demo"
    display_name: str


class CreateDistributeRequest(ApiModel):
    name: str
    owner: str = "demo"
    dataset_path: str                       # absolute path to the downloaded dataset on the controller
    bitfun_cli_path: str
    bitfun_config_dir: str
    selected_case_ids: list[str] = []       # empty -> all cases under dataset_path
    worker_ids: list[str] = []              # empty -> all enabled online workers
    per_worker_concurrency: int = 1
    executor_config: dict = {}
    model_profile_ref: str | None = None


class CreateDistributeResponse(ApiModel):
    run_id: str
    batch_ids: list[str]


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
