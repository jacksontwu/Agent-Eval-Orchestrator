from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from agent_eval_orchestrator.worker import daemon


class StopDaemonLoop(Exception):
    pass


def test_prepare_failure_reports_failed_heartbeat(tmp_path: Path) -> None:
    shared_root = tmp_path / "runtime"
    shared_root.mkdir()
    local_root = shared_root / "workers" / "worker-a" / "local"
    local_root.mkdir(parents=True)

    heartbeat_calls: list[dict] = []
    claim_calls = {"count": 0}

    def fake_post_json(url: str, payload: dict, auth_token: str | None = None) -> dict | None:
        if url.endswith("/api/workers/register"):
            return None
        if url.endswith("/api/workers/claim"):
            claim_calls["count"] += 1
            if claim_calls["count"] > 1:
                return {"task": None}
            return {
                "task": {
                    "batch": {
                        "batch_id": "batch-1",
                        "owner": "demo",
                        "run_id": "run-1",
                        "batch_root": str(
                            shared_root / "archives" / "demo" / "runs" / "run-1" / "batches" / "batch-1"
                        ),
                        "assigned_worker_id": "worker-a",
                    },
                    "run": {"run_id": "run-1"},
                    "template": {"template_id": "tpl-1"},
                    "datasetRef": "/missing/dataset",
                    "executorConfig": {},
                }
            }
        if url.endswith("/api/workers/heartbeat"):
            heartbeat_calls.append(payload)
            return None
        raise AssertionError(f"unexpected url: {url}")

    argv = [
        "--controller-url",
        "http://127.0.0.1:7380",
        "--worker-id",
        "worker-a",
        "--shared-root",
        str(shared_root),
        "--local-root",
        str(local_root),
        "--slots",
        "1",
        "--poll-interval",
        "1",
    ]

    with patch.object(daemon, "post_json", side_effect=fake_post_json), patch.object(
        daemon.HarborExecutor,
        "prepare",
        side_effect=RuntimeError("dataset path not found: /missing/dataset"),
    ), patch.object(daemon.time, "sleep", side_effect=StopDaemonLoop()):
        try:
            daemon.main(argv)
        except StopDaemonLoop:
            pass

    assert len(heartbeat_calls) == 1
    assert heartbeat_calls[0]["status"] == "failed"
    assert heartbeat_calls[0]["currentStep"] == "executor-starting"
    assert heartbeat_calls[0]["finished"] is True
    assert "dataset path not found" in heartbeat_calls[0]["errorText"]
