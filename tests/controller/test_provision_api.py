import json
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread

from agent_eval_orchestrator.controller.server import Handler, ThreadedServer
from agent_eval_orchestrator.controller.provisioner import Provisioner
from agent_eval_orchestrator.storage.store import Store


def start_test_server(store: Store, ssh_config: Path, port: int) -> ThreadedServer:
    bootstrap = ssh_config.parent / "bootstrap.sh"
    bootstrap.write_text("#!/bin/bash\n", encoding="utf-8")
    provisioner = Provisioner(
        store=store,
        ssh_config_path=ssh_config,
        auth_token="secret",
        controller_port=port,
        bootstrap_script_path=bootstrap,
        tunnel_state_path=store.layout.controller_dir / "tunnels.json",
    )
    server = ThreadedServer(("127.0.0.1", port), Handler)
    Handler.store = store
    Handler.auth_token = "secret"
    Handler.viewer_manager = None
    Handler.provisioner = provisioner
    Handler.ssh_config_path = ssh_config
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_provision_duplicate_worker_returns_409(store, sample_ssh_config):
    store.create_provisioning_worker(
        worker_id="ecs-worker-dup",
        display_name="dup",
        slots_total=1,
        ssh_host_alias="aeo-ecs-0004",
        ssh_bootstrap_host_alias=None,
        tunnel_remote_port=17380,
    )
    server = start_test_server(store, sample_ssh_config, 9877)
    conn = HTTPConnection("127.0.0.1", 9877)
    body = json.dumps(
        {
            "workerId": "ecs-worker-dup",
            "displayName": "dup",
            "slotsTotal": 1,
            "mode": "join",
            "sshHostAlias": "aeo-ecs-0004",
            "tunnelRemotePort": 17380,
        }
    )
    conn.request(
        "POST",
        "/api/workers/provision",
        body=body,
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    assert resp.status == 409
    server.shutdown()
