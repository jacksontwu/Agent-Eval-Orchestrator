from typing import Any
from app.schema.common import ApiModel
from app.schema.assets import AssetManifest


class RegisterRequest(ApiModel):
    worker_id: str
    display_name: str
    host: str
    slots_total: int
    capabilities: dict[str, Any] = {}


class RegisterResponse(ApiModel):
    ok: bool = True
    worker_id: str


class ClaimRequest(ApiModel):
    worker_id: str


class ClaimResponse(ApiModel):
    batch_id: str | None = None
    dataset_ref: str | None = None
    executor_config: dict[str, Any] = {}
    asset_manifest_id: str | None = None
    asset_url: str | None = None
    asset_manifest: AssetManifest | None = None


class HeartbeatRequest(ApiModel):
    worker_id: str
    batch_id: str | None = None
    status: str | None = None          # running | succeeded | failed | sync_failed
    slots_used: int | None = None
    summary: dict[str, Any] | None = None
    cases: list[dict[str, Any]] | None = None
    error_text: str | None = None
    finished: bool = False


class HeartbeatResponse(ApiModel):
    ok: bool = True


class JobArchiveResponse(ApiModel):
    ok: bool = True
    batch_id: str
