from app.schema.worker_protocol import ClaimResponse, RegisterRequest
from app.schema.assets import AssetEntry, AssetManifest


def test_register_request_camel():
    req = RegisterRequest.model_validate(
        {"workerId": "w1", "displayName": "W1", "host": "h", "slotsTotal": 2, "capabilities": {}}
    )
    assert req.worker_id == "w1" and req.slots_total == 2


def test_claim_response_carries_asset_contract():
    manifest = AssetManifest(
        asset_manifest_id="am-1",
        target_root_rel="sync/run-1",
        entries=[AssetEntry(path="cases/c1", size=10, sha256="abc", kind="case")],
    )
    resp = ClaimResponse(
        batch_id="batch-1", dataset_ref="d/x", executor_config={},
        asset_manifest_id="am-1", asset_url="/api/workers/assets/am-1", asset_manifest=manifest,
    )
    dumped = resp.model_dump(by_alias=True)
    assert dumped["assetManifestId"] == "am-1"
    assert dumped["assetManifest"]["entries"][0]["sha256"] == "abc"
