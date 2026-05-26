from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_eval_orchestrator.controller.provisioner import Provisioner
from agent_eval_orchestrator.controller.worker_updater import WorkerUpdater, build_git_pull_command
from agent_eval_orchestrator.core.ids import new_id


@pytest.fixture()
def updater(store, sample_ssh_config, tmp_path):
    bootstrap = tmp_path / "bootstrap.sh"
    bootstrap.write_text("#!/bin/bash\n", encoding="utf-8")
    provisioner = Provisioner(
        store=store,
        ssh_config_path=sample_ssh_config,
        auth_token="test-token",
        controller_port=8790,
        bootstrap_script_path=bootstrap,
        tunnel_state_path=store.layout.controller_dir / "tunnels.json",
    )
    return WorkerUpdater(
        store=store,
        ssh_config_path=sample_ssh_config,
        auth_token="test-token",
        controller_port=8790,
        provisioner=provisioner,
    )


def test_initial_steps_both_targets(updater):
    steps = updater.initial_steps(["aeo", "harbor"])
    ids = [step["id"] for step in steps]
    assert ids == [
        "validate_ssh",
        "stop_daemon",
        "pull_aeo",
        "sync_aeo",
        "pull_harbor",
        "restart_daemon",
        "wait_register",
    ]


def test_initial_steps_aeo_only(updater):
    steps = updater.initial_steps(["aeo"])
    ids = [step["id"] for step in steps]
    assert ids == [
        "validate_ssh",
        "stop_daemon",
        "pull_aeo",
        "sync_aeo",
        "restart_daemon",
        "wait_register",
    ]
    assert "pull_harbor" not in ids


def test_initial_steps_harbor_only(updater):
    steps = updater.initial_steps(["harbor"])
    ids = [step["id"] for step in steps]
    assert ids == [
        "validate_ssh",
        "stop_daemon",
        "pull_harbor",
        "restart_daemon",
        "wait_register",
    ]
    assert "pull_aeo" not in ids
    assert "sync_aeo" not in ids


def test_resolve_paths_from_shared_root(updater):
    worker = {
        "capabilities": {
            "sharedRoot": "/home/djn/worker/agent-eval-orchestrator/runtime",
        }
    }
    paths = updater.resolve_paths(worker)
    assert paths["aeo_dir"] == "/home/djn/worker/agent-eval-orchestrator"
    assert paths["harbor_dir"] == "/home/djn/worker/harbor"
    assert paths["uv_bin"] == "/home/djn/.local/bin/uv"
    assert paths["shared_root"] == "/home/djn/worker/agent-eval-orchestrator/runtime"
    assert paths["log_dir"] == "/home/djn/worker/logs"


def test_resolve_paths_missing_shared_root(updater):
    with pytest.raises(RuntimeError, match="sharedRoot"):
        updater.resolve_paths({"capabilities": {}})


def test_build_git_pull_command_without_token():
    cmd = build_git_pull_command("/home/djn/worker/harbor")
    assert "GIT_TERMINAL_PROMPT=0" in cmd
    assert "git fetch --prune" in cmd
    assert "git merge --ff-only" in cmd
    assert "git clean -f uv.lock" in cmd
    assert "AEO_GITHUB_TOKEN" not in cmd


def test_build_git_pull_command_with_token():
    cmd = build_git_pull_command("/home/djn/worker/agent-eval-orchestrator", github_token="ghp_secret")
    assert "username=JinnanDuan" in cmd
    assert "password='ghp_secret'" in cmd or "password=ghp_secret" in cmd
    assert "credential.helper" in cmd
    assert "fetch --prune" in cmd
    assert "merge --ff-only" in cmd
    assert "git clean -f uv.lock" in cmd
    assert "AEO_GITHUB_TOKEN" not in cmd


def test_git_pull_uses_github_token(updater, monkeypatch):
    updater.github_token = "ghp_secret"
    captured: list[str] = []

    def fake_ssh_run(alias, remote, **kwargs):
        captured.append(remote)
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        return result

    monkeypatch.setattr(updater.ssh, "ssh_run", fake_ssh_run)
    updater._git_pull("aeo-ecs-0004", "/home/djn/worker/agent-eval-orchestrator")
    assert captured
    assert "password='ghp_secret'" in captured[0] or "password=ghp_secret" in captured[0]
    assert "username=JinnanDuan" in captured[0]


def test_git_pull_auth_error_hint_without_token(updater, monkeypatch):
    def fake_ssh_run(alias, remote, **kwargs):
        result = MagicMock()
        result.returncode = 1
        result.stdout = ""
        result.stderr = "fatal: could not read Username for 'https://github.com': terminal prompts disabled"
        return result

    monkeypatch.setattr(updater.ssh, "ssh_run", fake_ssh_run)
    with pytest.raises(RuntimeError, match="--github-token"):
        updater._git_pull("aeo-ecs-0004", "/home/djn/worker/agent-eval-orchestrator")


def _seed_updatable_worker(store):
    store.create_provisioning_worker(
        worker_id="ecs-worker-upd",
        display_name="ecs-worker-upd",
        slots_total=1,
        ssh_host_alias="aeo-ecs-0004",
        ssh_bootstrap_host_alias=None,
        connection_mode="direct",
        controller_internal_ip="192.168.0.211",
        tunnel_remote_port=None,
    )
    store.set_worker_provision_status("ecs-worker-upd", provision_status="ready")
    store.register_worker(
        worker_id="ecs-worker-upd",
        display_name="ecs-worker-upd",
        host="worker-host",
        slots_total=1,
        slots_used=0,
        capabilities={"sharedRoot": "/home/djn/worker/agent-eval-orchestrator/runtime"},
    )


def test_run_job_success(updater, store, monkeypatch):
    ssh_commands: list[str] = []

    def fake_ssh_run(alias, remote, **kwargs):
        ssh_commands.append(remote)
        result = MagicMock()
        result.returncode = 0
        result.stdout = "Already up to date.\n"
        result.stderr = ""
        return result

    monkeypatch.setattr(updater.ssh, "ssh_run", fake_ssh_run)
    monkeypatch.setattr(
        "agent_eval_orchestrator.controller.ssh_config.test_ssh_alias",
        lambda *args, **kwargs: (True, "connected"),
    )
    monkeypatch.setattr(updater.provisioner, "decommission_worker", lambda **kwargs: {"remoteCleanup": "done", "warnings": []})
    monkeypatch.setattr(updater.provisioner, "_wait_for_register", lambda *args, **kwargs: None)

    _seed_updatable_worker(store)
    worker = next(item for item in store.list_workers() if item["worker_id"] == "ecs-worker-upd")
    job_id = new_id("upd")
    store.create_worker_update_job(
        job_id=job_id,
        worker_id="ecs-worker-upd",
        targets=["aeo", "harbor"],
        steps=updater.initial_steps(["aeo", "harbor"]),
    )

    updater.run_job(
        job_id=job_id,
        worker_id="ecs-worker-upd",
        targets=["aeo", "harbor"],
        ssh_host_alias="aeo-ecs-0004",
        connection_mode="direct",
        controller_internal_ip="192.168.0.211",
        tunnel_remote_port=None,
        display_name="ecs-worker-upd",
        slots_total=1,
        worker=worker,
    )

    joined = "\n".join(ssh_commands)
    assert "git merge --ff-only" in joined
    assert "uv sync" in joined or ".local/bin/uv sync" in joined
    assert "agent_eval_orchestrator.worker.daemon" in joined
    job = store.get_worker_update_job(job_id)
    assert job["status"] == "succeeded"


def test_run_job_git_pull_failure(updater, store, monkeypatch):
    ssh_commands: list[str] = []

    def fake_ssh_run(alias, remote, **kwargs):
        ssh_commands.append(remote)
        result = MagicMock()
        if "git merge --ff-only" in remote:
            result.returncode = 1
            result.stdout = ""
            result.stderr = "CONFLICT"
        else:
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
        return result

    monkeypatch.setattr(updater.ssh, "ssh_run", fake_ssh_run)
    monkeypatch.setattr(
        "agent_eval_orchestrator.controller.ssh_config.test_ssh_alias",
        lambda *args, **kwargs: (True, "connected"),
    )
    monkeypatch.setattr(updater.provisioner, "decommission_worker", lambda **kwargs: {"remoteCleanup": "done", "warnings": []})

    _seed_updatable_worker(store)
    worker = next(item for item in store.list_workers() if item["worker_id"] == "ecs-worker-upd")
    job_id = new_id("upd")
    store.create_worker_update_job(
        job_id=job_id,
        worker_id="ecs-worker-upd",
        targets=["aeo"],
        steps=updater.initial_steps(["aeo"]),
    )

    updater.run_job(
        job_id=job_id,
        worker_id="ecs-worker-upd",
        targets=["aeo"],
        ssh_host_alias="aeo-ecs-0004",
        connection_mode="direct",
        controller_internal_ip="192.168.0.211",
        tunnel_remote_port=None,
        display_name="ecs-worker-upd",
        slots_total=1,
        worker=worker,
    )

    job = store.get_worker_update_job(job_id)
    assert job["status"] == "failed"
    assert "CONFLICT" in (job["error_text"] or "")
    assert not any("worker.daemon" in cmd for cmd in ssh_commands)
