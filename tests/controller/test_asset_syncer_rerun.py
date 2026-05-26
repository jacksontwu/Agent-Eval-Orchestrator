from unittest.mock import patch

from agent_eval_orchestrator.controller.asset_syncer import AssetSyncer
from conftest import seed_finished_run_with_cases


def test_sync_rerun_job_promotes_rerun_batches(store, tmp_path, sample_ssh_config):
    run, parent = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom"}],
    )
    store.update_run_sync_fields(
        run_id=run["run_id"],
        sync_status="succeeded",
        sync_manifest={
            "datasetPath": str(tmp_path / "dataset"),
            "bitfunCliPath": str(tmp_path / "bitfun-cli"),
            "bitfunConfigDir": str(tmp_path / "bitfun-config"),
            "workers": {
                "worker-a": {
                    "caseIds": ["exc-a"],
                    "targetRoot": str(tmp_path / "shared" / "sync" / run["run_id"]),
                    "transport": "local",
                }
            },
        },
    )
    rerun = store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["exc-a"],
        preferred_worker_id="worker-a",
        batch_options={},
        initial_status="pending_sync",
        batch_kind="exception_rerun",
        parent_batch_id=parent["batch_id"],
    )
    job = store.create_run_rerun_job(
        job_id="rerun-1",
        run_id=run["run_id"],
        case_ids=["exc-a"],
        worker_shards={"worker-a": ["exc-a"]},
        rerun_batches={"worker-a": rerun["batch_id"]},
    )
    syncer = AssetSyncer(
        store=store,
        ssh_config_path=sample_ssh_config,
        controller_shared_root=tmp_path,
    )
    dataset = tmp_path / "dataset" / "exc-a"
    dataset.mkdir(parents=True)
    (dataset / "task.toml").write_text("", encoding="utf-8")
    bitfun_cli = tmp_path / "bitfun-cli"
    bitfun_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    bitfun_cli.chmod(0o755)
    (tmp_path / "bitfun-config").mkdir()

    with patch.object(syncer, "_sync_cases"), patch.object(syncer, "_sync_bitfun"):
        syncer.sync_rerun_job(job_id=job["job_id"], run_id=run["run_id"])

    promoted = store.get_batch(rerun["batch_id"])
    assert promoted["status"] == "queued"
    updated_run = store.get_run(run["run_id"])
    assert updated_run["rerun_status"] == "running"
