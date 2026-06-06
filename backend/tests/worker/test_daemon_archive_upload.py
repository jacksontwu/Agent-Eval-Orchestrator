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

    result = daemon.upload_archive("http://ctrl", batch_id="batch-1", job_dir=job_dir, token="secret")
    assert result["batchId"] == "batch-1"

    body = captured["body"]
    assert b'name="batchId"' in body
    assert b"batch-1" in body
    assert b'name="sha256"' in body
    assert b'name="archive"; filename="job.tar.gz"' in body
    # gzip magic bytes present (real tar bytes, not base64)
    assert b"\x1f\x8b" in body
    assert captured["headers"]["x-aeo-token"] == "secret"
    assert "multipart/form-data" in captured["headers"]["content-type"]
