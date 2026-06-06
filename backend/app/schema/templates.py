from typing import Any

from app.schema.common import ApiModel


class TemplateCreate(ApiModel):
    owner: str = "demo"
    name: str
    dataset_ref: str
    executor_kind: str = "harbor"
    executor_config: dict[str, Any] = {}
    model_profile_ref: str | None = None
    note: str = ""


class TemplateRead(ApiModel):
    template_id: str
    owner: str
    name: str
    dataset_ref: str
    executor_kind: str
    executor_config: dict[str, Any]
    model_profile_ref: str | None = None
    note: str
    created_at: str
    updated_at: str
