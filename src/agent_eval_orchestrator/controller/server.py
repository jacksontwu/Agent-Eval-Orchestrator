from __future__ import annotations

import argparse
import base64
from http.cookies import SimpleCookie
import io
import ipaddress
import json
from math import fsum
from pathlib import Path
import shutil
import subprocess
import tarfile
import time
from uuid import uuid4
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
from agent_eval_orchestrator.core.ids import now_iso, sanitize_name
from agent_eval_orchestrator.controller.harbor_viewer import HarborViewerManager
from agent_eval_orchestrator.controller.static import INDEX_HTML
from agent_eval_orchestrator.storage.layout import default_layout
from agent_eval_orchestrator.storage.store import Store


GLOBAL_VIEWER_PORT = 7369
DEFAULT_OWNER = "demo"
DEFAULT_AGENT_NAME = "bitfun-cli"
DEFAULT_JOBS_DIR = DEFAULT_HARBOR_REPO / "jobs"
DEFAULT_IMPORTED_JOBS_DIRNAME = "imported-jobs"


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


def _iter_trial_dirs(job_dir: Path) -> list[Path]:
    if not job_dir.exists():
        return []
    return sorted(
        child
        for child in job_dir.iterdir()
        if child.is_dir() and (child / "result.json").exists()
    )


def _copy_trial_dirs(source_job_dir: Path, target_job_dir: Path) -> None:
    for trial_dir in _iter_trial_dirs(source_job_dir):
        target_trial_dir = target_job_dir / trial_dir.name
        if target_trial_dir.exists():
            shutil.rmtree(target_trial_dir)
        shutil.copytree(trial_dir, target_trial_dir)


def _write_merged_job(
    *,
    merged_job_dir: Path,
    merged_job_name: str,
    source_job_dirs: list[Path],
) -> None:
    merged_job_dir.mkdir(parents=True, exist_ok=True)

    config_payload: dict[str, object] | None = None
    for source_job_dir in source_job_dirs:
        config_path = source_job_dir / "config.json"
        if config_payload is None and config_path.exists():
            config_payload = json.loads(config_path.read_text())
        _copy_trial_dirs(source_job_dir, merged_job_dir)

    if config_payload is None:
        raise RuntimeError("no job config found while merging Harbor jobs")

    config_payload["job_name"] = merged_job_name
    config_payload["jobs_dir"] = str(merged_job_dir.parent)
    (merged_job_dir / "config.json").write_text(
        json.dumps(config_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    trial_payloads = [
        json.loads((trial_dir / "result.json").read_text(encoding="utf-8"))
        for trial_dir in _iter_trial_dirs(merged_job_dir)
    ]
    if not trial_payloads:
        raise RuntimeError("no Harbor trial results found while merging jobs")

    evals: dict[str, dict[str, object]] = {}
    n_input_tokens: int | None = 0
    n_cache_tokens: int | None = 0
    n_output_tokens: int | None = 0
    cost_usd: float | None = 0.0
    n_errored_trials = 0
    n_cancelled_trials = 0

    def _sum_optional_int(current: int | None, value: object) -> int | None:
        if value is None:
            return current
        if not isinstance(value, int):
            return current
        return (current or 0) + value

    def _sum_optional_float(current: float | None, value: object) -> float | None:
        if value is None:
            return current
        if not isinstance(value, (float, int)):
            return current
        return float((current or 0.0) + float(value))

    for trial in trial_payloads:
        agent_info = trial.get("agent_info") or {}
        agent_name = str(agent_info.get("name") or "unknown")
        model_info = agent_info.get("model_info") or {}
        model_name = str(model_info.get("name") or "").strip()
        source = str(trial.get("source") or "adhoc")
        eval_key = f"{agent_name}__{model_name}__{source}" if model_name else f"{agent_name}__{source}"
        eval_stats = evals.setdefault(
            eval_key,
            {
                "n_trials": 0,
                "n_errors": 0,
                "metrics": [],
                "pass_at_k": {},
                "reward_stats": {},
                "exception_stats": {},
            },
        )
        rewards = ((trial.get("verifier_result") or {}).get("rewards") or {})
        if isinstance(rewards, dict) and rewards:
            eval_stats["n_trials"] = int(eval_stats["n_trials"]) + 1
            reward_stats = eval_stats["reward_stats"]
            for reward_key, reward_value in rewards.items():
                bucket = reward_stats.setdefault(str(reward_key), {})
                bucket.setdefault(str(reward_value), []).append(str(trial.get("trial_name") or "unknown"))
        exception_info = trial.get("exception_info") or {}
        if exception_info:
            n_errored_trials += 1
            exception_type = str(exception_info.get("exception_type") or "UnknownError")
            eval_stats["n_errors"] = int(eval_stats["n_errors"]) + 1
            exception_stats = eval_stats["exception_stats"]
            exception_stats.setdefault(exception_type, []).append(str(trial.get("trial_name") or "unknown"))
            if exception_type == "CancelledError":
                n_cancelled_trials += 1

        agent_result = trial.get("agent_result") or {}
        n_input_tokens = _sum_optional_int(n_input_tokens, agent_result.get("n_input_tokens"))
        n_cache_tokens = _sum_optional_int(n_cache_tokens, agent_result.get("n_cache_tokens"))
        n_output_tokens = _sum_optional_int(n_output_tokens, agent_result.get("n_output_tokens"))
        cost_usd = _sum_optional_float(cost_usd, agent_result.get("cost_usd"))

    for eval_stats in evals.values():
        reward_stats = eval_stats["reward_stats"]
        metric_entries: list[dict[str, float]] = []
        for reward_key, buckets in reward_stats.items():
            values: list[float] = []
            for bucket_value, trial_names in buckets.items():
                try:
                    values.extend([float(bucket_value)] * len(trial_names))
                except ValueError:
                    continue
            if values:
                metric_entries.append({"mean": fsum(values) / len(values)})
        eval_stats["metrics"] = metric_entries

    started_values = [str(trial.get("started_at")) for trial in trial_payloads if trial.get("started_at")]
    finished_values = [str(trial.get("finished_at")) for trial in trial_payloads if trial.get("finished_at")]
    updated_values = finished_values or started_values
    job_result = {
        "id": str(uuid4()),
        "started_at": min(started_values) if started_values else now_iso(),
        "updated_at": max(updated_values) if updated_values else now_iso(),
        "finished_at": max(finished_values) if len(finished_values) == len(trial_payloads) else None,
        "n_total_trials": len(trial_payloads),
        "stats": {
            "n_completed_trials": len(trial_payloads),
            "n_errored_trials": n_errored_trials,
            "n_running_trials": 0,
            "n_pending_trials": 0,
            "n_cancelled_trials": n_cancelled_trials,
            "n_retries": 0,
            "evals": evals,
            "n_input_tokens": n_input_tokens,
            "n_cache_tokens": n_cache_tokens,
            "n_output_tokens": n_output_tokens,
            "cost_usd": cost_usd,
        },
    }
    (merged_job_dir / "result.json").write_text(
        json.dumps(job_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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
    for batch in store.list_batches_for_run(run_id):
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


def _worker_repo_root(worker: dict[str, object] | None) -> Path | None:
    if not worker:
        return None
    capabilities = worker.get("capabilities") if isinstance(worker.get("capabilities"), dict) else {}
    shared_root = str(capabilities.get("sharedRoot") or "").strip()
    if not shared_root:
        return None
    shared_path = Path(shared_root).expanduser()
    return shared_path.parent if shared_path.name == "runtime" else shared_path


def _map_dataset_for_worker(dataset_ref: str, worker: dict[str, object] | None) -> str:
    dataset_path = Path(dataset_ref).expanduser().resolve()
    repo_root = Path("/root/projects/agent-eval-orchestrator").resolve()
    worker_root = _worker_repo_root(worker)
    if worker_root and _is_subpath(dataset_path, repo_root):
        return str(worker_root / dataset_path.relative_to(repo_root))
    return str(dataset_path)


def _default_harbor_for_worker(worker_id: str, worker: dict[str, object] | None) -> str:
    worker_root = _worker_repo_root(worker)
    if worker_root:
        return str(worker_root.parent / "harbor")
    if worker_id == "remote-a":
        return "/home/wt/harbor"
    return str(DEFAULT_HARBOR_REPO)


def _default_uv_for_worker(worker_id: str, worker: dict[str, object] | None) -> str:
    worker_root = _worker_repo_root(worker)
    if worker_id == "local-a":
        return "/root/.local/bin/uv"
    if worker_root:
        home = worker_root.parent
        return str(home / ".local" / "bin" / "uv")
    if worker_id == "remote-a":
        return "/home/wt/.local/bin/uv"
    return "/root/.local/bin/uv"


def _default_bitfun_mounts(worker_id: str, worker: dict[str, object] | None) -> list[dict[str, str]]:
    worker_root = _worker_repo_root(worker)
    if worker_root:
        home = worker_root.parent
        if worker_id == "local-a" or str(home) == "/root":
            bitfun_bin = "/root/projects/BitFun/target/release/bitfun-cli"
            bitfun_config = "/root/.config/bitfun"
        else:
            bitfun_bin = str(home / "bitfun-cli")
            bitfun_config = str(home / ".config" / "bitfun")
    elif worker_id == "remote-a":
        bitfun_bin = "/home/wt/bitfun-cli"
        bitfun_config = "/home/wt/.config/bitfun"
    else:
        bitfun_bin = "/root/projects/BitFun/target/release/bitfun-cli"
        bitfun_config = "/root/.config/bitfun"
    return [
        {"type": "bind", "source": bitfun_bin, "target": "/usr/local/bin/bitfun-cli"},
        {"type": "bind", "source": bitfun_config, "target": "/testbed/.config/bitfun"},
    ]


def _build_executor_config(
    *,
    dataset_ref: str,
    worker_ids: list[str],
    workers: list[dict[str, object]],
    body_config: dict[str, object],
    jobs_dir: str,
) -> dict[str, object]:
    workers_by_id = {str(worker["worker_id"]): worker for worker in workers}
    harbor_repo_by_worker: dict[str, str] = {}
    dataset_path_by_worker: dict[str, str] = {}
    uv_binary_by_worker: dict[str, str] = {}
    mounts_by_worker: dict[str, list[dict[str, str]]] = {}
    agent_env_by_worker: dict[str, dict[str, str]] = {}
    for worker_id in worker_ids:
        worker = workers_by_id.get(worker_id)
        harbor_repo_by_worker[worker_id] = _default_harbor_for_worker(worker_id, worker)
        dataset_path_by_worker[worker_id] = _map_dataset_for_worker(dataset_ref, worker)
        uv_binary_by_worker[worker_id] = _default_uv_for_worker(worker_id, worker)
        mounts_by_worker[worker_id] = _default_bitfun_mounts(worker_id, worker)
        agent_env_by_worker[worker_id] = {"XDG_CONFIG_HOME": "/testbed/.config"}
    n_concurrent = int(body_config.get("nConcurrent") or DEFAULT_PER_WORKER_CONCURRENCY)
    return {
        "agentName": str(body_config.get("agentName") or DEFAULT_AGENT_NAME),
        "envType": "docker",
        "nConcurrent": n_concurrent,
        "harborRepoPathByWorker": harbor_repo_by_worker,
        "datasetPathByWorker": dataset_path_by_worker,
        "uvBinaryByWorker": uv_binary_by_worker,
        "mountsByWorker": mounts_by_worker,
        "agentEnvByWorker": agent_env_by_worker,
        "collectJobs": True,
        "combinedJobsDir": jobs_dir,
    }


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
            for merged_job_name, source_job_dirs in grouped_sources:
                merged_job_dir = jobs_dir / merged_job_name
                if merged_job_dir.exists():
                    shutil.rmtree(merged_job_dir)
                try:
                    _write_merged_job(
                        merged_job_dir=merged_job_dir,
                        merged_job_name=merged_job_name,
                        source_job_dirs=source_job_dirs,
                    )
                except RuntimeError:
                    if merged_job_dir.exists():
                        shutil.rmtree(merged_job_dir)
                    continue
                merged_names.append(merged_job_name)
                for batch in self.store.list_batches_for_run(str(run["run_id"])):
                    legacy_batch_dir = jobs_dir / str(batch["batch_id"])
                    if legacy_batch_dir.exists() and legacy_batch_dir != merged_job_dir:
                        shutil.rmtree(legacy_batch_dir)
        return merged_names

    def _ensure_global_harbor_viewer(self) -> dict[str, object]:
        jobs_dir = DEFAULT_JOBS_DIR
        jobs_dir.mkdir(parents=True, exist_ok=True)
        self._rebuild_merged_jobs(jobs_dir)
        try:
            with request.urlopen(f"http://127.0.0.1:{GLOBAL_VIEWER_PORT}/api/health", timeout=1):
                return {
                    "available": True,
                    "url": f"http://{self.headers.get('Host', '').split(':')[0] or '127.0.0.1'}:{GLOBAL_VIEWER_PORT}/",
                    "jobsDir": str(jobs_dir),
                    "port": GLOBAL_VIEWER_PORT,
                }
        except Exception:
            pass

        if self.global_viewer_process and self.global_viewer_process.poll() is None:
            self.global_viewer_process.terminate()
        log_path = self.store.layout.controller_dir / "logs" / f"harbor-viewer-{GLOBAL_VIEWER_PORT}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("a", encoding="utf-8")
        command = [
            "/bin/bash",
            "-lc",
            f"cd {DEFAULT_HARBOR_REPO} && uv run harbor view --port {GLOBAL_VIEWER_PORT} --host 0.0.0.0 ./jobs/",
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
                        "url": f"http://{self.headers.get('Host', '').split(':')[0] or '127.0.0.1'}:{GLOBAL_VIEWER_PORT}/",
                        "jobsDir": str(jobs_dir),
                        "port": GLOBAL_VIEWER_PORT,
                    }
            except Exception:
                time.sleep(0.5)
        return {"available": False, "reason": "Harbor viewer did not become ready", "jobsDir": str(jobs_dir)}

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
            _json_response(self, self._ensure_global_harbor_viewer())
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
        body = self._read_json()
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
                dataset_ref = str(body["datasetRef"])
                allowed_dataset_refs = set(self.store.list_dataset_refs())
                if dataset_ref not in allowed_dataset_refs:
                    raise RuntimeError("dataset_ref must be one of the preset datasets")
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
                    case_ids = self.store.list_dataset_case_ids(dataset_ref)
                if not worker_ids:
                    raise RuntimeError("worker_ids must not be empty")
                jobs_dir = str(body.get("jobsDir") or DEFAULT_JOBS_DIR).strip() or str(DEFAULT_JOBS_DIR)
                body_config = dict(body.get("executorConfig") or {})
                executor_config = _build_executor_config(
                    dataset_ref=dataset_ref,
                    worker_ids=worker_ids,
                    workers=self.store.list_workers(),
                    body_config=body_config,
                    jobs_dir=jobs_dir,
                )
                task_name = str(body.get("name") or "").strip() or f"{Path(dataset_ref).name}-{now_iso()[:19]}"
                template = self.store.create_task_template(
                    owner=owner,
                    name=task_name,
                    dataset_ref=dataset_ref,
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
                )
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
                    "run": run,
                    "batches": batches,
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
                    run = self.store.get_run(str(batch["run_id"]))
                    if run:
                        merged_job_name = sanitize_name(str(run["display_name"]))
                        source_job_dirs = [
                            source
                            for _, sources in _job_sources_for_run(
                                store=self.store,
                                run_id=str(run["run_id"]),
                                jobs_dir=jobs_dir,
                            )
                            for source in sources
                        ]
                        if source_job_dirs:
                            merged_job_dir = jobs_dir / merged_job_name
                            if merged_job_dir.exists():
                                shutil.rmtree(merged_job_dir)
                            _write_merged_job(
                                merged_job_dir=merged_job_dir,
                                merged_job_name=merged_job_name,
                                source_job_dirs=source_job_dirs,
                            )
                            legacy_batch_dir = jobs_dir / batch_id
                            if legacy_batch_dir.exists():
                                shutil.rmtree(legacy_batch_dir)
                _json_response(self, {"ok": True, "batchId": batch_id, "jobsDir": str(jobs_dir)})
            except KeyError as exc:
                _json_response(self, {"error": f"missing field: {exc}"}, 400)
            except Exception as exc:
                _json_response(self, {"error": str(exc)}, 400)
            return
        if path == "/api/harbor-viewer/global":
            _json_response(self, self._ensure_global_harbor_viewer())
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
        if path.startswith("/api/workers/") and path.endswith("/settings"):
            worker_id = path.split("/")[3]
            worker = self.store.update_worker_settings(
                worker_id=worker_id,
                display_name=str(body.get("displayName") or "") or None,
                slots_total=int(body["slotsTotal"]) if body.get("slotsTotal") is not None else None,
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
            _json_response(self, {"batch": batch})
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
    args = parser.parse_args(argv)

    layout = default_layout(args.shared_root)
    store = Store(layout)
    server = ThreadedServer((args.host, args.port), Handler)
    Handler.store = store
    Handler.auth_token = str(args.auth_token or "") or None
    Handler.viewer_manager = HarborViewerManager(
        harbor_repo=Path("/root/projects/harbor").resolve(),
        logs_dir=layout.controller_dir / "viewer-logs",
    )
    print(f"[controller] listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
