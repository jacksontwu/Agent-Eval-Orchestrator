from typing import Literal
from app.schema.common import ApiModel


class AssetEntry(ApiModel):
    path: str
    size: int
    sha256: str
    kind: Literal["case", "bitfun", "cli"]


class AssetManifest(ApiModel):
    asset_manifest_id: str
    target_root_rel: str
    entries: list[AssetEntry]
