#!/usr/bin/env python3
"""Fetch live Harbor job stats for a run across its assigned workers.

Reads batch/worker metadata from the controller SQLite store, SSHes to each
worker, and aggregates progress / exception / score counts from on-disk
Harbor job directories.

When a run has Exception Rerun batches, the script also reads those rerun job
dirs and produces an *effective* merged view: rerun results override the
matching cases in the parent (primary) batch.

Example:
  cd /root/projects/agent-eval-orchestrator
  python3 scripts/aeo-run-stats.py run-0e7535baa043
  python3 scripts/aeo-run-stats.py run-e731483ce0b7 --json
  python3 scripts/aeo-run-stats.py run-0e7535baa043 --primary-only
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import textwrap
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHARED_ROOT = REPO_ROOT / "runtime"

ACTIVE_RERUN_STATUSES = {"pending_sync", "queued", "running"}
TERMINAL_RERUN_STATUSES = {"succeeded", "failed", "stopped"}

REMOTE_ANALYZER = textwrap.dedent(
    """
    import json
    from collections import Counter
    from pathlib import Path

    job_dir = Path(__JOB_DIR__)

    def infer_case_id(trial_dir, trial_result):
        if trial_result:
            trial_name = str(trial_result.get("trial_name") or trial_dir.name)
            candidate = trial_name.rsplit("__", 1)[0].strip()
            if candidate:
                return candidate
        candidate = trial_dir.name.rsplit("__", 1)[0].strip()
        return candidate or trial_dir.name

    def classify_trial(trial_result):
        exception_info = trial_result.get("exception_info")
        reward = ((trial_result.get("verifier_result") or {}).get("rewards") or {}).get("reward")
        if exception_info:
            return {"status": "exception", "scored": None, "has_result": True}
        if reward is None:
            return {"status": "finished_no_reward", "scored": None, "has_result": True}
        if float(reward) >= 1.0:
            return {"status": "passed", "scored": True, "has_result": True}
        return {"status": "failed", "scored": False, "has_result": True}

    summary = {}
    result_path = job_dir / "result.json"
    if result_path.exists():
        try:
            summary = json.loads(result_path.read_text(encoding="utf-8"))
        except Exception:
            summary = {}

    stats = summary.get("stats") or {}
    total = int(summary.get("n_total_trials") or 0)
    completed = int(stats.get("n_completed_trials") or 0)
    errored = int(stats.get("n_errored_trials") or 0)
    running = int(stats.get("n_running_trials") or 0)
    pending = int(stats.get("n_pending_trials") or 0)
    cancelled = int(stats.get("n_cancelled_trials") or 0)
    retries = int(stats.get("n_retries") or 0)

    status_counts: Counter[str] = Counter()
    score_counts: Counter[str] = Counter()
    cases = {}
    if job_dir.exists():
        for trial_dir in sorted(job_dir.iterdir()):
            if not trial_dir.is_dir():
                continue
            trial_result_path = trial_dir / "result.json"
            if not trial_result_path.exists():
                case_id = infer_case_id(trial_dir, None)
                entry = {"status": "in_progress", "scored": None, "has_result": False}
                cases[case_id] = entry
                status_counts["in_progress_or_pending"] += 1
                continue
            try:
                trial_result = json.loads(trial_result_path.read_text(encoding="utf-8"))
            except Exception:
                status_counts["bad_result_json"] += 1
                continue
            case_id = infer_case_id(trial_dir, trial_result)
            entry = classify_trial(trial_result)
            cases[case_id] = entry
            if entry["status"] == "exception":
                status_counts["exception"] += 1
            elif entry["status"] == "finished_no_reward":
                status_counts["finished_no_reward"] += 1
            elif entry["status"] == "passed":
                status_counts["passed"] += 1
                score_counts["scored"] += 1
            elif entry["status"] == "failed":
                status_counts["failed"] += 1
                score_counts["not_scored"] += 1

    payload = {
        "job_dir": str(job_dir),
        "job_started_at": summary.get("started_at"),
        "job_updated_at": summary.get("updated_at"),
        "total": total,
        "completed": completed,
        "errored": errored,
        "running": running,
        "pending": pending,
        "cancelled": cancelled,
        "retries": retries,
        "started_count": running + completed + errored,
        "not_started": pending,
        "trial_dirs": sum(1 for path in job_dir.iterdir() if path.is_dir()) if job_dir.exists() else 0,
        "trials_with_result_json": sum(
            1 for path in job_dir.iterdir() if path.is_dir() and (path / "result.json").exists()
        )
        if job_dir.exists()
        else 0,
        "status_counts": dict(status_counts),
        "score_counts": dict(score_counts),
        "cases": cases,
    }
    print(json.dumps(payload, ensure_ascii=False))
    """
).strip()


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def sanitize_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value.strip()) or "default"


def parse_batch_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["selected_case_ids"] = json.loads(item.pop("selected_case_ids_json"))
    item["batch_options"] = json.loads(item.pop("batch_options_json"))
    item["summary"] = json.loads(item.pop("summary_json"))
    item["artifact_index"] = json.loads(item.pop("artifact_index_json"))
    if not item.get("batch_kind"):
        item["batch_kind"] = "primary"
    return item


def pick_rerun_batch(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    ordered = sorted(rows, key=lambda item: str(item.get("created_at") or ""), reverse=True)
    for item in ordered:
        if str(item.get("status") or "") in ACTIVE_RERUN_STATUSES:
            return item
    for item in ordered:
        if str(item.get("status") or "") == "succeeded":
            return item
    for item in ordered:
        if str(item.get("status") or "") not in {"sync_failed"}:
            return item
    return ordered[0]


def fetch_controller_task(controller_url: str, auth_token: str, run_id: str) -> dict[str, Any] | None:
    url = f"{controller_url.rstrip('/')}/api/dashboard/tasks"
    headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    for item in payload.get("items") or []:
        if str(item.get("runId") or "") == run_id:
            return item
    return None


def load_run_plan(db_path: Path, run_id: str) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        run_row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if not run_row:
            raise SystemExit(f"run not found in store: {run_id}")
        run_item = dict(run_row)

        rerun_job = None
        rerun_job_id = str(run_item.get("rerun_job_id") or "").strip()
        if rerun_job_id:
            job_row = conn.execute(
                "SELECT * FROM run_rerun_jobs WHERE job_id = ?",
                (rerun_job_id,),
            ).fetchone()
            if job_row:
                rerun_job = dict(job_row)
                rerun_job["rerun_batches"] = json.loads(rerun_job.pop("rerun_batches_json"))

        workers = {
            str(row["worker_id"]): dict(row)
            for row in conn.execute("SELECT * FROM workers").fetchall()
        }
        for worker in workers.values():
            worker["capabilities"] = json.loads(worker.pop("capabilities_json"))
            worker["tags"] = json.loads(worker.pop("tags_json"))

        primary_rows = conn.execute(
            """
            SELECT *
            FROM batches
            WHERE run_id = ?
              AND COALESCE(batch_kind, 'primary') = 'primary'
            ORDER BY created_at ASC
            """,
            (run_id,),
        ).fetchall()
        if not primary_rows:
            raise SystemExit(f"no primary batches found for run: {run_id}")

        rerun_rows = conn.execute(
            """
            SELECT *
            FROM batches
            WHERE run_id = ?
              AND batch_kind = 'exception_rerun'
            ORDER BY created_at ASC
            """,
            (run_id,),
        ).fetchall()
        reruns_by_parent: dict[str, list[dict[str, Any]]] = {}
        for row in rerun_rows:
            batch_item = parse_batch_row(row)
            parent_batch_id = str(batch_item.get("parent_batch_id") or "").strip()
            if not parent_batch_id:
                continue
            reruns_by_parent.setdefault(parent_batch_id, []).append(batch_item)
    finally:
        conn.close()

    primary_batches: list[dict[str, Any]] = []
    for row in primary_rows:
        batch_item = parse_batch_row(row)
        worker_id = str(
            batch_item.get("assigned_worker_id") or batch_item.get("preferred_worker_id") or ""
        ).strip()
        worker = workers.get(worker_id)
        if not worker:
            raise SystemExit(f"worker not registered: {worker_id}")
        rerun_batch = pick_rerun_batch(reruns_by_parent.get(str(batch_item["batch_id"]), []))
        primary_batches.append(
            {
                "batch_id": str(batch_item["batch_id"]),
                "owner": str(batch_item["owner"]),
                "batch_status": str(batch_item.get("status") or ""),
                "worker_id": worker_id,
                "worker": worker,
                "expected_case_ids": list(batch_item["selected_case_ids"]),
                "expected_cases": len(batch_item["selected_case_ids"]),
                "rerun_batch": rerun_batch,
            }
        )

    return {
        "run": run_item,
        "rerun_job": rerun_job,
        "primary_batches": primary_batches,
    }


def worker_shared_root(worker: dict[str, Any]) -> str:
    capabilities = worker.get("capabilities") if isinstance(worker.get("capabilities"), dict) else {}
    shared_root = str(capabilities.get("sharedRoot") or "").strip()
    return shared_root or "/home/djn/worker/agent-eval-orchestrator/runtime"


def remote_job_dir(*, shared_root: str, owner: str, run_id: str, batch_id: str) -> str:
    owner_dir = sanitize_name(owner)
    run_dir = sanitize_name(run_id)
    batch_dir = sanitize_name(batch_id)
    return f"{shared_root.rstrip('/')}/archives/{owner_dir}/runs/{run_dir}/batches/{batch_dir}/harbor/jobs/{batch_dir}"


def build_ssh_target(
    worker: dict[str, Any],
    *,
    ssh_config: Path | None,
    ssh_user: str,
    ssh_key: Path | None,
) -> tuple[list[str], str]:
    alias = str(worker.get("ssh_host_alias") or "").strip()
    if alias and ssh_config and ssh_config.exists():
        return ["-F", str(ssh_config), alias], alias

    host = str(worker.get("host") or worker.get("worker_id") or "").strip()
    if not host:
        raise RuntimeError("worker has no ssh_host_alias or host")
    target = f"{ssh_user}@{host}"
    prefix: list[str] = []
    if ssh_key and ssh_key.exists():
        prefix.extend(["-i", str(ssh_key)])
    return prefix, target


def ssh_analyze_job(
    *,
    worker: dict[str, Any],
    job_dir: str,
    ssh_config: Path | None,
    ssh_user: str,
    ssh_key: Path | None,
    connect_timeout_sec: int,
) -> dict[str, Any]:
    ssh_prefix, target = build_ssh_target(worker, ssh_config=ssh_config, ssh_user=ssh_user, ssh_key=ssh_key)
    analyzer = REMOTE_ANALYZER.replace("__JOB_DIR__", json.dumps(job_dir))
    remote_cmd = f"python3 - <<'EOF'\n{analyzer}\nEOF"
    cmd = [
        "ssh",
        *ssh_prefix,
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={connect_timeout_sec}",
        "-o",
        "StrictHostKeyChecking=no",
        target,
        remote_cmd,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "ssh failed")
    return json.loads(result.stdout.strip())


def summarize_case_map(expected_case_ids: list[str], case_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    status_counts: Counter[str] = Counter()
    score_counts: Counter[str] = Counter()
    for case_id in expected_case_ids:
        entry = case_map.get(case_id) or {"status": "pending", "scored": None, "has_result": False}
        status = str(entry.get("status") or "pending")
        if status == "in_progress":
            status_counts["in_progress_or_pending"] += 1
        else:
            status_counts[status] += 1
        scored = entry.get("scored")
        if scored is True:
            score_counts["scored"] += 1
        elif scored is False:
            score_counts["not_scored"] += 1

    total = len(expected_case_ids)
    completed = sum(
        status_counts[key]
        for key in ("passed", "failed", "exception", "finished_no_reward")
    )
    running = sum(1 for case_id in expected_case_ids if case_map.get(case_id, {}).get("status") == "in_progress")
    pending = status_counts.get("pending", 0)
    finished_with_result = int(status_counts.get("passed", 0) + status_counts.get("failed", 0))
    pass_rate = None
    if finished_with_result:
        pass_rate = round(status_counts.get("passed", 0) / finished_with_result * 100, 1)

    return {
        "total": total,
        "completed": completed,
        "errored": int(status_counts.get("exception", 0)),
        "running": running,
        "pending": pending,
        "started_count": total - pending,
        "not_started": pending,
        "status_counts": dict(status_counts),
        "score_counts": dict(score_counts),
        "exception_cases": int(status_counts.get("exception", 0)),
        "pass_rate_pct": pass_rate,
    }


def merge_effective_cases(
    *,
    expected_case_ids: list[str],
    primary_cases: dict[str, dict[str, Any]],
    rerun_cases: dict[str, dict[str, Any]] | None,
    rerun_case_ids: list[str],
) -> dict[str, dict[str, Any]]:
    rerun_ids = set(rerun_case_ids)
    merged: dict[str, dict[str, Any]] = {}
    rerun_cases = rerun_cases or {}
    for case_id in expected_case_ids:
        if case_id in rerun_ids:
            if case_id in rerun_cases:
                merged[case_id] = {**rerun_cases[case_id], "source": "exception_rerun"}
            elif case_id in primary_cases:
                merged[case_id] = {**primary_cases[case_id], "source": "primary_until_rerun"}
            else:
                merged[case_id] = {
                    "status": "pending",
                    "scored": None,
                    "has_result": False,
                    "source": "exception_rerun",
                }
        elif case_id in primary_cases:
            merged[case_id] = {**primary_cases[case_id], "source": "primary"}
        else:
            merged[case_id] = {
                "status": "pending",
                "scored": None,
                "has_result": False,
                "source": "primary",
            }
    return merged


def aggregate_worker_rows(rows: list[dict[str, Any]], *, key: str = "stats") -> dict[str, Any]:
    totals = Counter()
    status_counts: Counter[str] = Counter()
    score_counts: Counter[str] = Counter()
    for row in rows:
        stats = row.get(key) or {}
        for metric in (
            "total",
            "completed",
            "errored",
            "running",
            "pending",
            "started_count",
            "not_started",
            "trials_with_result_json",
            "retries",
        ):
            totals[metric] += int(stats.get(metric) or 0)
        status_counts.update(stats.get("status_counts") or {})
        score_counts.update(stats.get("score_counts") or {})

    finished_with_result = int(status_counts.get("passed", 0) + status_counts.get("failed", 0))
    pass_rate = None
    if finished_with_result:
        pass_rate = round(status_counts.get("passed", 0) / finished_with_result * 100, 1)

    return {
        **dict(totals),
        "status_counts": dict(status_counts),
        "score_counts": dict(score_counts),
        "exception_cases": int(status_counts.get("exception", 0)),
        "pass_rate_pct": pass_rate,
    }


def collect_run_stats(
    *,
    run_id: str,
    db_path: Path,
    ssh_config: Path | None,
    ssh_user: str,
    ssh_key: Path | None,
    connect_timeout_sec: int,
    controller_url: str | None,
    auth_token: str,
    primary_only: bool = False,
) -> dict[str, Any]:
    plan = load_run_plan(db_path, run_id)
    worker_rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for item in plan["primary_batches"]:
        worker = item["worker"]
        shared_root = worker_shared_root(worker)
        primary_job_dir = remote_job_dir(
            shared_root=shared_root,
            owner=item["owner"],
            run_id=run_id,
            batch_id=item["batch_id"],
        )
        try:
            primary_stats = ssh_analyze_job(
                worker=worker,
                job_dir=primary_job_dir,
                ssh_config=ssh_config,
                ssh_user=ssh_user,
                ssh_key=ssh_key,
                connect_timeout_sec=connect_timeout_sec,
            )
        except Exception as exc:
            errors.append(
                {
                    "worker_id": item["worker_id"],
                    "batch_id": item["batch_id"],
                    "error": str(exc),
                }
            )
            continue

        rerun_info = None
        rerun_stats = None
        rerun_batch = item.get("rerun_batch")
        if rerun_batch and not primary_only:
            rerun_batch_id = str(rerun_batch["batch_id"])
            rerun_job_dir = remote_job_dir(
                shared_root=shared_root,
                owner=item["owner"],
                run_id=run_id,
                batch_id=rerun_batch_id,
            )
            try:
                rerun_stats = ssh_analyze_job(
                    worker=worker,
                    job_dir=rerun_job_dir,
                    ssh_config=ssh_config,
                    ssh_user=ssh_user,
                    ssh_key=ssh_key,
                    connect_timeout_sec=connect_timeout_sec,
                )
                rerun_info = {
                    "batch_id": rerun_batch_id,
                    "parent_batch_id": item["batch_id"],
                    "batch_status": str(rerun_batch.get("status") or ""),
                    "rerun_case_ids": list(rerun_batch["selected_case_ids"]),
                    "rerun_case_count": len(rerun_batch["selected_case_ids"]),
                    "stats": rerun_stats,
                }
            except Exception as exc:
                errors.append(
                    {
                        "worker_id": item["worker_id"],
                        "batch_id": rerun_batch_id,
                        "phase": "exception_rerun",
                        "error": str(exc),
                    }
                )

        effective_cases = merge_effective_cases(
            expected_case_ids=item["expected_case_ids"],
            primary_cases=primary_stats.get("cases") or {},
            rerun_cases=(rerun_stats or {}).get("cases") if rerun_stats else None,
            rerun_case_ids=list((rerun_batch or {}).get("selected_case_ids") or []),
        )
        effective_stats = summarize_case_map(item["expected_case_ids"], effective_cases)
        chosen_stats = primary_stats if primary_only else effective_stats

        worker_rows.append(
            {
                "worker_id": item["worker_id"],
                "primary_batch_id": item["batch_id"],
                "batch_id": item["batch_id"],
                "expected_cases": item["expected_cases"],
                "primary": primary_stats,
                "exception_rerun": rerun_info,
                "effective": effective_stats,
                "stats": chosen_stats,
                **chosen_stats,
            }
        )

    overall_primary = aggregate_worker_rows(worker_rows, key="primary")
    overall_effective = aggregate_worker_rows(worker_rows, key="effective") if not primary_only else overall_primary
    rerun_rows = [row["exception_rerun"]["stats"] for row in worker_rows if row.get("exception_rerun")]
    overall_rerun = aggregate_worker_rows(
        [{"stats": stats} for stats in rerun_rows],
        key="stats",
    ) if rerun_rows else None

    controller_task = None
    if controller_url:
        controller_task = fetch_controller_task(controller_url, auth_token, run_id)

    rerun_status = str(plan["run"].get("rerun_status") or "idle")
    has_rerun = any(row.get("exception_rerun") for row in worker_rows)

    return {
        "run_id": run_id,
        "queried_at": datetime.now(timezone.utc).isoformat(),
        "primary_only": primary_only,
        "rerun": {
            "status": rerun_status,
            "job_id": plan["run"].get("rerun_job_id"),
            "active": rerun_status in {"syncing", "running"} or any(
                str((row.get("exception_rerun") or {}).get("batch_status") or "") in ACTIVE_RERUN_STATUSES
                for row in worker_rows
            ),
            "has_batches": has_rerun,
            "job": plan.get("rerun_job"),
        },
        "controller": controller_task,
        "workers": worker_rows,
        "overall": overall_effective,
        "overall_primary": overall_primary,
        "overall_exception_rerun": overall_rerun,
        "errors": errors,
    }


def _print_stats_block(title: str, stats: dict[str, Any]) -> None:
    if not stats:
        return
    total = int(stats.get("total") or 0)
    completed = int(stats.get("completed") or 0)
    running = int(stats.get("running") or 0)
    pending = int(stats.get("pending") or 0)
    pct = round(completed / total * 100, 1) if total else 0.0
    print(f"\n{title}:")
    print(f"  total={total} completed={completed} ({pct}%) running={running} pending={pending}")
    print(
        "  exception="
        f"{stats.get('exception_cases', 0)} "
        f"scored={stats.get('score_counts', {}).get('scored', 0)} "
        f"not_scored={stats.get('score_counts', {}).get('not_scored', 0)}"
    )
    if stats.get("pass_rate_pct") is not None:
        print(f"  pass_rate={stats['pass_rate_pct']}% (among finished pass/fail cases)")


def print_human_report(payload: dict[str, Any]) -> None:
    print(f"Run: {payload['run_id']}")
    print(f"Queried at (UTC): {payload['queried_at']}")

    rerun = payload.get("rerun") or {}
    if rerun.get("has_batches") or rerun.get("status") not in {"", "idle", None}:
        print(
            "Exception Rerun: "
            f"status={rerun.get('status')} "
            f"active={rerun.get('active')} "
            f"job_id={rerun.get('job_id') or '-'}"
        )

    controller = payload.get("controller") or {}
    if controller:
        print(
            "Controller: "
            f"status={controller.get('status')} "
            f"caseTotal={controller.get('caseTotal')} "
            f"succeeded={controller.get('caseSucceeded')} "
            f"failed={controller.get('caseFailed')} "
            f"errored={controller.get('caseErrored')}"
        )

    if payload.get("errors"):
        print("\nErrors:")
        for item in payload["errors"]:
            phase = f" phase={item['phase']}" if item.get("phase") else ""
            print(f"  - worker={item['worker_id']} batch={item['batch_id']}{phase}: {item['error']}")

    if payload.get("primary_only"):
        _print_stats_block("Overall (primary only)", payload.get("overall") or {})
    else:
        _print_stats_block("Overall (effective, primary + exception rerun merge)", payload.get("overall") or {})
        if payload.get("overall_primary"):
            _print_stats_block("Overall (primary job only)", payload["overall_primary"])
        if payload.get("overall_exception_rerun"):
            _print_stats_block("Overall (exception rerun jobs only)", payload["overall_exception_rerun"])

    print("\nWorkers:")
    for row in payload.get("workers") or []:
        stats = row.get("stats") or {}
        scored = (stats.get("score_counts") or {}).get("scored", 0)
        not_scored = (stats.get("score_counts") or {}).get("not_scored", 0)
        print(
            f"  - {row['worker_id']} primary={row['primary_batch_id']}: "
            f"completed={stats.get('completed', 0)} running={stats.get('running', 0)} "
            f"pending={stats.get('pending', 0)} errored={stats.get('errored', 0)} "
            f"scored={scored} not_scored={not_scored}"
        )
        rerun_info = row.get("exception_rerun")
        if rerun_info:
            rerun_stats = rerun_info.get("stats") or {}
            print(
                f"      rerun batch={rerun_info['batch_id']} status={rerun_info['batch_status']} "
                f"cases={rerun_info['rerun_case_count']} "
                f"completed={rerun_stats.get('completed', 0)} "
                f"running={rerun_stats.get('running', 0)} "
                f"exception={rerun_stats.get('exception_cases', 0)}"
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id", help="Run ID, e.g. run-0e7535baa043")
    parser.add_argument(
        "--shared-root",
        default=os.environ.get("AEO_SHARED_ROOT", str(DEFAULT_SHARED_ROOT)),
        help="Controller shared root containing controller/state.sqlite3",
    )
    parser.add_argument(
        "--db-path",
        default="",
        help="Override SQLite path (default: <shared-root>/controller/state.sqlite3)",
    )
    parser.add_argument(
        "--ssh-config",
        default=os.environ.get("AEO_SSH_CONFIG", str(Path.home() / ".ssh" / "config")),
        help="SSH config path; used when worker has ssh_host_alias",
    )
    parser.add_argument(
        "--ssh-user",
        default=os.environ.get("AEO_SSH_USER", "djn"),
        help="SSH user for direct host connections",
    )
    parser.add_argument(
        "--ssh-key",
        default=os.environ.get("AEO_SSH_KEY", str(Path.home() / ".ssh" / "KeyPair.pem")),
        help="SSH private key for direct host connections",
    )
    parser.add_argument(
        "--connect-timeout",
        type=int,
        default=int(os.environ.get("AEO_SSH_CONNECT_TIMEOUT", "10")),
        help="SSH connect timeout in seconds",
    )
    parser.add_argument(
        "--controller-url",
        default=os.environ.get(
            "AEO_CONTROLLER_URL",
            f"http://{os.environ.get('AEO_HOST', '127.0.0.1')}:{os.environ.get('AEO_PORT', '7380')}",
        ),
        help="Controller base URL for optional dashboard snapshot",
    )
    parser.add_argument(
        "--auth-token",
        default=os.environ.get("AEO_AUTH_TOKEN", ""),
        help="Controller auth token",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON instead of human-readable text")
    parser.add_argument(
        "--no-controller",
        action="store_true",
        help="Skip querying controller dashboard API",
    )
    parser.add_argument(
        "--primary-only",
        action="store_true",
        help="Ignore exception rerun batches and report primary Harbor jobs only",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    args = parse_args()

    shared_root = Path(args.shared_root).expanduser().resolve()
    db_path = Path(args.db_path).expanduser().resolve() if args.db_path else shared_root / "controller" / "state.sqlite3"
    ssh_config = Path(args.ssh_config).expanduser()
    ssh_key = Path(args.ssh_key).expanduser()
    if not ssh_config.exists():
        ssh_config = None
    if not ssh_key.exists():
        ssh_key = None

    payload = collect_run_stats(
        run_id=args.run_id,
        db_path=db_path,
        ssh_config=ssh_config,
        ssh_user=args.ssh_user,
        ssh_key=ssh_key,
        connect_timeout_sec=args.connect_timeout,
        controller_url=None if args.no_controller else args.controller_url,
        auth_token=args.auth_token,
        primary_only=args.primary_only,
    )

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print_human_report(payload)

    return 1 if payload.get("errors") and not payload.get("workers") else 0


if __name__ == "__main__":
    raise SystemExit(main())
