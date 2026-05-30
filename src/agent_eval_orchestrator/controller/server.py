from __future__ import annotations

import argparse
import base64
from http.cookies import SimpleCookie
import io
import ipaddress
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time
from urllib import error, request
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

from agent_eval_orchestrator.core.defaults import (
    DEFAULT_HARBOR_REPO,
    DEFAULT_HOST,
    DEFAULT_PER_WORKER_CONCURRENCY,
    DEFAULT_PORT,
    DEFAULT_PRESET_DATASETS,
)
from agent_eval_orchestrator.core.ids import new_id, now_iso, sanitize_name
from agent_eval_orchestrator.controller.asset_syncer import (
    AssetSyncer,
    build_sync_manifest,
    initial_worker_steps,
    validate_create_task_assets,
)
from agent_eval_orchestrator.controller.executor_config import build_asset_sync_executor_config
from agent_eval_orchestrator.controller.harbor_viewer import HarborViewerManager
from agent_eval_orchestrator.normalizers.harbor import normalize_harbor_job
from agent_eval_orchestrator.normalizers.harbor_job_merge import copy_trial_dirs, write_merged_job
from agent_eval_orchestrator.normalizers.harbor_timestamps import normalize_jobs_dir
from agent_eval_orchestrator.controller.provisioner import Provisioner
from agent_eval_orchestrator.controller.run_rerun_coordinator import RunRerunCoordinator, RerunValidationError
from agent_eval_orchestrator.controller.worker_updater import WorkerUpdater
from agent_eval_orchestrator.controller.ssh_config import list_ssh_hosts, test_ssh_alias
from agent_eval_orchestrator.controller.static import INDEX_HTML
from agent_eval_orchestrator.storage.layout import default_layout
from agent_eval_orchestrator.storage.store import Store


GLOBAL_VIEWER_PORT = 7369
DEFAULT_OWNER = "demo"
DEFAULT_JOBS_DIR = DEFAULT_HARBOR_REPO / "jobs"
DEFAULT_IMPORTED_JOBS_DIRNAME = "imported-jobs"


def resolve_global_harbor_viewer_paths(jobs_dir: str | None = None) -> tuple[Path, Path]:
    raw = str(jobs_dir or DEFAULT_JOBS_DIR).strip() or str(DEFAULT_JOBS_DIR)
    jobs_path = Path(raw).expanduser().resolve()
    return jobs_path.parent, jobs_path


def _is_subpath(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _safe_extract_tar(archive: bytes, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for member in tar.getmembers():
            target = (target_dir / member.name).resolve()
            if not _is_subpath(target, target_dir):
                raise RuntimeError(f"unsafe tar member: {member.name}")
        tar.extractall(target_dir)


def _rebuild_merged_job_for_run(
    *,
    store: Store,
    run_id: str,
    jobs_dir: Path,
) -> None:
    grouped_sources = _job_sources_for_run(
        store=store,
        run_id=run_id,
        jobs_dir=jobs_dir,
    )
    for merged_job_name, source_job_dirs in grouped_sources:
        merged_job_dir = jobs_dir / merged_job_name
        if merged_job_dir.exists():
            shutil.rmtree(merged_job_dir)
        try:
            write_merged_job(
                merged_job_dir=merged_job_dir,
                merged_job_name=merged_job_name,
                source_job_dirs=source_job_dirs,
            )
        except RuntimeError:
            if merged_job_dir.exists():
                shutil.rmtree(merged_job_dir)
            continue
        for batch in store.list_batches_for_run(run_id):
            legacy_batch_dir = jobs_dir / str(batch["batch_id"])
            if legacy_batch_dir.exists() and legacy_batch_dir != merged_job_dir:
                shutil.rmtree(legacy_batch_dir)


def _apply_exception_rerun_merge(
    *,
    store: Store,
    batch_id: str,
    rerun_cases: list[dict[str, object]] | None = None,
    jobs_dir: Path | None = None,
) -> bool:
    batch_row = store.get_batch(batch_id)
    if not batch_row or str(batch_row.get("batch_kind") or "") != "exception_rerun":
        return False
    parent_batch_id = str(batch_row.get("parent_batch_id") or "").strip()
    if not parent_batch_id:
        return False
    imported_root = store.layout.controller_dir / DEFAULT_IMPORTED_JOBS_DIRNAME
    cases = rerun_cases
    if cases is None:
        imported_job_dir = imported_root / batch_id
        if not imported_job_dir.exists():
            return False
        _, cases, _ = normalize_harbor_job(imported_job_dir, batch_id)
    if not cases:
        return False
    store.merge_rerun_cases_into_parent(
        parent_batch_id=parent_batch_id,
        rerun_cases=cases,
        rerun_batch_id=batch_id,
    )
    parent_imported = imported_root / parent_batch_id
    rerun_imported = imported_root / batch_id
    if rerun_imported.exists():
        copy_trial_dirs(rerun_imported, parent_imported)
    parent_batch = store.get_batch(parent_batch_id)
    if parent_batch:
        parent_job_dir = Path(str(parent_batch["batch_root"])) / "harbor" / "jobs" / parent_batch_id
        rerun_job_dir = Path(str(batch_row["batch_root"])) / "harbor" / "jobs" / batch_id
        if rerun_job_dir.exists():
            parent_job_dir.mkdir(parents=True, exist_ok=True)
            copy_trial_dirs(rerun_job_dir, parent_job_dir)
    store.finish_rerun_batch_if_complete(rerun_batch_id=batch_id)
    run_row = store.get_run(str(batch_row["run_id"]))
    if run_row and str(run_row.get("rerun_status") or "") in {"succeeded", "failed"}:
        metadata = batch_row.get("executor_metadata") or {}
        resolved_jobs_dir = Path(
            str(metadata.get("combinedJobsDir") or DEFAULT_JOBS_DIR)
        ).expanduser().resolve()
        if jobs_dir is not None:
            resolved_jobs_dir = jobs_dir
        try:
            _rebuild_merged_job_for_run(
                store=store,
                run_id=str(batch_row["run_id"]),
                jobs_dir=resolved_jobs_dir,
            )
        except RuntimeError:
            pass
    return True


def _job_sources_for_run(
    *,
    store: Store,
    run_id: str,
    jobs_dir: Path,
) -> list[tuple[str, list[Path]]]:
    imported_root = store.layout.controller_dir / DEFAULT_IMPORTED_JOBS_DIRNAME
    grouped_sources: dict[str, list[Path]] = {}
    run = store.get_run(run_id)
    if not run:
        return []
    merged_job_name = sanitize_name(str(run["display_name"]))
    sources: list[Path] = []
    for batch in store.list_primary_batches_for_run(run_id):
        artifact_index = batch.get("artifact_index") or {}
        candidates: list[Path] = []
        raw_job_dir = str(artifact_index.get("jobDir") or "").strip()
        if raw_job_dir:
            candidates.append(Path(raw_job_dir).expanduser())
        candidates.extend(
            [
                imported_root / str(batch["batch_id"]),
                jobs_dir / str(batch["batch_id"]),
            ]
        )
        source = next(
            (path.resolve() for path in candidates if str(path).strip() and path.exists()),
            None,
        )
        if source is None:
            continue
        sources.append(source)
    if sources:
        grouped_sources[merged_job_name] = sources
    return list(grouped_sources.items())


def _validate_controller_internal_ip(value: str) -> bool:
    import ipaddress

    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return bool(value and " " not in value and len(value) <= 253)


def _json_response(handler: BaseHTTPRequestHandler, payload: object, code: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _html_response(handler: BaseHTTPRequestHandler, body: str, code: int = 200) -> None:
    payload = body.encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def _html_response_with_headers(
    handler: BaseHTTPRequestHandler,
    body: str,
    *,
    code: int = 200,
    headers: dict[str, str] | None = None,
) -> None:
    payload = body.encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    for key, value in (headers or {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(payload)


def _render_index_html(default_view: str = "tasks") -> str:
    html = INDEX_HTML
    if default_view == "create":
        html = html.replace('<section id="tasksView">', '<section id="tasksView" class="hidden">', 1)
        html = html.replace('<section id="createView" class="hidden">', '<section id="createView">', 1)
    return html


class Handler(BaseHTTPRequestHandler):
    store: Store
    auth_token: str | None = None
    viewer_manager: HarborViewerManager | None = None
    global_viewer_process: subprocess.Popen | None = None
    provisioner: Provisioner | None = None
    worker_updater: WorkerUpdater | None = None
    asset_syncer: AssetSyncer | None = None
    run_rerun_coordinator: RunRerunCoordinator | None = None
    ssh_config_path: Path | None = None
    controller_shared_root: Path | None = None

    def log_message(self, fmt: str, *args) -> None:
        print(f"[controller] {self.address_string()} {fmt % args}", flush=True)

    def _read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def _rebuild_merged_jobs(self, jobs_dir: Path) -> list[str]:
        jobs_dir.mkdir(parents=True, exist_ok=True)
        merged_names: list[str] = []
        for run in self.store.list_runs():
            grouped_sources = _job_sources_for_run(
                store=self.store,
                run_id=str(run["run_id"]),
                jobs_dir=jobs_dir,
            )
            if not grouped_sources:
                continue
            try:
                _rebuild_merged_job_for_run(
                    store=self.store,
                    run_id=str(run["run_id"]),
                    jobs_dir=jobs_dir,
                )
            except RuntimeError:
                continue
            merged_names.extend(name for name, _ in grouped_sources)
        return merged_names

    def _viewer_public_url(self) -> str:
        host = self.headers.get("Host", "").split(":")[0] or "127.0.0.1"
        return f"http://{host}:{GLOBAL_VIEWER_PORT}/"

    def _ensure_global_harbor_viewer(self, jobs_dir: str | None = None) -> dict[str, object]:
        harbor_repo, jobs_path = resolve_global_harbor_viewer_paths(jobs_dir)
        try:
            with request.urlopen(f"http://127.0.0.1:{GLOBAL_VIEWER_PORT}/api/health", timeout=1):
                return {
                    "available": True,
                    "url": self._viewer_public_url(),
                    "jobsDir": str(jobs_path),
                    "harborRepo": str(harbor_repo),
                    "port": GLOBAL_VIEWER_PORT,
                }
        except Exception:
            pass

        try:
            jobs_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return {
                "available": False,
                "reason": f"无法访问 Jobs Dir: {jobs_path} ({exc})",
                "jobsDir": str(jobs_path),
                "harborRepo": str(harbor_repo),
            }

        self._rebuild_merged_jobs(jobs_path)
        normalize_jobs_dir(jobs_path)

        if self.global_viewer_process and self.global_viewer_process.poll() is None:
            self.global_viewer_process.terminate()
        log_path = self.store.layout.controller_dir / "logs" / f"harbor-viewer-{GLOBAL_VIEWER_PORT}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("a", encoding="utf-8")
        command = [
            "/bin/bash",
            "-lc",
            (
                f"cd {harbor_repo} && "
                f"uv run harbor view {jobs_path} --port {GLOBAL_VIEWER_PORT} --host 0.0.0.0 --no-build"
            ),
        ]
        self.__class__.global_viewer_process = subprocess.Popen(
            command,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                with request.urlopen(f"http://127.0.0.1:{GLOBAL_VIEWER_PORT}/api/health", timeout=1):
                    return {
                        "available": True,
                        "url": self._viewer_public_url(),
                        "jobsDir": str(jobs_path),
                        "harborRepo": str(harbor_repo),
                        "port": GLOBAL_VIEWER_PORT,
                    }
            except Exception:
                time.sleep(0.5)
        return {
            "available": False,
            "reason": "Harbor viewer did not become ready",
            "jobsDir": str(jobs_path),
            "harborRepo": str(harbor_repo),
        }

    def _is_loopback_client(self) -> bool:
        host = self.client_address[0]
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return host in {"localhost"}

    def _is_authorized(self) -> bool:
        if self._is_loopback_client():
            return True
        expected = self.auth_token
        if not expected:
            return False
        supplied = self.headers.get("X-AEO-Token", "")
        if supplied == expected:
            return True
        cookie_header = self.headers.get("Cookie", "")
        if cookie_header:
            cookie = SimpleCookie()
            cookie.load(cookie_header)
            if cookie.get("aeo_token") and cookie["aeo_token"].value == expected:
                return True
        return False

    def _proxy_viewer_request(self, viewer_id: str, remainder: str, query: str) -> None:
        manager = self.viewer_manager
        if manager is None:
            _json_response(self, {"error": "viewer manager unavailable"}, 500)
            return
        session = manager.sessions.get(viewer_id)
        if session is None or session.process.poll() is not None:
            _json_response(self, {"error": "viewer session not found"}, 404)
            return
        target_path = "/" + remainder if remainder else "/"
        if query:
            target_path += f"?{query}"
        upstream = f"http://127.0.0.1:{session.port}{target_path}"
        try:
            with request.urlopen(upstream, timeout=30) as resp:
                content = resp.read()
                content_type = resp.headers.get("Content-Type", "application/octet-stream")
        except error.HTTPError as exc:
            body = exc.read()
            self.send_response(exc.code)
            self.send_header("Content-Type", exc.headers.get("Content-Type", "text/plain; charset=utf-8"))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        except Exception as exc:
            _json_response(self, {"error": str(exc)}, 502)
            return

        prefix = f"/harbor-viewer/{viewer_id}"
        if content_type.startswith("text/html") or "javascript" in content_type or content_type.startswith("text/css"):
            text = content.decode("utf-8", errors="replace")
            text = text.replace('"basename":"/"', f'"basename":"{prefix}"')
            text = text.replace('"/assets/', f'"{prefix}/assets/')
            text = text.replace("'/assets/", f"'{prefix}/assets/")
            text = text.replace('"/api/', f'"{prefix}/api/')
            text = text.replace("'/api/", f"'{prefix}/api/")
            text = text.replace('"/favicon.ico"', f'"{prefix}/favicon.ico"')
            content = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        if path.startswith("/harbor-viewer/"):
            if not self._is_authorized():
                _json_response(self, {"error": "forbidden"}, 403)
                return
            parts = path.split("/", 3)
            viewer_id = parts[2] if len(parts) > 2 else ""
            remainder = parts[3] if len(parts) > 3 else ""
            self._proxy_viewer_request(viewer_id, remainder, parsed.query)
            return
        if path in {"/", "/create"}:
            query_token = str(qs.get("token", [""])[0]).strip()
            expected = self.auth_token
            if not self._is_authorized():
                if not (expected and query_token == expected):
                    _json_response(self, {"error": "forbidden"}, 403)
                    return
                _html_response_with_headers(
                    self,
                    _render_index_html("create" if path == "/create" else "tasks"),
                    headers={"Set-Cookie": f"aeo_token={expected}; Path=/; SameSite=Lax"},
                )
                return
            _html_response(self, _render_index_html("create" if path == "/create" else "tasks"))
            return
        if not self._is_authorized():
            _json_response(self, {"error": "forbidden"}, 403)
            return
        if path == "/api/health":
            _json_response(self, {"ok": True, "time": now_iso()})
            return
        if path == "/api/overview":
            _json_response(
                self,
                {
                    "time": now_iso(),
                    "workers": self.store.list_workers(),
                    "templates": self.store.list_task_templates(),
                    "runs": self.store.list_runs(),
                    "batches": self.store.list_batches(),
                },
            )
            return
        if path == "/api/dashboard/tasks":
            _json_response(self, {"time": now_iso(), "items": self.store.list_eval_task_summaries()})
            return
        if path == "/api/dashboard/batches":
            _json_response(self, {"time": now_iso(), "items": self.store.list_batch_summaries()})
            return
        if path == "/api/task-templates":
            _json_response(self, self.store.list_task_templates())
            return
        if path.startswith("/api/eval-tasks/"):
            run_id = path.split("/")[3]
            detail = self.store.get_eval_task_detail(run_id)
            if not detail:
                _json_response(self, {"error": "eval task not found"}, 404)
                return
            _json_response(self, detail)
            return
        if path == "/api/workers":
            _json_response(self, self.store.list_workers())
            return
        if path == "/api/ssh/hosts":
            config_path = (self.ssh_config_path or Path("~/.ssh/config")).expanduser()
            _json_response(
                self,
                {
                    "sshConfigPath": str(config_path),
                    "items": list_ssh_hosts(config_path),
                },
            )
            return
        if path.startswith("/api/workers/update/"):
            job_id = path.split("/")[4]
            job = self.store.get_worker_update_job(job_id)
            if not job:
                _json_response(self, {"error": "job not found"}, 404)
                return
            _json_response(
                self,
                {
                    "jobId": job["job_id"],
                    "workerId": job["worker_id"],
                    "status": job["status"],
                    "targets": job["targets"],
                    "currentStep": job["current_step"],
                    "steps": job["steps"],
                    "logTail": job["log_tail"],
                    "errorText": job["error_text"],
                    "createdAt": job["created_at"],
                    "finishedAt": job["finished_at"],
                },
            )
            return
        if path.startswith("/api/workers/provision/"):
            job_id = path.split("/")[4]
            job = self.store.get_provision_job(job_id)
            if not job:
                _json_response(self, {"error": "job not found"}, 404)
                return
            _json_response(
                self,
                {
                    "jobId": job["job_id"],
                    "workerId": job["worker_id"],
                    "mode": job["mode"],
                    "status": job["status"],
                    "currentStep": job["current_step"],
                    "steps": job["steps"],
                    "logTail": job["log_tail"],
                    "errorText": job["error_text"],
                    "createdAt": job["created_at"],
                    "finishedAt": job["finished_at"],
                },
            )
            return
        if path == "/api/workers/runtime":
            _json_response(self, self.store.list_worker_runtime_status())
            return
        if path == "/api/datasets":
            _json_response(
                self,
                {
                    "items": [
                        {"label": label, "path": str(path)}
                        for label, path in DEFAULT_PRESET_DATASETS.items()
                    ]
                },
            )
            return
        if path == "/api/harbor-viewer/global":
            jobs_dir = str(qs.get("jobsDir", [""])[0]).strip() or None
            _json_response(self, self._ensure_global_harbor_viewer(jobs_dir))
            return
        if path == "/api/files/read":
            raw_path = str(qs.get("path", [""])[0]).strip()
            if not raw_path:
                _json_response(self, {"error": "path is required"}, 400)
                return
            target = Path(raw_path).expanduser().resolve()
            archives_root = self.store.layout.archives_dir.resolve()
            try:
                target.relative_to(archives_root)
            except ValueError:
                _json_response(self, {"error": "path is outside readable root"}, 403)
                return
            if not target.exists() or not target.is_file():
                _json_response(self, {"error": "file not found"}, 404)
                return
            text = target.read_text(encoding="utf-8", errors="replace")
            _json_response(self, {"path": str(target), "content": text[-200000:]})
            return
        if path.startswith("/api/runs/") and path.endswith("/sync"):
            run_id = path.split("/")[3]
            run = self.store.get_run(run_id)
            if not run:
                _json_response(self, {"error": "run not found"}, 404)
                return
            job = self.store.get_asset_sync_job_for_run(run_id)
            if not job:
                _json_response(self, {"error": "sync job not found"}, 404)
                return
            _json_response(
                self,
                {
                    "runId": run_id,
                    "syncStatus": run.get("sync_status") or "",
                    "jobId": job["job_id"],
                    "status": job["status"],
                    "currentStep": job["current_step"],
                    "steps": job["steps"],
                    "logTail": job["log_tail"],
                    "errorText": job["error_text"],
                    "createdAt": job["created_at"],
                    "finishedAt": job["finished_at"],
                },
            )
            return
        if path.startswith("/api/sync-jobs/"):
            job_id = path.split("/")[3]
            job = self.store.get_asset_sync_job(job_id)
            if not job:
                _json_response(self, {"error": "job not found"}, 404)
                return
            _json_response(
                self,
                {
                    "jobId": job["job_id"],
                    "runId": job["run_id"],
                    "status": job["status"],
                    "currentStep": job["current_step"],
                    "steps": job["steps"],
                    "logTail": job["log_tail"],
                    "errorText": job["error_text"],
                    "createdAt": job["created_at"],
                    "finishedAt": job["finished_at"],
                },
            )
            return
        if path.startswith("/api/runs/") and path.endswith("/rerun"):
            run_id = path.split("/")[3]
            run = self.store.get_run(run_id)
            if not run:
                _json_response(self, {"error": "run not found"}, 404)
                return
            job = None
            if run.get("rerun_job_id"):
                job = self.store.get_run_rerun_job(str(run["rerun_job_id"]))
            rerun_batches = []
            if job:
                for worker_id, batch_id in self.store.iter_run_rerun_batch_ids(job):
                    batch = self.store.get_batch(str(batch_id))
                    if batch:
                        rerun_batches.append(
                            {
                                "workerId": worker_id,
                                "batchId": batch_id,
                                "status": batch["status"],
                                "parentBatchId": batch.get("parent_batch_id"),
                            }
                        )
            remaining = len(self.store.list_exception_cases_for_run(run_id))
            error_text = str((job or {}).get("error_text") or "") or None
            _json_response(
                self,
                {
                    "runId": run_id,
                    "rerunStatus": run.get("rerun_status") or "idle",
                    "rerunJobId": run.get("rerun_job_id"),
                    "errorText": error_text,
                    "job": job,
                    "rerunBatches": rerun_batches,
                    "remainingExceptionCount": remaining,
                },
            )
            return
        if path.startswith("/api/runs/"):
            run_id = path.split("/")[3]
            run = self.store.get_run(run_id)
            if not run:
                _json_response(self, {"error": "run not found"}, 404)
                return
            template = self.store.get_task_template(run["template_id"])
            batches = self.store.list_batches_for_run(run_id)
            _json_response(self, {"run": run, "template": template, "batches": batches})
            return
        if path.startswith("/api/batches/"):
            batch_id = path.split("/")[3]
            detail = self.store.get_batch_detail(batch_id)
            if not detail:
                _json_response(self, {"error": "batch not found"}, 404)
                return
            _json_response(self, detail)
            return
        _json_response(self, {"error": "not found"}, 404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not self._is_authorized():
            _json_response(self, {"error": "forbidden"}, 403)
            return
        try:
            body = self._read_json()
        except (UnicodeDecodeError, json.JSONDecodeError):
            _json_response(self, {"error": "request body must be valid JSON"}, 400)
            return
        if path == "/api/task-templates":
            try:
                item = self.store.create_task_template(
                    owner=str(body.get("owner") or "default"),
                    name=str(body["name"]),
                    dataset_ref=str(body["datasetRef"]),
                    executor_kind=str(body["executorKind"]),
                    executor_config=dict(body.get("executorConfig") or {}),
                    model_profile_ref=str(body.get("modelProfileRef") or "") or None,
                    note=str(body.get("note") or ""),
                )
            except KeyError as exc:
                _json_response(self, {"error": f"missing field: {exc}"}, 400)
                return
            _json_response(self, item, 201)
            return
        if path == "/api/eval-tasks/create-and-distribute":
            try:
                owner = DEFAULT_OWNER
                dataset_path = Path(str(body["datasetPath"])).expanduser()
                bitfun_cli_path = Path(str(body["bitfunCliPath"])).expanduser()
                bitfun_config_dir = Path(str(body["bitfunConfigDir"])).expanduser()
                worker_ids = [
                    str(item).strip()
                    for item in body.get("workerIds") or []
                    if str(item).strip()
                ]
                case_ids = [
                    str(item).strip()
                    for item in body.get("selectedCaseIds") or []
                    if str(item).strip()
                ]
                if not case_ids:
                    case_ids = self.store.list_dataset_case_ids(str(dataset_path))
                workers = self.store.list_workers()
                controller_root = (self.controller_shared_root or self.store.layout.root).expanduser()
                validate_create_task_assets(
                    dataset_path=dataset_path,
                    bitfun_cli_path=bitfun_cli_path,
                    bitfun_config_dir=bitfun_config_dir,
                    case_ids=case_ids,
                    workers=workers,
                    worker_ids=worker_ids,
                    controller_shared_root=controller_root,
                )
                jobs_dir = str(body.get("jobsDir") or DEFAULT_JOBS_DIR).strip() or str(DEFAULT_JOBS_DIR)
                body_config = dict(body.get("executorConfig") or {})
                executor_config = build_asset_sync_executor_config(
                    worker_ids=worker_ids,
                    workers=workers,
                    body_config=body_config,
                    jobs_dir=jobs_dir,
                )
                task_name = str(body.get("name") or "").strip() or f"{dataset_path.name}-{now_iso()[:19]}"
                template = self.store.create_task_template(
                    owner=owner,
                    name=task_name,
                    dataset_ref=str(dataset_path),
                    executor_kind="harbor-docker",
                    executor_config=executor_config,
                    model_profile_ref=str(body.get("modelProfileRef") or "") or None,
                    note="",
                )
                run = self.store.create_run(template_id=str(template["template_id"]), display_name=task_name)
                batches = self.store.create_sharded_batches(
                    run_id=str(run["run_id"]),
                    selected_case_ids=case_ids,
                    worker_ids=worker_ids,
                    batch_options={
                        "concurrency": int(
                            body_config.get("nConcurrent") or DEFAULT_PER_WORKER_CONCURRENCY
                        )
                    },
                    initial_status="pending_sync",
                )
                workers_by_id = {str(item["worker_id"]): item for item in workers}
                worker_shards = {
                    str(batch["preferred_worker_id"]): list(batch["selected_case_ids"])
                    for batch in batches
                }
                manifest = build_sync_manifest(
                    run_id=str(run["run_id"]),
                    dataset_path=dataset_path.resolve(),
                    bitfun_cli_path=bitfun_cli_path.resolve(),
                    bitfun_config_dir=bitfun_config_dir.resolve(),
                    worker_shards=worker_shards,
                    workers_by_id=workers_by_id,
                    controller_shared_root=controller_root,
                )
                sync_job_id = new_id("sync")
                self.store.update_run_sync_fields(
                    run_id=str(run["run_id"]),
                    sync_status="pending",
                    sync_job_id=sync_job_id,
                    sync_manifest=manifest,
                )
                self.store.create_asset_sync_job(
                    job_id=sync_job_id,
                    run_id=str(run["run_id"]),
                    steps=initial_worker_steps(worker_ids),
                )
                if self.asset_syncer is not None:
                    self.asset_syncer.start_job_async(
                        job_id=sync_job_id,
                        run_id=str(run["run_id"]),
                        template_id=str(template["template_id"]),
                    )
                run = self.store.get_run(str(run["run_id"])) or run
            except KeyError as exc:
                _json_response(self, {"error": f"missing field: {exc}"}, 400)
                return
            except Exception as exc:
                _json_response(self, {"error": str(exc)}, 400)
                return
            _json_response(
                self,
                {
                    "template": template,
                    "run": {
                        **run,
                        "syncStatus": run.get("sync_status") or "pending",
                    },
                    "batches": batches,
                    "syncJobId": sync_job_id,
                },
                201,
            )
            return
        if path == "/api/workers/job-archive":
            try:
                batch_id = str(body["batchId"])
                jobs_dir = Path(str(body.get("jobsDir") or DEFAULT_JOBS_DIR)).expanduser().resolve()
                archive = base64.b64decode(str(body["archiveBase64"]))
                imported_root = self.store.layout.controller_dir / DEFAULT_IMPORTED_JOBS_DIRNAME
                batch_dir = imported_root / batch_id
                if batch_dir.exists():
                    shutil.rmtree(batch_dir)
                imported_root.mkdir(parents=True, exist_ok=True)
                _safe_extract_tar(archive, imported_root)
                batch = self.store.get_batch(batch_id)
                if batch:
                    if str(batch.get("batch_kind") or "") == "exception_rerun":
                        _apply_exception_rerun_merge(
                            store=self.store,
                            batch_id=batch_id,
                            jobs_dir=jobs_dir,
                        )
                    try:
                        _rebuild_merged_job_for_run(
                            store=self.store,
                            run_id=str(batch["run_id"]),
                            jobs_dir=jobs_dir,
                        )
                    except RuntimeError as exc:
                        _json_response(self, {"error": str(exc)}, 500)
                        return
                _json_response(self, {"ok": True, "batchId": batch_id, "jobsDir": str(jobs_dir)})
            except KeyError as exc:
                _json_response(self, {"error": f"missing field: {exc}"}, 400)
            except Exception as exc:
                _json_response(self, {"error": str(exc)}, 400)
            return
        if path == "/api/harbor-viewer/global":
            jobs_dir = str(body.get("jobsDir") or "").strip() or None
            _json_response(self, self._ensure_global_harbor_viewer(jobs_dir))
            return
        if path == "/api/runs":
            try:
                run = self.store.create_run(
                    template_id=str(body["templateId"]),
                    display_name=str(body.get("displayName") or "") or None,
                )
            except Exception as exc:
                _json_response(self, {"error": str(exc)}, 400)
                return
            _json_response(self, run, 201)
            return
        if path.startswith("/api/runs/") and path.endswith("/batches"):
            run_id = path.split("/")[3]
            try:
                batch = self.store.create_batch(
                    run_id=run_id,
                    selected_case_ids=[str(item) for item in body.get("selectedCaseIds") or []],
                    preferred_worker_id=str(body.get("preferredWorkerId") or "") or None,
                    batch_options=dict(body.get("batchOptions") or {}),
                )
            except Exception as exc:
                _json_response(self, {"error": str(exc)}, 400)
                return
            _json_response(self, batch, 201)
            return
        if path.startswith("/api/runs/") and path.endswith("/distribute"):
            run_id = path.split("/")[3]
            worker_ids = [str(item) for item in body.get("workerIds") or [] if str(item).strip()]
            selected_case_ids = [str(item) for item in body.get("selectedCaseIds") or [] if str(item).strip()]
            try:
                batches = self.store.create_sharded_batches(
                    run_id=run_id,
                    selected_case_ids=selected_case_ids,
                    worker_ids=worker_ids,
                    batch_options=dict(body.get("batchOptions") or {}),
                )
            except Exception as exc:
                _json_response(self, {"error": str(exc)}, 400)
                return
            _json_response(self, {"runId": run_id, "batches": batches}, 201)
            return
        if path.startswith("/api/runs/") and path.endswith("/rerun-exceptions"):
            if self.run_rerun_coordinator is None:
                _json_response(self, {"error": "rerun coordinator unavailable"}, 500)
                return
            if not isinstance(body, dict):
                _json_response(self, {"error": "request body must be a JSON object"}, 400)
                return
            run_id = path.split("/")[3]
            try:
                result = self.run_rerun_coordinator.start_rerun(run_id, config=body)
            except RerunValidationError as exc:
                _json_response(self, {"error": exc.message}, exc.code)
                return
            _json_response(self, result, 201)
            return
        if path == "/api/workers/register":
            worker = self.store.register_worker(
                worker_id=str(body["workerId"]),
                display_name=str(body.get("displayName") or body["workerId"]),
                host=str(body.get("host") or ""),
                slots_total=int(body.get("slotsTotal") or 1),
                slots_used=int(body.get("slotsUsed") or 0),
                capabilities=dict(body.get("capabilities") or {}),
            )
            _json_response(self, worker)
            return
        if path == "/api/ssh/test":
            host_alias = str(body.get("hostAlias") or "").strip()
            if not host_alias:
                _json_response(self, {"error": "hostAlias is required"}, 400)
                return
            config_path = (self.ssh_config_path or Path("~/.ssh/config")).expanduser()
            ok, message = test_ssh_alias(config_path, host_alias)
            _json_response(self, {"ok": ok, "message": message})
            return
        if path == "/api/workers/provision":
            if self.provisioner is None:
                _json_response(self, {"error": "provisioner unavailable"}, 500)
                return
            worker_id = str(body.get("workerId") or "").strip()
            mode = str(body.get("mode") or "").strip()
            ssh_host_alias = str(body.get("sshHostAlias") or "").strip()
            if not worker_id or mode not in {"fresh", "join"} or not ssh_host_alias:
                _json_response(self, {"error": "workerId, mode, sshHostAlias are required"}, 400)
                return
            if self.store.worker_exists(worker_id):
                _json_response(self, {"error": "worker already exists"}, 409)
                return
            connection_mode = str(body.get("connectionMode") or "direct").strip()
            if connection_mode not in {"direct", "tunnel"}:
                _json_response(self, {"error": "connectionMode must be direct or tunnel"}, 400)
                return
            controller_internal_ip = str(body.get("controllerInternalIp") or "").strip() or None
            tunnel_remote_port = (
                int(body.get("tunnelRemotePort") or 17380)
                if body.get("tunnelRemotePort") is not None
                else 17380
            )
            if connection_mode == "direct":
                if not controller_internal_ip:
                    _json_response(self, {"error": "direct mode requires controllerInternalIp"}, 400)
                    return
                if not _validate_controller_internal_ip(controller_internal_ip):
                    _json_response(self, {"error": "invalid controllerInternalIp"}, 400)
                    return
                tunnel_remote_port = None
            else:
                controller_internal_ip = None
                if tunnel_remote_port < 1024 or tunnel_remote_port > 65535:
                    _json_response(self, {"error": "tunnelRemotePort out of range"}, 400)
                    return
            config_path = (self.ssh_config_path or Path("~/.ssh/config")).expanduser()
            ok, message = test_ssh_alias(config_path, ssh_host_alias)
            if not ok:
                _json_response(self, {"error": message}, 400)
                return
            bootstrap_alias = str(body.get("sshBootstrapHostAlias") or "").strip() or None
            djn_password = str(body.get("djnPassword") or "")
            if mode == "fresh":
                if not bootstrap_alias or not djn_password:
                    _json_response(
                        self,
                        {"error": "fresh mode requires sshBootstrapHostAlias and djnPassword"},
                        400,
                    )
                    return
                ok_root, root_message = test_ssh_alias(config_path, bootstrap_alias)
                if not ok_root:
                    _json_response(self, {"error": root_message}, 400)
                    return
            display_name = str(body.get("displayName") or worker_id)
            slots_total = int(body.get("slotsTotal") or 1)

            job_id = new_id("prov")
            self.store.create_provisioning_worker(
                worker_id=worker_id,
                display_name=display_name,
                slots_total=slots_total,
                ssh_host_alias=ssh_host_alias,
                ssh_bootstrap_host_alias=bootstrap_alias,
                connection_mode=connection_mode,
                controller_internal_ip=controller_internal_ip,
                tunnel_remote_port=tunnel_remote_port,
            )
            self.store.create_provision_job(
                job_id=job_id,
                worker_id=worker_id,
                mode=mode,
                steps=self.provisioner.initial_steps(mode, connection_mode=connection_mode),
            )
            self.provisioner.start_job_async(
                job_id=job_id,
                worker_id=worker_id,
                mode=mode,
                ssh_host_alias=ssh_host_alias,
                ssh_bootstrap_host_alias=bootstrap_alias,
                djn_password=djn_password or None,
                connection_mode=connection_mode,
                controller_internal_ip=controller_internal_ip,
                tunnel_remote_port=tunnel_remote_port,
                display_name=display_name,
                slots_total=slots_total,
            )
            _json_response(self, {"jobId": job_id, "workerId": worker_id, "status": "pending"}, 201)
            return
        if path.startswith("/api/workers/provision/") and path.endswith("/cancel"):
            job_id = path.split("/")[4]
            job = self.store.get_provision_job(job_id)
            if not job:
                _json_response(self, {"error": "job not found"}, 404)
                return
            if self.provisioner is None:
                _json_response(self, {"error": "provisioner unavailable"}, 500)
                return
            worker = next(
                (item for item in self.store.list_workers() if item["worker_id"] == job["worker_id"]),
                None,
            )
            ssh_alias = str(worker.get("ssh_host_alias") or "") if worker else ""
            connection_mode = str(worker.get("connection_mode") or "tunnel") if worker else "tunnel"
            self.provisioner.cancel_job(
                job_id,
                worker_id=str(job["worker_id"]),
                ssh_host_alias=ssh_alias,
                connection_mode=connection_mode,
            )
            self.store.set_worker_provision_status(
                str(job["worker_id"]),
                provision_status="failed",
                last_provision_error="cancelled by operator",
            )
            _json_response(self, {"ok": True, "jobId": job_id, "status": "cancelled"})
            return
        if path.startswith("/api/workers/update/") and path.endswith("/cancel"):
            job_id = path.split("/")[4]
            job = self.store.get_worker_update_job(job_id)
            if not job:
                _json_response(self, {"error": "job not found"}, 404)
                return
            if self.worker_updater is None:
                _json_response(self, {"error": "worker updater unavailable"}, 500)
                return
            worker = next(
                (item for item in self.store.list_workers() if item["worker_id"] == job["worker_id"]),
                None,
            )
            ssh_alias = str(worker.get("ssh_host_alias") or "") if worker else ""
            connection_mode = str(worker.get("connection_mode") or "tunnel") if worker else "tunnel"
            self.worker_updater.cancel_job(
                job_id,
                worker_id=str(job["worker_id"]),
                ssh_host_alias=ssh_alias,
                connection_mode=connection_mode,
            )
            _json_response(self, {"ok": True, "jobId": job_id, "status": "cancelled"})
            return
        if path.startswith("/api/workers/") and path.endswith("/update"):
            worker_id = path.split("/")[3]
            if self.worker_updater is None:
                _json_response(self, {"error": "worker updater unavailable"}, 500)
                return
            worker = next(
                (item for item in self.store.list_workers() if item["worker_id"] == worker_id),
                None,
            )
            if not worker:
                _json_response(self, {"error": "worker not found"}, 404)
                return
            ssh_alias = str(worker.get("ssh_host_alias") or "").strip()
            if not ssh_alias:
                _json_response(self, {"error": "ssh_host_alias required"}, 400)
                return
            counts = self.store.worker_has_active_batches(worker_id)
            if counts["runningCount"] > 0 or counts["queuedCount"] > 0:
                _json_response(
                    self,
                    {
                        "error": "worker has active batches",
                        "runningCount": counts["runningCount"],
                        "queuedCount": counts["queuedCount"],
                    },
                    409,
                )
                return
            if self.store.get_active_worker_update_job_for_worker(worker_id):
                _json_response(self, {"error": "update already in progress"}, 409)
                return
            provision_status = str(worker.get("provision_status") or "none")
            latest_prov = self.store.get_latest_provision_job_for_worker(worker_id)
            if provision_status == "provisioning" or (
                latest_prov
                and str(latest_prov["status"]) in {"pending", "running"}
                and provision_status not in {"ready", "failed", "none"}
            ):
                _json_response(self, {"error": "provision in progress"}, 409)
                return
            raw_targets = body.get("targets")
            if raw_targets is None:
                targets = ["aeo", "harbor"]
            elif isinstance(raw_targets, list):
                targets = [str(item) for item in raw_targets]
            else:
                _json_response(self, {"error": "targets must be an array"}, 400)
                return
            allowed = {"aeo", "harbor"}
            if not targets or any(item not in allowed for item in targets):
                _json_response(self, {"error": "targets must contain aeo and/or harbor"}, 400)
                return
            job_id = new_id("upd")
            steps = self.worker_updater.initial_steps(targets)
            self.store.create_worker_update_job(
                job_id=job_id,
                worker_id=worker_id,
                targets=targets,
                steps=steps,
            )
            self.worker_updater.start_job_async(
                job_id=job_id,
                worker_id=worker_id,
                targets=targets,
                ssh_host_alias=ssh_alias,
                connection_mode=str(worker.get("connection_mode") or "direct"),
                controller_internal_ip=worker.get("controller_internal_ip"),
                tunnel_remote_port=worker.get("tunnel_remote_port"),
                display_name=str(worker.get("display_name") or worker_id),
                slots_total=int(worker.get("slots_total") or 1),
                worker=worker,
            )
            _json_response(
                self,
                {
                    "jobId": job_id,
                    "workerId": worker_id,
                    "status": "pending",
                    "targets": targets,
                },
                202,
            )
            return
        if path.startswith("/api/workers/") and path.endswith("/settings"):
            worker_id = path.split("/")[3]
            worker = self.store.update_worker_settings(
                worker_id=worker_id,
                display_name=str(body.get("displayName") or "") or None,
                slots_total=int(body["slotsTotal"]) if body.get("slotsTotal") is not None else None,
                allocation_weight=float(body["allocationWeight"]) if body.get("allocationWeight") is not None else None,
                enabled=bool(body["enabled"]) if body.get("enabled") is not None else None,
                note=str(body.get("note") or "") if body.get("note") is not None else None,
                tags=[str(item) for item in body.get("tags") or []] if isinstance(body.get("tags"), list) else None,
            )
            if not worker:
                _json_response(self, {"error": "worker not found"}, 404)
                return
            _json_response(self, worker)
            return
        if path.startswith("/api/batches/") and path.endswith("/viewer"):
            batch_id = path.split("/")[3]
            detail = self.store.get_batch_detail(batch_id)
            if not detail:
                _json_response(self, {"error": "batch not found"}, 404)
                return
            artifact_index = detail["batch"].get("artifact_index") or {}
            job_dir = str(artifact_index.get("jobDir") or "").strip()
            if not job_dir:
                _json_response(
                    self,
                    {"available": False, "reason": "当前 batch 尚未回收本机 Harbor 结果，无法嵌入 Harbor viewer。"},
                )
                return
            local_job_dir = Path(job_dir).expanduser().resolve()
            if not local_job_dir.exists():
                _json_response(
                    self,
                    {"available": False, "reason": "Harbor jobs 目录当前不在 controller 本机可读范围内。"},
                )
                return
            jobs_dir = local_job_dir.parent
            viewer_id = batch_id
            try:
                session = self.viewer_manager.ensure_viewer(viewer_id=viewer_id, jobs_dir=jobs_dir)
            except Exception as exc:
                _json_response(self, {"available": False, "reason": str(exc)}, 500)
                return
            _json_response(
                self,
                {
                    "available": True,
                    "viewerId": viewer_id,
                    "embeddedUrl": f"/harbor-viewer/{viewer_id}/",
                    "upstreamPort": session.port,
                },
            )
            return
        if path == "/api/workers/claim":
            payload = self.store.claim_next_batch(str(body["workerId"]))
            _json_response(self, {"task": payload})
            return
        if path == "/api/workers/heartbeat":
            batch = self.store.update_batch_progress(
                batch_id=str(body["batchId"]),
                worker_id=str(body["workerId"]),
                status=str(body.get("status") or "running"),
                current_step=str(body.get("currentStep") or "") or None,
                finished=bool(body.get("finished") or False),
                error_text=str(body.get("errorText") or "") or None,
                summary=body.get("summary") if isinstance(body.get("summary"), dict) else None,
                cases=body.get("cases") if isinstance(body.get("cases"), list) else None,
                executor_metadata=body.get("executorMetadata") if isinstance(body.get("executorMetadata"), dict) else None,
                artifact_index=body.get("artifactIndex") if isinstance(body.get("artifactIndex"), dict) else None,
            )
            if not batch:
                _json_response(self, {"error": "batch not found"}, 404)
                return
            batch_row = self.store.get_batch(str(body["batchId"]))
            if (
                batch_row
                and str(batch_row.get("batch_kind") or "") == "exception_rerun"
                and bool(body.get("finished"))
                and isinstance(body.get("cases"), list)
            ):
                metadata = (
                    body.get("executorMetadata") if isinstance(body.get("executorMetadata"), dict) else {}
                )
                jobs_dir = Path(str(metadata.get("combinedJobsDir") or DEFAULT_JOBS_DIR)).expanduser().resolve()
                _apply_exception_rerun_merge(
                    store=self.store,
                    batch_id=str(body["batchId"]),
                    rerun_cases=body["cases"],
                    jobs_dir=jobs_dir,
                )
            if self.asset_syncer is not None:
                batch_row = self.store.get_batch(str(body["batchId"]))
                if batch_row and self.store.is_run_terminal(str(batch_row["run_id"])):
                    run = self.store.get_run(str(batch_row["run_id"]))
                    if run and str(run.get("sync_status") or "") in {"succeeded", "failed"}:
                        self.asset_syncer.cleanup_run_sync_assets(str(batch_row["run_id"]))
            _json_response(self, {"batch": batch})
            return
        _json_response(self, {"error": "not found"}, 404)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if not self._is_authorized():
            _json_response(self, {"error": "forbidden"}, 403)
            return
        parts = path.split("/")
        if len(parts) == 4 and parts[1] == "api" and parts[2] == "workers":
            worker_id = parts[3]
            reserved = {"provision", "runtime", "register", "claim", "heartbeat", "job-archive", "update"}
            if worker_id in reserved:
                _json_response(self, {"error": "not found"}, 404)
                return
            worker = next(
                (item for item in self.store.list_workers() if item["worker_id"] == worker_id),
                None,
            )
            if not worker:
                _json_response(self, {"error": "worker not found"}, 404)
                return
            counts = self.store.worker_has_active_batches(worker_id)
            if counts["runningCount"] > 0 or counts["queuedCount"] > 0:
                _json_response(
                    self,
                    {
                        "error": "worker has active batches",
                        "runningCount": counts["runningCount"],
                        "queuedCount": counts["queuedCount"],
                    },
                    409,
                )
                return
            if self.worker_updater is not None:
                active_update = self.store.get_active_worker_update_job_for_worker(worker_id)
                if active_update:
                    self.worker_updater.cancel_job(
                        str(active_update["job_id"]),
                        worker_id=worker_id,
                        ssh_host_alias=str(worker.get("ssh_host_alias") or ""),
                        connection_mode=str(worker.get("connection_mode") or "tunnel"),
                    )
            if self.provisioner is not None:
                latest = self.store.get_latest_provision_job_for_worker(worker_id)
                if latest and str(latest["status"]) in {"pending", "running"}:
                    ssh_alias = str(worker.get("ssh_host_alias") or "")
                    connection_mode = str(worker.get("connection_mode") or "tunnel")
                    self.provisioner.cancel_job(
                        str(latest["job_id"]),
                        worker_id=worker_id,
                        ssh_host_alias=ssh_alias,
                        connection_mode=connection_mode,
                    )
                cleanup = self.provisioner.decommission_worker(
                    worker_id=worker_id,
                    ssh_host_alias=str(worker.get("ssh_host_alias") or "") or None,
                    connection_mode=str(worker.get("connection_mode") or "tunnel"),
                )
            else:
                cleanup = {"remoteCleanup": "skipped", "warnings": []}
            if not self.store.delete_worker(worker_id):
                _json_response(self, {"error": "worker not found"}, 404)
                return
            payload: dict[str, object] = {
                "ok": True,
                "workerId": worker_id,
                "remoteCleanup": cleanup.get("remoteCleanup", "skipped"),
            }
            warnings = cleanup.get("warnings") or []
            if warnings:
                payload["warnings"] = warnings
            _json_response(self, payload)
            return
        _json_response(self, {"error": "not found"}, 404)


class ThreadedServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agent Eval Orchestrator controller")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--shared-root", default=None)
    parser.add_argument("--auth-token", default=None)
    parser.add_argument("--github-token", default=None)
    parser.add_argument("--ssh-config", default="~/.ssh/config")
    args = parser.parse_args(argv)

    github_token = str(args.github_token or os.environ.get("AEO_GITHUB_TOKEN") or "") or None

    layout = default_layout(args.shared_root)
    store = Store(layout)
    repo_root = Path(__file__).resolve().parents[3]
    bootstrap_script = repo_root / "scripts" / "bootstrap-huawei-worker.sh"
    ssh_config_path = Path(args.ssh_config).expanduser()
    provisioner = Provisioner(
        store=store,
        ssh_config_path=ssh_config_path,
        auth_token=str(args.auth_token or "") or None,
        controller_port=args.port,
        bootstrap_script_path=bootstrap_script,
        tunnel_state_path=layout.controller_dir / "tunnels.json",
    )
    worker_updater = WorkerUpdater(
        store=store,
        ssh_config_path=ssh_config_path,
        auth_token=str(args.auth_token or "") or "",
        controller_port=args.port,
        provisioner=provisioner,
        github_token=github_token,
    )
    asset_syncer = AssetSyncer(
        store=store,
        ssh_config_path=ssh_config_path,
        controller_shared_root=layout.root,
    )
    run_rerun_coordinator = RunRerunCoordinator(store=store, asset_syncer=asset_syncer)
    server = ThreadedServer((args.host, args.port), Handler)
    Handler.store = store
    Handler.auth_token = str(args.auth_token or "") or None
    Handler.provisioner = provisioner
    Handler.worker_updater = worker_updater
    Handler.asset_syncer = asset_syncer
    Handler.run_rerun_coordinator = run_rerun_coordinator
    Handler.controller_shared_root = layout.root
    Handler.ssh_config_path = ssh_config_path
    Handler.viewer_manager = HarborViewerManager(
        harbor_repo=Path("/root/projects/harbor").resolve(),
        logs_dir=layout.controller_dir / "viewer-logs",
    )
    print(f"[controller] listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
