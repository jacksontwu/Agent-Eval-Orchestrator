from unittest.mock import MagicMock, patch
from pathlib import Path
from agent_eval_orchestrator.controller.provisioner import Provisioner, initial_steps_for_mode


def _provisioner(store, ssh_config: Path) -> Provisioner:
    return Provisioner(
        store=store,
        ssh_config_path=ssh_config,
        auth_token="secret",
        controller_port=7380,
        bootstrap_script_path=ssh_config.parent / "bootstrap.sh",
        tunnel_state_path=store.layout.controller_dir / "tunnels.json",
    )


def test_run_job_direct_skips_tunnel(store, sample_ssh_config):
    provisioner = _provisioner(store, sample_ssh_config)
    store.create_provisioning_worker(
        worker_id="w-direct",
        display_name="w-direct",
        slots_total=1,
        ssh_host_alias="aeo-ecs-0004",
        ssh_bootstrap_host_alias=None,
        connection_mode="direct",
        controller_internal_ip="192.168.0.211",
        tunnel_remote_port=None,
    )
    job_id = "prov-direct"
    store.create_provision_job(
        job_id=job_id,
        worker_id="w-direct",
        mode="join",
        steps=provisioner.initial_steps("join", connection_mode="direct"),
    )
    with patch.object(provisioner, "_establish_tunnel") as tunnel, \
         patch.object(provisioner, "_validate_ssh"), \
         patch.object(provisioner, "_verify_layout"), \
         patch.object(provisioner, "_start_daemon") as start_daemon, \
         patch.object(provisioner, "_wait_for_register"):
        provisioner.run_job(
            job_id=job_id,
            worker_id="w-direct",
            mode="join",
            ssh_host_alias="aeo-ecs-0004",
            ssh_bootstrap_host_alias=None,
            djn_password=None,
            connection_mode="direct",
            controller_internal_ip="192.168.0.211",
            tunnel_remote_port=None,
            display_name="w-direct",
            slots_total=1,
        )
    tunnel.assert_not_called()
    start_daemon.assert_called_once()
    _, kwargs = start_daemon.call_args
    assert kwargs["controller_url"] == "http://192.168.0.211:7380"


def test_initial_steps_direct_join_excludes_tunnel():
    steps = initial_steps_for_mode("join", connection_mode="direct")
    assert [s["id"] for s in steps] == [
        "validate_ssh",
        "verify_layout",
        "start_daemon",
        "wait_register",
    ]


def test_initial_steps_tunnel_join_includes_tunnel():
    steps = initial_steps_for_mode("join", connection_mode="tunnel")
    assert [s["id"] for s in steps] == [
        "validate_ssh",
        "verify_layout",
        "establish_tunnel",
        "start_daemon",
        "wait_register",
    ]


def test_initial_steps_direct_fresh_excludes_tunnel():
    steps = initial_steps_for_mode("fresh", connection_mode="direct")
    assert "establish_tunnel" not in [s["id"] for s in steps]
    assert steps[0]["id"] == "validate_ssh"
    assert "bootstrap" in [s["id"] for s in steps]
