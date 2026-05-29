from __future__ import annotations

import json

from agent_eval_orchestrator.worker import daemon


def test_post_json_honors_custom_timeout(monkeypatch) -> None:
    seen: dict[str, int] = {}

    def fake_urlopen(req, timeout=0):
        seen["timeout"] = timeout

        class _Resp:
            def read(self) -> bytes:
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *args) -> None:
                return None

        return _Resp()

    monkeypatch.setattr(daemon.request, "urlopen", fake_urlopen)
    daemon.post_json("http://127.0.0.1/test", {"ok": True}, timeout_sec=600)
    assert seen["timeout"] == 600


def test_archive_timeout_preserves_executor_success_status() -> None:
    code = 0
    status = "succeeded" if code == 0 else "failed"
    error_text = None if code == 0 else f"executor exited with code {code}"
    archive_exc = TimeoutError("timed out")
    archive_error = f"job archive upload failed: {archive_exc}"
    if status == "failed":
        error_text = f"{error_text}; {archive_error}" if error_text else archive_error
    else:
        error_text = archive_error

    assert status == "succeeded"
    assert error_text == "job archive upload failed: timed out"


def test_final_heartbeat_uses_extended_timeout(monkeypatch) -> None:
    calls: list[int] = []

    def fake_urlopen(req, timeout=0):
        calls.append(timeout)

        class _Resp:
            def read(self) -> bytes:
                return json.dumps({"batch": {}}).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args) -> None:
                return None

        return _Resp()

    monkeypatch.setattr(daemon.request, "urlopen", fake_urlopen)
    daemon.post_json(
        "http://127.0.0.1/api/workers/heartbeat",
        {"batchId": "batch-1", "finished": True},
        timeout_sec=daemon.DEFAULT_WORKER_FINAL_HEARTBEAT_TIMEOUT_SEC,
    )
    assert calls == [daemon.DEFAULT_WORKER_FINAL_HEARTBEAT_TIMEOUT_SEC]
