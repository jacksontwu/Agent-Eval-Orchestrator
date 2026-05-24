from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_eval_orchestrator.controller.provisioner import Provisioner
from agent_eval_orchestrator.core.ids import new_id


@pytest.fixture()
def provisioner(store, sample_ssh_config, tmp_path: Path):
    bootstrap = tmp_path / "bootstrap.sh"
    bootstrap.write_text("#!/bin/bash\n", encoding="utf-8")
    return Provisioner(
        store=store,
        ssh_config_path=sample_ssh_config,
        auth_token="test-token",
        controller_port=8790,
        bootstrap_script_path=bootstrap,
        tunnel_state_path=store.layout.controller_dir / "tunnels.json",
    )


def test_fresh_mode_step_order(provisioner, store, monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        result = MagicMock()
        result.returncode = 0
        result.stdout = "uv 0.5.0\n"
        result.stderr = ""
        return result

    monkeypatch.setattr("agent_eval_orchestrator.controller.provisioner.subprocess.run", fake_run)
    monkeypatch.setattr(
        "agent_eval_orchestrator.controller.ssh_config.test_ssh_alias",
        lambda *args, **kwargs: (True, "connected"),
    )
    monkeypatch.setattr(
        provisioner,
        "_find_tunnel_pid",
        lambda *args, **kwargs: 999,
    )
    monkeypatch.setattr(provisioner, "_wait_for_register", lambda *args, **kwargs: None)

    worker_id = "ecs-worker-0004"
    store.create_provisioning_worker(
        worker_id=worker_id,
        display_name=worker_id,
        slots_total=1,
        ssh_host_alias="aeo-ecs-0004",
        ssh_bootstrap_host_alias="aeo-ecs-0004-root",
        tunnel_remote_port=17380,
    )
    job_id = new_id("prov")
    store.create_provision_job(
        job_id=job_id,
        worker_id=worker_id,
        mode="fresh",
        steps=provisioner.initial_steps("fresh"),
    )

    provisioner.run_job(
        job_id=job_id,
        worker_id=worker_id,
        mode="fresh",
        ssh_host_alias="aeo-ecs-0004",
        ssh_bootstrap_host_alias="aeo-ecs-0004-root",
        djn_password="pw",
        tunnel_remote_port=17380,
        display_name=worker_id,
        slots_total=1,
    )

    joined = " ".join(" ".join(call) for call in calls)
    assert "scp" in joined
    assert "aeo-bootstrap.sh" in joined or "/tmp/aeo-bootstrap.sh" in joined
    assert "DJN_PASSWORD=" not in store.get_provision_job(job_id)["log_text"]
    job = store.get_provision_job(job_id)
    assert job["status"] == "succeeded"


def test_join_mode_skips_bootstrap(provisioner, store, monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        result = MagicMock()
        result.returncode = 0
        result.stdout = "uv 0.5.0\n"
        result.stderr = ""
        return result

    monkeypatch.setattr("agent_eval_orchestrator.controller.provisioner.subprocess.run", fake_run)
    monkeypatch.setattr(
        "agent_eval_orchestrator.controller.ssh_config.test_ssh_alias",
        lambda *args, **kwargs: (True, "connected"),
    )
    monkeypatch.setattr(provisioner, "_find_tunnel_pid", lambda *args, **kwargs: 999)
    monkeypatch.setattr(provisioner, "_wait_for_register", lambda *args, **kwargs: None)

    worker_id = "ecs-worker-0005"
    store.create_provisioning_worker(
        worker_id=worker_id,
        display_name=worker_id,
        slots_total=1,
        ssh_host_alias="aeo-ecs-0004",
        ssh_bootstrap_host_alias=None,
        tunnel_remote_port=17380,
    )
    job_id = new_id("prov")
    store.create_provision_job(
        job_id=job_id,
        worker_id=worker_id,
        mode="join",
        steps=provisioner.initial_steps("join"),
    )

    provisioner.run_job(
        job_id=job_id,
        worker_id=worker_id,
        mode="join",
        ssh_host_alias="aeo-ecs-0004",
        ssh_bootstrap_host_alias=None,
        djn_password=None,
        tunnel_remote_port=17380,
        display_name=worker_id,
        slots_total=1,
    )

    joined = " ".join(" ".join(call) for call in calls)
    assert "scp" not in joined
    assert store.get_provision_job(job_id)["status"] == "succeeded"
