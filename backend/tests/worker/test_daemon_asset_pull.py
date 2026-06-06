import hashlib

import pytest

from app.schema.assets import AssetEntry, AssetManifest
from app.worker import daemon


def _entry(path: str, data: bytes, kind="case") -> AssetEntry:
    return AssetEntry(path=path, size=len(data), sha256=hashlib.sha256(data).hexdigest(), kind=kind)


def test_pull_assets_writes_and_verifies(tmp_path):
    files = {"cases/c1/task.toml": b"hello", "cli/bitfun-cli": b"clibytes"}
    manifest = AssetManifest(
        asset_manifest_id="am-1",
        target_root_rel="sync/run-1",
        entries=[_entry("cases/c1/task.toml", files["cases/c1/task.toml"]),
                 _entry("cli/bitfun-cli", files["cli/bitfun-cli"], kind="cli")],
    )

    def fetch(url: str) -> bytes:
        # url like {base}/file?path=cases/c1/task.toml
        path = url.split("path=", 1)[1]
        return files[path]

    daemon.pull_assets(manifest, base_url="http://ctrl/api/workers/assets/am-1",
                       target_root=tmp_path, fetch=fetch)

    assert (tmp_path / "cases/c1/task.toml").read_bytes() == b"hello"
    assert (tmp_path / "cli/bitfun-cli").read_bytes() == b"clibytes"


def test_pull_assets_retries_then_succeeds(tmp_path):
    data = b"payload"
    manifest = AssetManifest(
        asset_manifest_id="am-1", target_root_rel="sync/run-1",
        entries=[_entry("cases/c1/x", data)],
    )
    attempts = {"n": 0}

    def flaky_fetch(url: str) -> bytes:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise OSError("transient")
        return data

    daemon.pull_assets(manifest, base_url="http://ctrl/api/workers/assets/am-1",
                       target_root=tmp_path, fetch=flaky_fetch, max_retries=3)
    assert attempts["n"] == 3
    assert (tmp_path / "cases/c1/x").read_bytes() == data


def test_pull_assets_raises_on_checksum_mismatch(tmp_path):
    manifest = AssetManifest(
        asset_manifest_id="am-1", target_root_rel="sync/run-1",
        entries=[AssetEntry(path="cases/c1/x", size=4, sha256="deadbeef", kind="case")],
    )

    def fetch(url: str) -> bytes:
        return b"data"

    with pytest.raises(Exception):
        daemon.pull_assets(manifest, base_url="http://ctrl/api/workers/assets/am-1",
                           target_root=tmp_path, fetch=fetch, max_retries=2)
