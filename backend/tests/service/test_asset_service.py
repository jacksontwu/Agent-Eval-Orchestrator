import hashlib

from app.model import repo_batches, repo_runs
from app.service import asset_service


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _seed(session, tmp_path):
    dataset = tmp_path / "dataset"
    (dataset / "c1").mkdir(parents=True)
    (dataset / "c1" / "task.toml").write_bytes(b"hello")
    (dataset / "c1" / "sub").mkdir()
    (dataset / "c1" / "sub" / "f.txt").write_bytes(b"world")
    (dataset / "c2").mkdir()
    (dataset / "c2" / "task.toml").write_bytes(b"other")

    cli = tmp_path / "bitfun-cli"
    cli.write_bytes(b"clibytes")

    config_dir = tmp_path / "bitfun-config"
    config_dir.mkdir()
    (config_dir / "config").write_bytes(b"cfg")

    run = repo_runs.create_run(session, template_id="tpl-1", owner="alice", display_name="R1")
    session.commit()
    batch = repo_batches.create_batch(
        session, run_id=run.run_id, owner="alice", executor_kind="harbor",
        selected_case_ids=["c1"], batch_options={}, batch_root="/tmp/b",
        executor_metadata={
            "datasetPath": str(dataset),
            "bitfunCliPath": str(cli),
            "bitfunConfigDir": str(config_dir),
        },
    )
    session.commit()
    return run, batch, dataset, cli, config_dir


def test_build_manifest(session, tmp_path):
    run, batch, dataset, cli, config_dir = _seed(session, tmp_path)
    manifest = asset_service.build_manifest(session, batch.batch_id)

    assert manifest.target_root_rel == f"sync/{run.run_id}"
    assert manifest.asset_manifest_id == f"am-{batch.batch_id}"

    by_path = {e.path: e for e in manifest.entries}
    # only c1 cases, not c2
    assert not any(p.startswith("cases/c2") for p in by_path)
    assert by_path["cases/c1/task.toml"].sha256 == _sha(b"hello")
    assert by_path["cases/c1/task.toml"].kind == "case"
    assert by_path["cases/c1/sub/f.txt"].sha256 == _sha(b"world")
    assert by_path["cli/bitfun-cli"].sha256 == _sha(b"clibytes")
    assert by_path["cli/bitfun-cli"].kind == "cli"
    assert by_path["bitfun/config"].sha256 == _sha(b"cfg")
    assert by_path["bitfun/config"].kind == "bitfun"


def test_open_entry_resolves_and_rejects_traversal(session, tmp_path):
    run, batch, dataset, cli, config_dir = _seed(session, tmp_path)
    am_id = f"am-{batch.batch_id}"
    resolved = asset_service.open_entry(session, am_id, "cases/c1/task.toml")
    assert resolved.read_bytes() == b"hello"

    import pytest
    with pytest.raises(Exception):
        asset_service.open_entry(session, am_id, "cases/../../etc/passwd")
