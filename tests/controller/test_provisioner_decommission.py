from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_eval_orchestrator.controller.provisioner import Provisioner


def _provisioner(store, ssh_config: Path) -> Provisioner:
    return Provisioner(
        store=store,
        ssh_config_path=ssh_config,
        auth_token="secret",
        controller_port=7380,
        bootstrap_script_path=ssh_config.parent / "bootstrap.sh",
        tunnel_state_path=store.layout.controller_dir / "tunnels.json",
    )


def test_decommission_worker_skipped_without_ssh_alias(store, sample_ssh_config):
    provisioner = _provisioner(store, sample_ssh_config)
    result = provisioner.decommission_worker(worker_id="w1", ssh_host_alias=None)
    assert result == {"remoteCleanup": "skipped", "warnings": []}


def test_decommission_worker_done(store, sample_ssh_config):
    provisioner = _provisioner(store, sample_ssh_config)
    provisioner.tunnels.kill_tunnel = MagicMock()
    with patch.object(provisioner, "_ssh_run") as ssh_run:
        ssh_run.return_value.returncode = 0
        ssh_run.return_value.stderr = ""
        result = provisioner.decommission_worker(worker_id="w1", ssh_host_alias="aeo-ecs-0004")
    provisioner.tunnels.kill_tunnel.assert_called_once_with("w1")
    ssh_run.assert_called_once()
    assert result["remoteCleanup"] == "done"
    assert result["warnings"] == []


def test_decommission_worker_partial_on_tunnel_failure(store, sample_ssh_config):
    provisioner = _provisioner(store, sample_ssh_config)
    provisioner.tunnels.kill_tunnel = MagicMock(side_effect=RuntimeError("no pid"))
    with patch.object(provisioner, "_ssh_run") as ssh_run:
        ssh_run.return_value.returncode = 0
        ssh_run.return_value.stderr = ""
        result = provisioner.decommission_worker(worker_id="w1", ssh_host_alias="aeo-ecs-0004")
    assert result["remoteCleanup"] == "partial"
    assert any("failed to kill tunnel" in item for item in result["warnings"])


def test_cancel_job_uses_decommission_worker(store, sample_ssh_config):
    provisioner = _provisioner(store, sample_ssh_config)
    job_id = "prov-test"
    store.create_provision_job(
        job_id=job_id,
        worker_id="w1",
        mode="join",
        steps=[{"id": "validate_ssh", "label": "校验 SSH 连接", "status": "pending"}],
    )
    with patch.object(provisioner, "decommission_worker", return_value={"remoteCleanup": "done", "warnings": []}) as decommission:
        provisioner.cancel_job(job_id, worker_id="w1", ssh_host_alias="aeo-ecs-0004")
    decommission.assert_called_once_with(
        worker_id="w1",
        ssh_host_alias="aeo-ecs-0004",
        connection_mode="tunnel",
    )
    job = store.get_provision_job(job_id)
    assert job is not None
    assert job["status"] == "cancelled"
