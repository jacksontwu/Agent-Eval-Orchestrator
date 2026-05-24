from agent_eval_orchestrator.controller.provisioner import (
    build_bootstrap_command,
    build_daemon_start_command,
    redact_sensitive_log,
)


def test_build_daemon_start_command():
    cmd = build_daemon_start_command(
        worker_id="ecs-worker-0004",
        display_name="ecs-worker-0004",
        slots=2,
        tunnel_remote_port=17380,
        auth_token="secret-token-value",
    )
    assert '--worker-id "ecs-worker-0004"' in cmd
    assert '--controller-url "http://127.0.0.1:17380"' in cmd
    assert "secret-token-value" in cmd


def test_build_bootstrap_command():
    cmd = build_bootstrap_command(djn_password="pw123")
    assert "DJN_PASSWORD='pw123'" in cmd
    assert "/tmp/aeo-bootstrap.sh --yes" in cmd


def test_redact_sensitive_log():
    raw = (
        "DJN_PASSWORD='pw123' bash /tmp/aeo-bootstrap.sh\n"
        "AEO_TOKEN=abc123 setsid uv run\n"
        "--auth-token abc123\n"
    )
    redacted = redact_sensitive_log(raw)
    assert "pw123" not in redacted
    assert "abc123" not in redacted
    assert "***REDACTED***" in redacted
