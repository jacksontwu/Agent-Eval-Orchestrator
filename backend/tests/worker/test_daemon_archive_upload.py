import io

from app.worker import daemon


class _FakeResp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_upload_archive_sends_multipart(tmp_path, monkeypatch):
    job_dir = tmp_path / "job"
    (job_dir / "trial").mkdir(parents=True)
    (job_dir / "result.json").write_bytes(b'{"stats": {}}')
    (job_dir / "trial" / "result.json").write_bytes(b'{"trial_name": "x"}')

    captured = {}

    def fake_urlopen(req, timeout=60.0):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["body"] = req.data
        return _FakeResp(b'{"ok": true, "batchId": "batch-1"}')

    monkeypatch.setattr(daemon, "_urlopen", fake_urlopen)

    result = daemon.upload_archive("http://ctrl", batch_id="batch-1", job_dir=job_dir, token="access-token")
    assert result["batchId"] == "batch-1"

    body = captured["body"]
    assert b'name="batchId"' in body
    assert b"batch-1" in body
    assert b'name="sha256"' in body
    assert b'name="archive"; filename="job.tar.gz"' in body
    # gzip magic bytes present (real tar bytes, not base64)
    assert b"\x1f\x8b" in body
    assert captured["headers"]["authorization"] == "Bearer access-token"
    assert "multipart/form-data" in captured["headers"]["content-type"]


def test_login_posts_credentials_and_returns_access_token(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=60.0):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["body"] = req.data
        return _FakeResp(b'{"accessToken": "access-token"}')

    monkeypatch.setattr(daemon, "_urlopen", fake_urlopen)

    token = daemon.login("http://ctrl", "worker-bot", "bot-secret")

    assert token == "access-token"
    assert captured["url"] == "http://ctrl/api/auth/login"
    assert captured["headers"]["content-type"] == "application/json"
    assert b'"username": "worker-bot"' in captured["body"]
    assert b'"password": "bot-secret"' in captured["body"]


def test_post_json_sends_bearer_header(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=60.0):
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _FakeResp(b'{"ok": true}')

    monkeypatch.setattr(daemon, "_urlopen", fake_urlopen)

    daemon.post_json("http://ctrl/api/workers/heartbeat", {"workerId": "w1"}, token="access-token")

    assert captured["headers"]["authorization"] == "Bearer access-token"
