import json

from agent_eval_orchestrator.controller.provisioner import TunnelManager


def test_tunnel_manager_persist_roundtrip(temp_layout):
    path = temp_layout.controller_dir / "tunnels.json"
    manager = TunnelManager(path)
    manager.save_record(
        "ecs-worker-0004",
        {
            "djnHostAlias": "aeo-ecs-0004",
            "remotePort": 17380,
            "localPort": 8790,
            "sshPid": 12345,
            "startedAt": "2026-05-24T12:00:00+00:00",
        },
    )
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["ecs-worker-0004"]["sshPid"] == 12345
    record = manager.get_record("ecs-worker-0004")
    assert record["remotePort"] == 17380
