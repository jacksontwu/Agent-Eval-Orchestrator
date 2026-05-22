from __future__ import annotations

import argparse
import base64
import io
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import tarfile
import time
from urllib import request

from agent_eval_orchestrator.core.defaults import (
    DEFAULT_HARBOR_REPO,
    DEFAULT_MIN_FREE_DISK_GB,
    DEFAULT_POLL_INTERVAL_SEC,
    DEFAULT_SHARED_ROOT,
    DEFAULT_SLOTS,
)
from agent_eval_orchestrator.executors.harbor import HarborExecutor
from agent_eval_orchestrator.normalizers.harbor import normalize_harbor_job, write_normalized_snapshot
from agent_eval_orchestrator.storage.layout import default_layout


def log(message: str) -> None:
    print(f"[worker] {message}", flush=True)


def post_json(url: str, payload: dict, auth_token: str | None = None) -> dict:
    req = request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if auth_token:
        req.add_header("X-AEO-Token", auth_token)
    with request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


class ActiveBatch:
    def __init__(self, batch_id: str, process: subprocess.Popen, log_file, prepared) -> None:
        self.batch_id = batch_id
        self.process = process
        self.log_file = log_file
        self.prepared = prepared


def _format_gb(value: int) -> str:
    return f"{value / (1024 ** 3):.1f} GiB"


def _check_free_disk(local_root: Path, min_free_bytes: int) -> list[str]:
    problems: list[str] = []
    local_usage = shutil.disk_usage(local_root)
    if local_usage.free < min_free_bytes:
        problems.append(
            f"local-root free space too low: {_format_gb(local_usage.free)} < {_format_gb(min_free_bytes)}"
        )
    docker_root = Path("/var/lib/docker")
    if docker_root.exists():
        docker_usage = shutil.disk_usage(docker_root)
        if docker_usage.free < min_free_bytes:
            problems.append(
                f"docker-root free space too low: {_format_gb(docker_usage.free)} < {_format_gb(min_free_bytes)}"
            )
    return problems


def _read_mem_total_bytes() -> int:
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return 0
    for line in meminfo.read_text().splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            if len(parts) >= 2:
                return int(parts[1]) * 1024
    return 0


def _collect_capabilities(shared_root: Path, local_root: Path, slots_total: int) -> dict[str, object]:
    local_usage = shutil.disk_usage(local_root)
    docker_root = Path("/var/lib/docker")
    docker_free_bytes = shutil.disk_usage(docker_root).free if docker_root.exists() else 0
    return {
        "sharedRoot": str(shared_root),
        "localRoot": str(local_root),
        "defaultHarborRepo": str(DEFAULT_HARBOR_REPO),
        "cpuCount": int(os.cpu_count() or 1),
        "memoryTotalBytes": _read_mem_total_bytes(),
        "localFreeBytes": int(local_usage.free),
        "dockerFreeBytes": int(docker_free_bytes),
        "slotsTotal": int(slots_total),
    }


def _tar_directory(path: Path) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        tar.add(path, arcname=path.name)
    return buffer.getvalue()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agent Eval Orchestrator worker")
    parser.add_argument("--controller-url", required=True)
    parser.add_argument("--worker-id", default=socket.gethostname())
    parser.add_argument("--display-name", default="")
    parser.add_argument("--host", default=socket.gethostname())
    parser.add_argument("--shared-root", default=str(DEFAULT_SHARED_ROOT))
    parser.add_argument("--local-root", default="")
    parser.add_argument("--slots", type=int, default=DEFAULT_SLOTS)
    parser.add_argument("--poll-interval", type=int, default=DEFAULT_POLL_INTERVAL_SEC)
    parser.add_argument("--min-free-disk-gb", type=int, default=DEFAULT_MIN_FREE_DISK_GB)
    parser.add_argument("--auth-token", default=None)
    args = parser.parse_args(argv)

    shared_root = Path(args.shared_root).expanduser().resolve()
    shared_layout = default_layout(shared_root)
    local_root = Path(args.local_root or (shared_root / "workers" / args.worker_id / "local")).expanduser().resolve()
    local_root.mkdir(parents=True, exist_ok=True)
    min_free_bytes = int(args.min_free_disk_gb) * (1024 ** 3)
    active: dict[str, ActiveBatch] = {}
    stop_requested = False

    def request_stop(signum, _frame) -> None:
        nonlocal stop_requested
        stop_requested = True
        log(f"received signal {signum}, stopping")

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    while not stop_requested:
        try:
            capabilities = _collect_capabilities(shared_root, local_root, args.slots)
            post_json(
                f"{args.controller_url}/api/workers/register",
                {
                    "workerId": args.worker_id,
                    "displayName": args.display_name or args.worker_id,
                    "host": args.host,
                    "slotsTotal": args.slots,
                    "slotsUsed": len(active),
                    "capabilities": capabilities,
                },
                auth_token=str(args.auth_token or "") or None,
            )
        except Exception as exc:
            log(f"register failed: {exc}")

        finished: list[str] = []
        for batch_id, active_batch in active.items():
            code = active_batch.process.poll()
            if code is None:
                try:
                    post_json(
                        f"{args.controller_url}/api/workers/heartbeat",
                        {
                            "workerId": args.worker_id,
                            "batchId": batch_id,
                            "status": "running",
                            "currentStep": "executor-running",
                            "finished": False,
                        },
                        auth_token=str(args.auth_token or "") or None,
                    )
                except Exception as exc:
                    log(f"heartbeat failed for {batch_id}: {exc}")
                continue

            active_batch.log_file.close()
            summary = None
            cases = None
            artifact_index = None
            error_text = None if code == 0 else f"executor exited with code {code}"
            status = "succeeded" if code == 0 else "failed"
            try:
                collected = HarborExecutor().collect(active_batch.prepared)
                if collected.job_dir.exists():
                    summary, cases, artifact_index = normalize_harbor_job(collected.job_dir, batch_id)
                    write_normalized_snapshot(active_batch.prepared.batch_root, summary, cases)
                    if active_batch.prepared.metadata.get("collectJobs"):
                        post_json(
                            f"{args.controller_url}/api/workers/job-archive",
                            {
                                "workerId": args.worker_id,
                                "batchId": batch_id,
                                "jobsDir": active_batch.prepared.metadata.get("combinedJobsDir"),
                                "archiveBase64": base64.b64encode(_tar_directory(collected.job_dir)).decode("ascii"),
                            },
                            auth_token=str(args.auth_token or "") or None,
                        )
            except Exception as exc:
                status = "failed"
                error_text = f"{error_text}; collect failed: {exc}" if error_text else f"collect failed: {exc}"
            try:
                post_json(
                    f"{args.controller_url}/api/workers/heartbeat",
                    {
                        "workerId": args.worker_id,
                        "batchId": batch_id,
                        "status": status,
                        "currentStep": "completed",
                        "finished": True,
                        "errorText": error_text,
                        "summary": summary,
                        "cases": cases,
                        "executorMetadata": active_batch.prepared.metadata,
                        "artifactIndex": artifact_index,
                    },
                    auth_token=str(args.auth_token or "") or None,
                )
            except Exception as exc:
                log(f"final heartbeat failed for {batch_id}: {exc}")
            shutil.rmtree(active_batch.prepared.local_root, ignore_errors=True)
            finished.append(batch_id)

        for batch_id in finished:
            active.pop(batch_id, None)

        while len(active) < args.slots and not stop_requested:
            try:
                response = post_json(
                    f"{args.controller_url}/api/workers/claim",
                    {"workerId": args.worker_id},
                    auth_token=str(args.auth_token or "") or None,
                )
            except Exception as exc:
                log(f"claim failed: {exc}")
                break
            task = response.get("task")
            if not task:
                break
            batch = task["batch"]
            run = task["run"]
            template = task["template"]
            batch = {
                **batch,
                "batch_root": str(
                    shared_layout.batch_dir(
                        str(batch["owner"]),
                        str(batch["run_id"]),
                        str(batch["batch_id"]),
                    )
                ),
            }
            batch_local_root = local_root / str(batch["batch_id"])
            batch_local_root.mkdir(parents=True, exist_ok=True)
            disk_problems = _check_free_disk(local_root, min_free_bytes)
            if disk_problems:
                error_text = "; ".join(disk_problems)
                log(f"refusing batch {batch['batch_id']}: {error_text}")
                shutil.rmtree(batch_local_root, ignore_errors=True)
                post_json(
                    f"{args.controller_url}/api/workers/heartbeat",
                    {
                        "workerId": args.worker_id,
                        "batchId": str(batch["batch_id"]),
                        "status": "failed",
                        "currentStep": "executor-starting",
                        "finished": True,
                        "errorText": error_text,
                    },
                    auth_token=str(args.auth_token or "") or None,
                )
                continue
            prepared = HarborExecutor().prepare(
                batch=batch,
                run=run,
                template=template,
                dataset_ref=str(task["datasetRef"]),
                executor_config=dict(task["executorConfig"]),
                local_root=batch_local_root,
            )
            log_file = prepared.worker_log_path.open("a", encoding="utf-8")
            env = os.environ.copy()
            env.update(prepared.env)
            try:
                process = subprocess.Popen(
                    prepared.command,
                    cwd=str(prepared.cwd),
                    env=env,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except Exception as exc:
                log_file.write(f"spawn failed: {exc}\n")
                log_file.close()
                shutil.rmtree(prepared.local_root, ignore_errors=True)
                post_json(
                    f"{args.controller_url}/api/workers/heartbeat",
                    {
                        "workerId": args.worker_id,
                        "batchId": str(batch["batch_id"]),
                        "status": "failed",
                        "currentStep": "executor-starting",
                        "finished": True,
                        "errorText": f"spawn failed: {exc}",
                        "executorMetadata": prepared.metadata,
                    },
                    auth_token=str(args.auth_token or "") or None,
                )
                continue
            active[str(batch["batch_id"])] = ActiveBatch(str(batch["batch_id"]), process, log_file, prepared)
            log(f"started batch {batch['batch_id']}")

        time.sleep(args.poll_interval)

    for batch_id, active_batch in list(active.items()):
        if active_batch.process.poll() is None:
            try:
                os.killpg(active_batch.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            active_batch.log_file.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
