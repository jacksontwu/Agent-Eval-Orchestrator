"""Rewritten worker daemon.

Poll lifecycle: register -> claim -> (pull assets -> run executor -> upload archive)
-> heartbeat. Asset transfer is HTTP-only (no SSH); results stream as multipart tar
(no base64).
"""
from __future__ import annotations

import hashlib
import json
import os
import tarfile
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib import request

from app.schema.assets import AssetManifest

AUTH_HEADER = "Authorization"


# --- low-level HTTP (patchable in tests) -----------------------------------

def _urlopen(req: request.Request, timeout: float = 60.0):  # pragma: no cover - thin wrapper
    return request.urlopen(req, timeout=timeout)


def _http_get(url: str, token: str | None = None) -> bytes:
    req = request.Request(url, method="GET")
    if token:
        req.add_header(AUTH_HEADER, f"Bearer {token}")
    with _urlopen(req) as resp:
        return resp.read()


def login(controller_url: str, username: str, password: str) -> str:
    body = json.dumps({"username": username, "password": password}).encode("utf-8")
    req = request.Request(
        f"{controller_url.rstrip('/')}/api/auth/login",
        data=body,
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    with _urlopen(req) as resp:
        raw = resp.read()
    payload = json.loads(raw)
    return str(payload["accessToken"])


def post_json(url: str, payload: dict[str, Any], token: str | None = None) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header(AUTH_HEADER, f"Bearer {token}")
    with _urlopen(req) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else {}


# --- asset pull -------------------------------------------------------------

def pull_assets(manifest: AssetManifest, *, base_url: str, target_root: Path,
                fetch: Callable[[str], bytes] | None = None, max_retries: int = 3,
                token: str | None = None) -> None:
    target_root = Path(target_root)
    fetcher = fetch or (lambda url: _http_get(url, token=token))
    for entry in manifest.entries:
        url = f"{base_url}/file?path={entry.path}"
        data = _fetch_with_retry(fetcher, url, expected_sha=entry.sha256, max_retries=max_retries)
        dest = target_root / entry.path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)


def _fetch_with_retry(fetcher: Callable[[str], bytes], url: str, *, expected_sha: str,
                      max_retries: int) -> bytes:
    last_exc: Exception | None = None
    for _ in range(max_retries):
        try:
            data = fetcher(url)
        except Exception as exc:  # noqa: BLE001 - retry transient transport errors
            last_exc = exc
            continue
        if hashlib.sha256(data).hexdigest() == expected_sha:
            return data
        last_exc = RuntimeError(f"checksum mismatch for {url}")
    raise last_exc or RuntimeError(f"failed to fetch {url}")


# --- archive upload ---------------------------------------------------------

def _tar_job_dir(job_dir: Path) -> tuple[bytes, str]:
    job_dir = Path(job_dir)
    buf = tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False)
    try:
        with tarfile.open(buf.name, mode="w:gz") as tar:
            for child in sorted(job_dir.rglob("*")):
                tar.add(child, arcname=str(child.relative_to(job_dir)))
        data = Path(buf.name).read_bytes()
    finally:
        os.unlink(buf.name)
    return data, hashlib.sha256(data).hexdigest()


def upload_archive(controller_url: str, *, batch_id: str, job_dir: Path,
                   token: str | None = None) -> dict[str, Any]:
    archive_bytes, sha = _tar_job_dir(job_dir)
    boundary = f"----aeo{uuid.uuid4().hex}"
    body = _build_multipart(
        boundary,
        fields={"batchId": batch_id, "sha256": sha},
        file_field="archive", file_name="job.tar.gz", file_bytes=archive_bytes,
    )
    url = f"{controller_url.rstrip('/')}/api/workers/job-archive"
    req = request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    if token:
        req.add_header(AUTH_HEADER, f"Bearer {token}")
    with _urlopen(req) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else {}


def _build_multipart(boundary: str, *, fields: dict[str, str], file_field: str,
                     file_name: str, file_bytes: bytes) -> bytes:
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        parts.append(f"{value}\r\n".encode())
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{file_name}"\r\n'.encode()
    )
    parts.append(b"Content-Type: application/gzip\r\n\r\n")
    parts.append(file_bytes)
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts)


# --- poll loop --------------------------------------------------------------

def run_forever(*, controller_url: str, worker_id: str, display_name: str, host: str,
                slots_total: int, shared_root: Path, token: str | None = None,
                bot_username: str | None = None, bot_password: str | None = None,
                executor_run: Callable[[dict[str, Any], Path], Path],
                poll_interval: float = 5.0, stop: Callable[[], bool] | None = None) -> None:  # pragma: no cover
    base = controller_url.rstrip("/")
    if not token and bot_username and bot_password:
        token = login(base, bot_username, bot_password)
    post_json(f"{base}/api/workers/register", {
        "workerId": worker_id, "displayName": display_name, "host": host,
        "slotsTotal": slots_total,
        "capabilities": {"sharedRoot": str(shared_root)},
    }, token=token)

    while not (stop and stop()):
        try:
            claim = post_json(f"{base}/api/workers/claim", {"workerId": worker_id}, token=token)
            batch_id = claim.get("batchId")
            if not batch_id:
                post_json(f"{base}/api/workers/heartbeat",
                          {"workerId": worker_id, "status": "online", "slotsUsed": 0}, token=token)
                time.sleep(poll_interval)
                continue
            manifest = AssetManifest.model_validate(claim["assetManifest"])
            target_root = Path(shared_root) / manifest.target_root_rel
            try:
                pull_assets(manifest, base_url=f"{base}{claim['assetUrl'].split('?')[0]}"
                            if claim.get("assetUrl", "").startswith("/") else claim["assetUrl"],
                            target_root=target_root, token=token)
            except Exception as exc:  # noqa: BLE001
                post_json(f"{base}/api/workers/heartbeat", {
                    "workerId": worker_id, "batchId": batch_id, "status": "sync_failed",
                    "errorText": str(exc), "finished": True,
                }, token=token)
                continue
            job_dir = executor_run(claim, target_root)
            upload_archive(controller_url, batch_id=batch_id, job_dir=job_dir, token=token)
            post_json(f"{base}/api/workers/heartbeat", {
                "workerId": worker_id, "batchId": batch_id, "status": "succeeded", "finished": True,
            }, token=token)
        except Exception:  # noqa: BLE001 - keep the daemon alive
            time.sleep(poll_interval)
