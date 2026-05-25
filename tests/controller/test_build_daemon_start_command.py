from agent_eval_orchestrator.controller.provisioner import (
    DEFAULT_AEO_DIR,
    DEFAULT_UV_BIN,
    DEFAULT_WORKER_LOG_DIR,
    build_daemon_start_command,
)


def test_build_daemon_start_command_uses_defaults():
    cmd = build_daemon_start_command(
        worker_id="w1",
        display_name="Worker One",
        slots=2,
        controller_url="http://192.168.0.211:7380",
        auth_token="secret",
    )
    assert f"cd {DEFAULT_AEO_DIR}" in cmd
    assert DEFAULT_UV_BIN in cmd
    assert DEFAULT_WORKER_LOG_DIR in cmd
    assert '--worker-id "w1"' in cmd
    assert f"--shared-root {DEFAULT_AEO_DIR}/runtime" in cmd


def test_build_daemon_start_command_dynamic_paths():
    cmd = build_daemon_start_command(
        worker_id="w2",
        display_name="Worker Two",
        slots=1,
        controller_url="http://127.0.0.1:17380",
        auth_token="tok",
        aeo_dir="/home/djn/worker/agent-eval-orchestrator",
        uv_bin="/home/djn/.local/bin/uv",
        log_dir="/home/djn/worker/logs",
    )
    assert "cd /home/djn/worker/agent-eval-orchestrator" in cmd
    assert "/home/djn/.local/bin/uv run python" in cmd
    assert "/home/djn/worker/logs/daemon-w2.log" in cmd
    assert "--shared-root /home/djn/worker/agent-eval-orchestrator/runtime" in cmd
    assert "DEFAULT_AEO_DIR" not in cmd
