from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from agent_eval_orchestrator.core.defaults import (
    DEFAULT_HEARTBEAT_TIMEOUT_SEC,
    DEFAULT_PER_WORKER_CONCURRENCY,
    DEFAULT_PRESET_DATASETS,
)
from agent_eval_orchestrator.core.ids import new_id, now_iso, sanitize_name
from agent_eval_orchestrator.storage.layout import Layout


class Store:
    def __init__(self, layout: Layout) -> None:
        self.layout = layout
        self.layout.ensure_dirs()
        self._init_schema()

    @staticmethod
    def _case_is_errored(case: dict[str, Any]) -> bool:
        status = str(case.get("status") or "")
        if status == "errored":
            return True
        return status == "failed" and bool(case.get("error_text"))

    @staticmethod
    def _case_is_failed(case: dict[str, Any]) -> bool:
        return str(case.get("status") or "") == "failed" and not case.get("error_text")

    @staticmethod
    def case_error_type(case: dict[str, Any]) -> str:
        metrics = case.get("metrics") or {}
        raw = case.get("errorType") or metrics.get("errorType")
        if raw is None or str(raw).strip() == "":
            return "(unknown)"
        return str(raw).strip()

    @staticmethod
    def _trial_merge_key(case: dict[str, Any]) -> str:
        artifact = case.get("artifact_index") or case.get("artifactIndex") or {}
        trial_dir = str(artifact.get("trialDir") or "").strip()
        if trial_dir:
            return Path(trial_dir).name
        trial_name = str(case.get("trialName") or case.get("trial_name") or "").strip()
        if trial_name:
            return trial_name
        return str(case.get("case_id") or case.get("caseId") or "").strip()

    @contextmanager
    def connect(self) -> Any:
        conn = sqlite3.connect(self.layout.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS task_templates (
                    template_id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    name TEXT NOT NULL,
                    dataset_ref TEXT NOT NULL,
                    executor_kind TEXT NOT NULL,
                    executor_config_json TEXT NOT NULL,
                    model_profile_ref TEXT,
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    template_id TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    bound_worker_id TEXT,
                    latest_batch_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS batches (
                    batch_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_step TEXT,
                    preferred_worker_id TEXT,
                    assigned_worker_id TEXT,
                    executor_kind TEXT NOT NULL,
                    executor_metadata_json TEXT NOT NULL,
                    selected_case_ids_json TEXT NOT NULL,
                    batch_options_json TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    artifact_index_json TEXT NOT NULL,
                    batch_root TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    error_text TEXT
                );

                CREATE TABLE IF NOT EXISTS case_runs (
                    case_run_id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    score REAL,
                    metrics_json TEXT NOT NULL,
                    artifact_index_json TEXT NOT NULL,
                    error_text TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS workers (
                    worker_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    host TEXT NOT NULL,
                    slots_total INTEGER NOT NULL,
                    slots_used INTEGER NOT NULL,
                    capabilities_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    note TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    allocation_weight REAL NOT NULL DEFAULT 1.0,
                    last_heartbeat_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
        self._ensure_schema_migrations()

    def _ensure_schema_migrations(self) -> None:
        with self.connect() as conn:
            worker_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(workers)").fetchall()
            }
            if "enabled" not in worker_columns:
                conn.execute(
                    "ALTER TABLE workers ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1"
                )
            if "note" not in worker_columns:
                conn.execute(
                    "ALTER TABLE workers ADD COLUMN note TEXT NOT NULL DEFAULT ''"
                )
            if "tags_json" not in worker_columns:
                conn.execute(
                    "ALTER TABLE workers ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]'"
                )
            if "allocation_weight" not in worker_columns:
                conn.execute(
                    "ALTER TABLE workers ADD COLUMN allocation_weight REAL NOT NULL DEFAULT 1.0"
                )
            provision_columns = {
                "ssh_host_alias": "TEXT NOT NULL DEFAULT ''",
                "ssh_bootstrap_host_alias": "TEXT",
                "tunnel_remote_port": "INTEGER NOT NULL DEFAULT 17380",
                "provision_status": "TEXT NOT NULL DEFAULT 'none'",
                "last_provision_error": "TEXT",
            }
            for column, ddl in provision_columns.items():
                if column not in worker_columns:
                    conn.execute(f"ALTER TABLE workers ADD COLUMN {column} {ddl}")

            if "connection_mode" not in worker_columns:
                conn.execute(
                    "ALTER TABLE workers ADD COLUMN connection_mode TEXT NOT NULL DEFAULT 'direct'"
                )
                conn.execute(
                    "ALTER TABLE workers ADD COLUMN controller_internal_ip TEXT"
                )
                conn.execute(
                    """
                    UPDATE workers
                    SET connection_mode = 'tunnel'
                    WHERE tunnel_remote_port IS NOT NULL
                      AND ssh_host_alias != ''
                      AND connection_mode = 'direct'
                    """
                )
                self._make_tunnel_remote_port_nullable(conn)

            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS provision_jobs (
                    job_id TEXT PRIMARY KEY,
                    worker_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_step TEXT,
                    steps_json TEXT NOT NULL,
                    log_text TEXT NOT NULL DEFAULT '',
                    error_text TEXT,
                    created_at TEXT NOT NULL,
                    finished_at TEXT
                );

                CREATE TABLE IF NOT EXISTS worker_update_jobs (
                    job_id       TEXT PRIMARY KEY,
                    worker_id    TEXT NOT NULL,
                    targets_json TEXT NOT NULL,
                    status       TEXT NOT NULL,
                    current_step TEXT,
                    steps_json   TEXT NOT NULL,
                    log_text     TEXT NOT NULL DEFAULT '',
                    error_text   TEXT,
                    created_at   TEXT NOT NULL,
                    finished_at  TEXT
                );
                """
            )

            run_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(runs)").fetchall()
            }
            if "parent_run_id" not in run_columns:
                conn.execute("ALTER TABLE runs ADD COLUMN parent_run_id TEXT")
            for column, ddl in {
                "sync_status": "TEXT NOT NULL DEFAULT ''",
                "sync_job_id": "TEXT",
                "sync_manifest_json": "TEXT NOT NULL DEFAULT '{}'",
            }.items():
                if column not in run_columns:
                    conn.execute(f"ALTER TABLE runs ADD COLUMN {column} {ddl}")

            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS asset_sync_jobs (
                    job_id       TEXT PRIMARY KEY,
                    run_id       TEXT NOT NULL,
                    status       TEXT NOT NULL,
                    current_step TEXT,
                    steps_json   TEXT NOT NULL,
                    log_text     TEXT NOT NULL DEFAULT '',
                    error_text   TEXT,
                    created_at   TEXT NOT NULL,
                    finished_at  TEXT
                );
                """
            )

            rerun_run_columns = {
                "rerun_status": "TEXT NOT NULL DEFAULT 'idle'",
                "rerun_job_id": "TEXT",
            }
            for column, ddl in rerun_run_columns.items():
                if column not in run_columns:
                    conn.execute(f"ALTER TABLE runs ADD COLUMN {column} {ddl}")

            batch_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(batches)").fetchall()
            }
            for column, ddl in {
                "parent_batch_id": "TEXT",
                "batch_kind": "TEXT NOT NULL DEFAULT 'primary'",
            }.items():
                if column not in batch_columns:
                    conn.execute(f"ALTER TABLE batches ADD COLUMN {column} {ddl}")

            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS run_rerun_jobs (
                    job_id              TEXT PRIMARY KEY,
                    run_id              TEXT NOT NULL,
                    status              TEXT NOT NULL,
                    sync_job_id         TEXT,
                    case_ids_json       TEXT NOT NULL,
                    worker_shards_json  TEXT NOT NULL,
                    rerun_batches_json  TEXT NOT NULL,
                    error_text          TEXT,
                    created_at          TEXT NOT NULL,
                    finished_at         TEXT
                );
                """
            )
            rerun_job_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(run_rerun_jobs)").fetchall()
            }
            if "selected_error_types_json" not in rerun_job_columns:
                conn.execute(
                    "ALTER TABLE run_rerun_jobs ADD COLUMN selected_error_types_json TEXT"
                )
            self._drop_case_details_column_if_present(conn)

    def _make_tunnel_remote_port_nullable(self, conn: sqlite3.Connection) -> None:
        tunnel_info = next(
            (
                row
                for row in conn.execute("PRAGMA table_info(workers)").fetchall()
                if str(row[1]) == "tunnel_remote_port"
            ),
            None,
        )
        if tunnel_info is None or not tunnel_info[3]:
            return
        conn.executescript(
            """
            CREATE TABLE workers_new (
                worker_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                host TEXT NOT NULL,
                slots_total INTEGER NOT NULL,
                slots_used INTEGER NOT NULL,
                capabilities_json TEXT NOT NULL,
                status TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                note TEXT NOT NULL DEFAULT '',
                tags_json TEXT NOT NULL DEFAULT '[]',
                allocation_weight REAL NOT NULL DEFAULT 1.0,
                last_heartbeat_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                ssh_host_alias TEXT NOT NULL DEFAULT '',
                ssh_bootstrap_host_alias TEXT,
                tunnel_remote_port INTEGER,
                provision_status TEXT NOT NULL DEFAULT 'none',
                last_provision_error TEXT,
                connection_mode TEXT NOT NULL DEFAULT 'direct',
                controller_internal_ip TEXT
            );
            INSERT INTO workers_new(
                worker_id, display_name, host, slots_total, slots_used,
                capabilities_json, status, enabled, note, tags_json,
                allocation_weight, last_heartbeat_at, created_at, updated_at,
                ssh_host_alias, ssh_bootstrap_host_alias, tunnel_remote_port,
                provision_status, last_provision_error, connection_mode,
                controller_internal_ip
            )
            SELECT
                worker_id, display_name, host, slots_total, slots_used,
                capabilities_json, status, enabled, note, tags_json,
                allocation_weight, last_heartbeat_at, created_at, updated_at,
                ssh_host_alias, ssh_bootstrap_host_alias,
                CASE WHEN connection_mode = 'direct' THEN NULL ELSE tunnel_remote_port END,
                provision_status, last_provision_error, connection_mode,
                controller_internal_ip
            FROM workers;
            DROP TABLE workers;
            ALTER TABLE workers_new RENAME TO workers;
            """
        )

    def _drop_case_details_column_if_present(self, conn: sqlite3.Connection) -> None:
        case_columns = [
            str(row[1]) for row in conn.execute("PRAGMA table_info(case_runs)").fetchall()
        ]
        if "details_json" not in case_columns:
            return
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS case_runs_new (
                case_run_id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL,
                case_id TEXT NOT NULL,
                status TEXT NOT NULL,
                score REAL,
                metrics_json TEXT NOT NULL,
                artifact_index_json TEXT NOT NULL,
                error_text TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO case_runs_new(
                case_run_id, batch_id, case_id, status, score, metrics_json,
                artifact_index_json, error_text, created_at, updated_at
            )
            SELECT
                case_run_id, batch_id, case_id, status, score, metrics_json,
                artifact_index_json, error_text, created_at, updated_at
            FROM case_runs;
            DROP TABLE case_runs;
            ALTER TABLE case_runs_new RENAME TO case_runs;
            """
        )

    def create_task_template(
        self,
        *,
        owner: str,
        name: str,
        dataset_ref: str,
        executor_kind: str,
        executor_config: dict[str, Any],
        model_profile_ref: str | None,
        note: str,
    ) -> dict[str, Any]:
        template_id = new_id("tpl")
        now = now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO task_templates(
                    template_id, owner, name, dataset_ref, executor_kind,
                    executor_config_json, model_profile_ref, note, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    template_id,
                    owner,
                    name,
                    dataset_ref,
                    executor_kind,
                    json.dumps(executor_config, ensure_ascii=False),
                    model_profile_ref,
                    note,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM task_templates WHERE template_id = ?",
                (template_id,),
            ).fetchone()
        return self._template_item(row)

    def list_task_templates(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM task_templates ORDER BY created_at ASC, name ASC"
            ).fetchall()
        return [self._template_item(row) for row in rows]

    def get_task_template(self, template_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM task_templates WHERE template_id = ?",
                (template_id,),
            ).fetchone()
        return self._template_item(row) if row else None

    def create_run(
        self,
        *,
        template_id: str,
        display_name: str | None = None,
        parent_run_id: str | None = None,
    ) -> dict[str, Any]:
        template = self.get_task_template(template_id)
        if not template:
            raise RuntimeError("template not found")
        run_id = new_id("run")
        now = now_iso()
        name = display_name or f"{template['name']} {now.replace('T', ' ')[:19]}"
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO runs(
                    run_id, template_id, owner, display_name, bound_worker_id,
                    latest_batch_id, parent_run_id, created_at, updated_at
                ) VALUES(?, ?, ?, ?, NULL, NULL, ?, ?, ?)
                """,
                (run_id, template_id, template["owner"], name, parent_run_id, now, now),
            )
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return self._run_item(row)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return self._run_item(row) if row else None

    def list_runs(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM runs ORDER BY created_at DESC").fetchall()
        return [self._run_item(row) for row in rows]

    def list_active_derived_reruns(self, parent_run_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM runs
                WHERE parent_run_id = ?
                  AND rerun_status IN ('syncing', 'running')
                ORDER BY created_at ASC
                """,
                (parent_run_id,),
            ).fetchall()
        return [self._run_item(row) for row in rows]

    def list_batches_for_run(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM batches WHERE run_id = ? ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
        return [self._batch_item(row) for row in rows]

    def is_run_terminal(self, run_id: str) -> bool:
        batches = self.list_batches_for_run(run_id)
        if not batches:
            return False
        terminal = {"succeeded", "failed", "stopped", "sync_failed"}
        return all(str(batch["status"]) in terminal for batch in batches)

    def list_primary_batches_for_run(self, run_id: str) -> list[dict[str, Any]]:
        return [
            batch
            for batch in self.list_batches_for_run(run_id)
            if str(batch.get("batch_kind") or "primary") == "primary"
        ]

    def is_run_primary_terminal(self, run_id: str) -> bool:
        batches = self.list_primary_batches_for_run(run_id)
        if not batches:
            return False
        terminal = {"succeeded", "failed", "stopped", "sync_failed"}
        return all(str(batch["status"]) in terminal for batch in batches)

    def list_exception_cases_for_run(self, run_id: str) -> list[dict[str, Any]]:
        exceptions: list[dict[str, Any]] = []
        for batch in self.list_primary_batches_for_run(run_id):
            worker_id = str(batch.get("assigned_worker_id") or batch.get("preferred_worker_id") or "").strip()
            for case in self.list_case_runs(str(batch["batch_id"])):
                if not self._case_is_errored(case):
                    continue
                exceptions.append(
                    {
                        "case_id": str(case["case_id"]),
                        "parent_batch_id": str(batch["batch_id"]),
                        "worker_id": worker_id,
                        "case": case,
                    }
                )
        return exceptions

    def summarize_exception_types_for_run(self, run_id: str) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for item in self.list_exception_cases_for_run(run_id):
            error_type = self.case_error_type(dict(item.get("case") or {}))
            counts[error_type] = counts.get(error_type, 0) + 1
        by_type = [
            {"errorType": error_type, "count": count}
            for error_type, count in counts.items()
        ]
        by_type.sort(key=lambda entry: (-entry["count"], entry["errorType"]))
        total = sum(entry["count"] for entry in by_type)
        return {"total": total, "byType": by_type}

    def read_harbor_merged_job_stats(
        self,
        run_id: str,
        merged_jobs_dir: Path,
    ) -> dict[str, Any] | None:
        run = self.get_run(run_id)
        if not run:
            return None
        job_name = sanitize_name(str(run["display_name"]))
        result_path = merged_jobs_dir.expanduser().resolve() / job_name / "result.json"
        if not result_path.exists():
            return None
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
        return {
            "jobName": job_name,
            "erroredTrials": int(stats.get("n_errored_trials") or 0),
            "totalTrials": int(payload.get("n_total_trials") or 0),
        }

    def summarize_exception_display_for_run(
        self,
        run_id: str,
        *,
        merged_jobs_dir: Path | None = None,
    ) -> dict[str, Any]:
        items = self.list_exception_cases_for_run(run_id)
        display: dict[str, Any] = {
            "trialRecordCount": len(items),
            "uniqueCaseCount": len({str(item["case_id"]) for item in items}),
        }
        if merged_jobs_dir is not None:
            merged = self.read_harbor_merged_job_stats(run_id, merged_jobs_dir)
            if merged is not None:
                display["harborMergedJobName"] = merged["jobName"]
                display["harborMergedErroredTrials"] = merged["erroredTrials"]
                display["harborMergedTotalTrials"] = merged["totalTrials"]
        return display

    def filter_exception_cases_by_types(
        self,
        run_id: str,
        selected_error_types: list[str] | None,
    ) -> list[dict[str, Any]]:
        items = self.list_exception_cases_for_run(run_id)
        if selected_error_types is None:
            return items
        selected = set(selected_error_types)
        return [
            item
            for item in items
            if self.case_error_type(dict(item.get("case") or {})) in selected
        ]

    def group_exception_items_by_worker(
        self,
        items: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            worker_id = str(item.get("worker_id") or "").strip()
            if not worker_id:
                continue
            grouped.setdefault(worker_id, []).append(item)
        return grouped

    def group_exception_cases_by_worker(self, run_id: str) -> dict[str, list[dict[str, Any]]]:
        return self.group_exception_items_by_worker(self.list_exception_cases_for_run(run_id))

    @staticmethod
    def _recompute_batch_summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
        succeeded = sum(1 for case in cases if str(case.get("status") or "") == "succeeded")
        failed = sum(1 for case in cases if Store._case_is_failed(case))
        errored = sum(1 for case in cases if Store._case_is_errored(case))
        return {
            "succeeded": succeeded,
            "failed": failed,
            "errored": errored,
            "total": len(cases),
        }

    def merge_rerun_cases_into_parent(
        self,
        *,
        parent_batch_id: str,
        rerun_cases: list[dict[str, Any]],
        rerun_batch_id: str,
    ) -> dict[str, Any] | None:
        now = now_iso()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            parent = conn.execute(
                "SELECT * FROM batches WHERE batch_id = ?",
                (parent_batch_id,),
            ).fetchone()
            if not parent:
                conn.execute("ROLLBACK")
                return None
            rerun_trial_keys = {self._trial_merge_key(case) for case in rerun_cases if self._trial_merge_key(case)}
            existing_rows = conn.execute(
                "SELECT * FROM case_runs WHERE batch_id = ?",
                (parent_batch_id,),
            ).fetchall()
            kept = [
                self._case_item(row)
                for row in existing_rows
                if self._trial_merge_key(self._case_item(row)) not in rerun_trial_keys
            ]
            conn.execute("DELETE FROM case_runs WHERE batch_id = ?", (parent_batch_id,))
            for case in kept:
                conn.execute(
                    """
                    INSERT INTO case_runs(
                        case_run_id, batch_id, case_id, status, score, metrics_json,
                        artifact_index_json, error_text, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        case["case_run_id"],
                        parent_batch_id,
                        case["original_case_id"],
                        case["status"],
                        case.get("score"),
                        json.dumps(case.get("metrics") or {}, ensure_ascii=False),
                        json.dumps(case.get("artifact_index") or {}, ensure_ascii=False),
                        case.get("error_text"),
                        case["created_at"],
                        now,
                    ),
                )
            for case in rerun_cases:
                case_id = str(case["caseId"])
                metrics = dict(case.get("metrics") or {})
                if case.get("errorType"):
                    metrics["errorType"] = case.get("errorType")
                conn.execute(
                    """
                    INSERT INTO case_runs(
                        case_run_id, batch_id, case_id, status, score, metrics_json,
                        artifact_index_json, error_text, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id("case"),
                        parent_batch_id,
                        case_id,
                        str(case.get("status") or "pending"),
                        case.get("score"),
                        json.dumps(metrics, ensure_ascii=False),
                        json.dumps(case.get("artifactIndex") or {}, ensure_ascii=False),
                        case.get("errorText"),
                        now,
                        now,
                    ),
                )
            merged_cases = [
                self._case_item(row)
                for row in conn.execute(
                    "SELECT * FROM case_runs WHERE batch_id = ?",
                    (parent_batch_id,),
                ).fetchall()
            ]
            summary = self._recompute_batch_summary(merged_cases)
            conn.execute(
                "UPDATE batches SET summary_json = ? WHERE batch_id = ?",
                (json.dumps(summary, ensure_ascii=False), parent_batch_id),
            )
            conn.execute("DELETE FROM case_runs WHERE batch_id = ?", (rerun_batch_id,))
            updated = conn.execute(
                "SELECT * FROM batches WHERE batch_id = ?",
                (parent_batch_id,),
            ).fetchone()
        return self._batch_item(updated)

    def finish_rerun_batch_if_complete(self, *, rerun_batch_id: str) -> None:
        batch = self.get_batch(rerun_batch_id)
        if not batch or str(batch.get("batch_kind") or "") != "exception_rerun":
            return
        run_id = str(batch["run_id"])
        run = self.get_run(run_id)
        if not run or not run.get("rerun_job_id"):
            return
        job = self.get_run_rerun_job(str(run["rerun_job_id"]))
        if not job:
            return
        rerun_batch_ids = [str(batch_id) for batch_id in job["rerun_batches"].values()]
        statuses = []
        for batch_id in rerun_batch_ids:
            item = self.get_batch(batch_id)
            statuses.append(str(item["status"]) if item else "missing")
        terminal = {"succeeded", "failed", "stopped", "sync_failed"}
        if not all(status in terminal for status in statuses):
            return
        final_status = "failed" if any(status in {"failed", "sync_failed", "stopped"} for status in statuses) else "succeeded"
        self.update_run_rerun_fields(run_id=run_id, rerun_status=final_status)
        self.update_run_rerun_job(str(job["job_id"]), status=final_status, finished=True)

    def create_batch(
        self,
        *,
        run_id: str,
        selected_case_ids: list[str],
        preferred_worker_id: str | None,
        batch_options: dict[str, Any] | None,
        initial_status: str = "queued",
        batch_kind: str = "primary",
        parent_batch_id: str | None = None,
    ) -> dict[str, Any]:
        run = self.get_run(run_id)
        if not run:
            raise RuntimeError("run not found")
        template = self.get_task_template(run["template_id"])
        if not template:
            raise RuntimeError("template not found")
        batch_id = new_id("batch")
        now = now_iso()
        batch_root = str(self.layout.batch_dir(run["owner"], run_id, batch_id))
        self.layout.batch_dir(run["owner"], run_id, batch_id).mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO batches(
                    batch_id, run_id, owner, status, current_step, preferred_worker_id,
                    assigned_worker_id, executor_kind, executor_metadata_json,
                    selected_case_ids_json, batch_options_json, summary_json,
                    artifact_index_json, batch_root, created_at, started_at, finished_at,
                    error_text, parent_batch_id, batch_kind
                ) VALUES(?, ?, ?, ?, NULL, ?, NULL, ?, '{}', ?, ?, '{}', '{}', ?, ?, NULL, NULL, NULL, ?, ?)
                """,
                (
                    batch_id,
                    run_id,
                    run["owner"],
                    initial_status,
                    preferred_worker_id,
                    template["executor_kind"],
                    json.dumps(selected_case_ids, ensure_ascii=False),
                    json.dumps(batch_options or {}, ensure_ascii=False),
                    batch_root,
                    now,
                    parent_batch_id,
                    batch_kind,
                ),
            )
            conn.execute(
                "UPDATE runs SET latest_batch_id = ?, updated_at = ? WHERE run_id = ?",
                (batch_id, now, run_id),
            )
            row = conn.execute("SELECT * FROM batches WHERE batch_id = ?", (batch_id,)).fetchone()
        return self._batch_item(row)

    def clone_primary_batches_to_run(
        self,
        *,
        source_run_id: str,
        target_run_id: str,
    ) -> dict[str, str]:
        target_run = self.get_run(target_run_id)
        if not target_run:
            raise RuntimeError("target run not found")
        mapping: dict[str, str] = {}
        now = now_iso()
        source_batches = self.list_primary_batches_for_run(source_run_id)
        with self.connect() as conn:
            for source in source_batches:
                new_batch_id = new_id("batch")
                batch_root = str(self.layout.batch_dir(target_run["owner"], target_run_id, new_batch_id))
                self.layout.batch_dir(target_run["owner"], target_run_id, new_batch_id).mkdir(
                    parents=True,
                    exist_ok=True,
                )
                conn.execute(
                    """
                    INSERT INTO batches(
                        batch_id, run_id, owner, status, current_step, preferred_worker_id,
                        assigned_worker_id, executor_kind, executor_metadata_json,
                        selected_case_ids_json, batch_options_json, summary_json,
                        artifact_index_json, batch_root, created_at, started_at, finished_at,
                        error_text, parent_batch_id, batch_kind
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'primary')
                    """,
                    (
                        new_batch_id,
                        target_run_id,
                        target_run["owner"],
                        source["status"],
                        source.get("current_step"),
                        source.get("preferred_worker_id"),
                        source.get("assigned_worker_id"),
                        source["executor_kind"],
                        json.dumps(source.get("executor_metadata") or {}, ensure_ascii=False),
                        json.dumps(source.get("selected_case_ids") or [], ensure_ascii=False),
                        json.dumps(source.get("batch_options") or {}, ensure_ascii=False),
                        json.dumps(source.get("summary") or {}, ensure_ascii=False),
                        json.dumps(source.get("artifact_index") or {}, ensure_ascii=False),
                        batch_root,
                        now,
                        source.get("started_at"),
                        source.get("finished_at"),
                        source.get("error_text"),
                    ),
                )
                for case in self.list_case_runs(str(source["batch_id"])):
                    conn.execute(
                        """
                        INSERT INTO case_runs(
                            case_run_id, batch_id, case_id, status, score, metrics_json,
                            artifact_index_json, error_text, created_at, updated_at
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            new_id("case"),
                            new_batch_id,
                            case["original_case_id"],
                            case["status"],
                            case.get("score"),
                            json.dumps(case.get("metrics") or {}, ensure_ascii=False),
                            json.dumps(case.get("artifact_index") or {}, ensure_ascii=False),
                            case.get("error_text"),
                            case.get("created_at") or now,
                            now,
                        ),
                    )
                mapping[str(source["batch_id"])] = new_batch_id
            if source_batches:
                conn.execute(
                    "UPDATE runs SET latest_batch_id = ?, updated_at = ? WHERE run_id = ?",
                    (list(mapping.values())[-1], now, target_run_id),
                )
        return mapping

    def create_sharded_batches(
        self,
        *,
        run_id: str,
        selected_case_ids: list[str],
        worker_ids: list[str],
        batch_options: dict[str, Any] | None,
        initial_status: str = "queued",
    ) -> list[dict[str, Any]]:
        if not selected_case_ids:
            raise RuntimeError("selected_case_ids must not be empty")
        if not worker_ids:
            raise RuntimeError("worker_ids must not be empty")
        options = dict(batch_options or {})
        max_concurrency = int(options.get("concurrency") or DEFAULT_PER_WORKER_CONCURRENCY)
        case_groups = self._weighted_case_groups(selected_case_ids, worker_ids)
        created: list[dict[str, Any]] = []
        for worker_id, case_ids in zip(worker_ids, case_groups):
            if not case_ids:
                continue
            created.append(
                self.create_batch(
                    run_id=run_id,
                    selected_case_ids=case_ids,
                    preferred_worker_id=worker_id,
                    batch_options={**options, "concurrency": min(max_concurrency, len(case_ids))},
                    initial_status=initial_status,
                )
            )
        return created

    def promote_worker_batches_to_queued(self, *, run_id: str, worker_id: str) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE batches
                SET status = 'queued'
                WHERE run_id = ? AND preferred_worker_id = ? AND status = 'pending_sync'
                """,
                (run_id, worker_id),
            )
        return int(cursor.rowcount)

    def mark_worker_batches_sync_failed(self, *, run_id: str, worker_id: str) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE batches
                SET status = 'sync_failed'
                WHERE run_id = ? AND preferred_worker_id = ? AND status = 'pending_sync'
                """,
                (run_id, worker_id),
            )
        return int(cursor.rowcount)

    def update_task_template_executor_config(
        self,
        template_id: str,
        patch: dict[str, Any],
        *,
        replace_keys: set[str] | None = None,
    ) -> dict[str, Any]:
        now = now_iso()
        replace_keys = replace_keys or set()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT executor_config_json FROM task_templates WHERE template_id = ?",
                (template_id,),
            ).fetchone()
            if not row:
                raise RuntimeError("template not found")
            config = json.loads(row["executor_config_json"])
            for key, value in patch.items():
                if key not in replace_keys and isinstance(value, dict) and isinstance(config.get(key), dict):
                    merged = dict(config[key])
                    merged.update(value)
                    config[key] = merged
                else:
                    config[key] = value
            conn.execute(
                """
                UPDATE task_templates
                SET executor_config_json = ?, updated_at = ?
                WHERE template_id = ?
                """,
                (json.dumps(config, ensure_ascii=False), now, template_id),
            )
        updated = self.get_task_template(template_id)
        if not updated:
            raise RuntimeError("template not found after update")
        return updated

    def update_task_template_dataset_ref(
        self,
        template_id: str,
        dataset_ref: str,
    ) -> dict[str, Any]:
        now = now_iso()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE task_templates
                SET dataset_ref = ?, updated_at = ?
                WHERE template_id = ?
                """,
                (dataset_ref, now, template_id),
            )
            if cursor.rowcount == 0:
                raise RuntimeError("template not found")
        updated = self.get_task_template(template_id)
        if not updated:
            raise RuntimeError("template not found after update")
        return updated

    def _weighted_case_groups(
        self,
        selected_case_ids: list[str],
        worker_ids: list[str],
    ) -> list[list[str]]:
        workers = {item["worker_id"]: item for item in self.list_workers()}
        weights = [
            self._worker_allocation_score(workers.get(worker_id) or {})
            for worker_id in worker_ids
        ]
        if not any(weight > 0 for weight in weights):
            weights = [1.0 for _ in worker_ids]
        total_weight = sum(weights)
        exact_shares = [len(selected_case_ids) * weight / total_weight for weight in weights]
        quotas = [int(share) for share in exact_shares]
        remaining = len(selected_case_ids) - sum(quotas)
        remainders = sorted(
            range(len(worker_ids)),
            key=lambda index: exact_shares[index] - quotas[index],
            reverse=True,
        )
        for index in remainders[:remaining]:
            quotas[index] += 1

        groups: list[list[str]] = [[] for _ in worker_ids]
        smooth_scores = [0.0 for _ in worker_ids]
        for case_id in selected_case_ids:
            candidates = [index for index, quota in enumerate(quotas) if quota > 0]
            if not candidates:
                break
            for index in candidates:
                smooth_scores[index] += weights[index]
            chosen = max(
                candidates,
                key=lambda index: (smooth_scores[index], quotas[index], -index),
            )
            groups[chosen].append(case_id)
            quotas[chosen] -= 1
            smooth_scores[chosen] -= total_weight
        return groups

    @staticmethod
    def _worker_allocation_score(worker: dict[str, Any]) -> float:
        capabilities = dict(worker.get("capabilities") or {})
        slots_total = max(1.0, float(worker.get("slots_total") or capabilities.get("slotsTotal") or 1))
        cpu_count = max(1.0, float(capabilities.get("cpuCount") or 1))
        memory_gib = max(1.0, float(capabilities.get("memoryTotalBytes") or 0) / (1024 ** 3))
        override = float(worker.get("allocation_weight") or 1.0)
        # CPU dominates throughput; memory adds a smaller smoothing term so
        # larger machines get more work without making the split too extreme.
        return max(0.1, slots_total * (cpu_count + (memory_gib / 8.0)) * override)

    def list_dataset_case_ids(self, dataset_ref: str) -> list[str]:
        dataset_path = Path(dataset_ref).expanduser().resolve()
        if not dataset_path.exists() or not dataset_path.is_dir():
            raise RuntimeError(f"dataset path not found: {dataset_path}")
        if (dataset_path / "task.toml").exists():
            return [dataset_path.name]
        case_ids = [
            item.name
            for item in dataset_path.iterdir()
            if item.is_dir() and (item / "task.toml").exists()
        ]
        if not case_ids:
            case_ids = [item.name for item in dataset_path.iterdir() if item.is_dir()]
        if not case_ids:
            raise RuntimeError(f"dataset has no cases: {dataset_path}")
        return sorted(case_ids)

    def list_dataset_refs(self) -> list[str]:
        return [str(path) for path in DEFAULT_PRESET_DATASETS.values()]

    def dataset_label_for_ref(self, dataset_ref: str) -> str:
        for label, path in DEFAULT_PRESET_DATASETS.items():
            if str(path) == str(Path(dataset_ref).expanduser().resolve()):
                return label
        return sanitize_name(Path(dataset_ref).name)

    def register_worker(
        self,
        *,
        worker_id: str,
        display_name: str,
        host: str,
        slots_total: int,
        slots_used: int,
        capabilities: dict[str, Any],
        status: str = "online",
    ) -> dict[str, Any]:
        now = now_iso()
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT * FROM workers WHERE worker_id = ?",
                (worker_id,),
            ).fetchone()
            if existing:
                merged_capabilities = json.loads(existing["capabilities_json"] or "{}")
                merged_capabilities.update(capabilities)
                provision_status = str(existing["provision_status"] or "none")
                if provision_status == "provisioning":
                    provision_status = "ready"
                conn.execute(
                    """
                    UPDATE workers
                    SET display_name = ?, host = ?, slots_total = ?, slots_used = ?,
                        capabilities_json = ?, status = ?, provision_status = ?,
                        last_heartbeat_at = ?, updated_at = ?
                    WHERE worker_id = ?
                    """,
                    (
                        display_name,
                        host,
                        slots_total,
                        slots_used,
                        json.dumps(merged_capabilities, ensure_ascii=False),
                        status,
                        provision_status,
                        now,
                        now,
                        worker_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO workers(
                        worker_id, display_name, host, slots_total, slots_used,
                        capabilities_json, status, enabled, note, tags_json,
                        last_heartbeat_at, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, 1, '', '[]', ?, ?, ?)
                    """,
                    (
                        worker_id,
                        display_name,
                        host,
                        slots_total,
                        slots_used,
                        json.dumps(capabilities, ensure_ascii=False),
                        status,
                        now,
                        now,
                        now,
                    ),
                )
            row = conn.execute("SELECT * FROM workers WHERE worker_id = ?", (worker_id,)).fetchone()
        return self._decorate_worker(self._worker_item(row))

    def worker_exists(self, worker_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM workers WHERE worker_id = ?",
                (worker_id,),
            ).fetchone()
        return row is not None

    def delete_worker(self, worker_id: str) -> bool:
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM workers WHERE worker_id = ?",
                (worker_id,),
            ).fetchone()
            if not existing:
                return False
            conn.execute("DELETE FROM worker_update_jobs WHERE worker_id = ?", (worker_id,))
            conn.execute("DELETE FROM provision_jobs WHERE worker_id = ?", (worker_id,))
            conn.execute("DELETE FROM workers WHERE worker_id = ?", (worker_id,))
        return True

    def create_provisioning_worker(
        self,
        *,
        worker_id: str,
        display_name: str,
        slots_total: int,
        ssh_host_alias: str,
        ssh_bootstrap_host_alias: str | None,
        connection_mode: str = "direct",
        controller_internal_ip: str | None = None,
        tunnel_remote_port: int | None = None,
    ) -> dict[str, Any]:
        now = now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO workers(
                    worker_id, display_name, host, slots_total, slots_used,
                    capabilities_json, status, enabled, note, tags_json,
                    ssh_host_alias, ssh_bootstrap_host_alias, tunnel_remote_port,
                    provision_status, last_provision_error, connection_mode,
                    controller_internal_ip,
                    last_heartbeat_at, created_at, updated_at
                ) VALUES(?, ?, '', ?, 0, '{}', 'unavailable', 1, '', '[]',
                         ?, ?, ?, 'provisioning', NULL, ?, ?,
                         NULL, ?, ?)
                """,
                (
                    worker_id,
                    display_name,
                    slots_total,
                    ssh_host_alias,
                    ssh_bootstrap_host_alias,
                    tunnel_remote_port,
                    connection_mode,
                    controller_internal_ip,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM workers WHERE worker_id = ?",
                (worker_id,),
            ).fetchone()
        return self._decorate_worker(self._worker_item(row))

    def update_worker_host(self, worker_id: str, host: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE workers SET host = ?, updated_at = ?
                WHERE worker_id = ?
                """,
                (host, now_iso(), worker_id),
            )

    def set_worker_provision_status(
        self,
        worker_id: str,
        *,
        provision_status: str,
        last_provision_error: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE workers
                SET provision_status = ?, last_provision_error = ?, updated_at = ?
                WHERE worker_id = ?
                """,
                (provision_status, last_provision_error, now_iso(), worker_id),
            )

    def create_provision_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        mode: str,
        steps: list[dict[str, Any]],
    ) -> dict[str, Any]:
        now = now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO provision_jobs(
                    job_id, worker_id, mode, status, current_step,
                    steps_json, log_text, error_text, created_at, finished_at
                ) VALUES(?, ?, ?, 'pending', NULL, ?, '', NULL, ?, NULL)
                """,
                (
                    job_id,
                    worker_id,
                    mode,
                    json.dumps(steps, ensure_ascii=False),
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM provision_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return self._provision_job_item(row)

    def get_provision_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM provision_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return self._provision_job_item(row) if row else None

    def get_latest_provision_job_for_worker(self, worker_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM provision_jobs
                WHERE worker_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (worker_id,),
            ).fetchone()
        return self._provision_job_item(row) if row else None

    def append_provision_log(self, job_id: str, chunk: str) -> None:
        if not chunk:
            return
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE provision_jobs
                SET log_text = log_text || ?
                WHERE job_id = ?
                """,
                (chunk, job_id),
            )

    def update_provision_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        current_step: str | None = None,
        steps: list[dict[str, Any]] | None = None,
        error_text: str | None = None,
        finished: bool = False,
    ) -> dict[str, Any] | None:
        now = now_iso()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM provision_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if not row:
                return None
            next_status = status or str(row["status"])
            next_step = current_step if current_step is not None else row["current_step"]
            next_steps_json = (
                json.dumps(steps, ensure_ascii=False)
                if steps is not None
                else str(row["steps_json"])
            )
            next_error = error_text if error_text is not None else row["error_text"]
            finished_at = now if finished else row["finished_at"]
            conn.execute(
                """
                UPDATE provision_jobs
                SET status = ?, current_step = ?, steps_json = ?,
                    error_text = ?, finished_at = ?
                WHERE job_id = ?
                """,
                (next_status, next_step, next_steps_json, next_error, finished_at, job_id),
            )
            updated = conn.execute(
                "SELECT * FROM provision_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return self._provision_job_item(updated)

    def _provision_job_item(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["steps"] = json.loads(item.pop("steps_json"))
        item["log_tail"] = item["log_text"][-8192:] if item.get("log_text") else ""
        return item

    def create_worker_update_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        targets: list[str],
        steps: list[dict[str, Any]],
    ) -> dict[str, Any]:
        now = now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO worker_update_jobs(
                    job_id, worker_id, targets_json, status, current_step,
                    steps_json, log_text, error_text, created_at, finished_at
                ) VALUES(?, ?, ?, 'pending', NULL, ?, '', NULL, ?, NULL)
                """,
                (
                    job_id,
                    worker_id,
                    json.dumps(targets, ensure_ascii=False),
                    json.dumps(steps, ensure_ascii=False),
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM worker_update_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return self._worker_update_job_item(row)

    def get_worker_update_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM worker_update_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return self._worker_update_job_item(row) if row else None

    def get_latest_worker_update_job_for_worker(self, worker_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM worker_update_jobs
                WHERE worker_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (worker_id,),
            ).fetchone()
        return self._worker_update_job_item(row) if row else None

    def get_active_worker_update_job_for_worker(self, worker_id: str) -> dict[str, Any] | None:
        latest = self.get_latest_worker_update_job_for_worker(worker_id)
        if not latest:
            return None
        if str(latest["status"]) in {"pending", "running"}:
            return latest
        return None

    def append_worker_update_log(self, job_id: str, chunk: str) -> None:
        if not chunk:
            return
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE worker_update_jobs
                SET log_text = log_text || ?
                WHERE job_id = ?
                """,
                (chunk, job_id),
            )

    def update_worker_update_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        current_step: str | None = None,
        steps: list[dict[str, Any]] | None = None,
        error_text: str | None = None,
        finished: bool = False,
    ) -> dict[str, Any] | None:
        now = now_iso()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM worker_update_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if not row:
                return None
            next_status = status or str(row["status"])
            next_step = current_step if current_step is not None else row["current_step"]
            next_steps_json = (
                json.dumps(steps, ensure_ascii=False)
                if steps is not None
                else str(row["steps_json"])
            )
            next_error = error_text if error_text is not None else row["error_text"]
            finished_at = now if finished else row["finished_at"]
            conn.execute(
                """
                UPDATE worker_update_jobs
                SET status = ?, current_step = ?, steps_json = ?,
                    error_text = ?, finished_at = ?
                WHERE job_id = ?
                """,
                (next_status, next_step, next_steps_json, next_error, finished_at, job_id),
            )
            updated = conn.execute(
                "SELECT * FROM worker_update_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return self._worker_update_job_item(updated)

    def _worker_update_job_item(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["targets"] = json.loads(item.pop("targets_json"))
        item["steps"] = json.loads(item.pop("steps_json"))
        item["log_tail"] = item["log_text"][-8192:] if item.get("log_text") else ""
        return item

    def update_run_sync_fields(
        self,
        *,
        run_id: str,
        sync_status: str | None = None,
        sync_job_id: str | None = None,
        sync_manifest: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        run = self.get_run(run_id)
        if not run:
            return None
        next_status = sync_status if sync_status is not None else str(run.get("sync_status") or "")
        next_job_id = sync_job_id if sync_job_id is not None else run.get("sync_job_id")
        next_manifest = (
            json.dumps(sync_manifest, ensure_ascii=False)
            if sync_manifest is not None
            else json.dumps(run.get("sync_manifest") or {}, ensure_ascii=False)
        )
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE runs
                SET sync_status = ?, sync_job_id = ?, sync_manifest_json = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (next_status, next_job_id, next_manifest, now_iso(), run_id),
            )
        return self.get_run(run_id)

    def update_run_rerun_fields(
        self,
        *,
        run_id: str,
        rerun_status: str | None = None,
        rerun_job_id: str | None = None,
    ) -> dict[str, Any] | None:
        run = self.get_run(run_id)
        if not run:
            return None
        next_status = rerun_status if rerun_status is not None else str(run.get("rerun_status") or "idle")
        next_job_id = rerun_job_id if rerun_job_id is not None else run.get("rerun_job_id")
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE runs
                SET rerun_status = ?, rerun_job_id = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (next_status, next_job_id, now_iso(), run_id),
            )
        return self.get_run(run_id)

    def create_run_rerun_job(
        self,
        *,
        job_id: str,
        run_id: str,
        case_ids: list[str],
        worker_shards: dict[str, list[str]],
        rerun_batches: dict[str, str],
        selected_error_types: list[str] | None = None,
    ) -> dict[str, Any]:
        now = now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO run_rerun_jobs(
                    job_id, run_id, status, sync_job_id,
                    case_ids_json, worker_shards_json, rerun_batches_json,
                    selected_error_types_json,
                    error_text, created_at, finished_at
                ) VALUES(?, ?, 'pending', NULL, ?, ?, ?, ?, NULL, ?, NULL)
                """,
                (
                    job_id,
                    run_id,
                    json.dumps(case_ids, ensure_ascii=False),
                    json.dumps(worker_shards, ensure_ascii=False),
                    json.dumps(rerun_batches, ensure_ascii=False),
                    json.dumps(selected_error_types or [], ensure_ascii=False),
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM run_rerun_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return self._run_rerun_job_item(row)

    def update_run_rerun_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        sync_job_id: str | None = None,
        error_text: str | None = None,
        finished: bool = False,
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM run_rerun_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if not row:
                return None
            next_status = status if status is not None else str(row["status"])
            next_sync_job_id = sync_job_id if sync_job_id is not None else row["sync_job_id"]
            next_error = error_text if error_text is not None else row["error_text"]
            finished_at = now_iso() if finished else row["finished_at"]
            conn.execute(
                """
                UPDATE run_rerun_jobs
                SET status = ?, sync_job_id = ?, error_text = ?, finished_at = ?
                WHERE job_id = ?
                """,
                (next_status, next_sync_job_id, next_error, finished_at, job_id),
            )
            updated = conn.execute(
                "SELECT * FROM run_rerun_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return self._run_rerun_job_item(updated)

    def get_run_rerun_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM run_rerun_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return self._run_rerun_job_item(row) if row else None

    def get_active_run_rerun_job(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM run_rerun_jobs
                WHERE run_id = ? AND status IN ('pending', 'running')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        return self._run_rerun_job_item(row) if row else None

    def _run_rerun_job_item(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["case_ids"] = json.loads(item.pop("case_ids_json"))
        item["worker_shards"] = json.loads(item.pop("worker_shards_json"))
        item["rerun_batches"] = json.loads(item.pop("rerun_batches_json"))
        selected_raw = item.pop("selected_error_types_json", None)
        item["selected_error_types"] = json.loads(selected_raw) if selected_raw else []
        return item

    def create_asset_sync_job(
        self,
        *,
        job_id: str,
        run_id: str,
        steps: list[dict[str, Any]],
    ) -> dict[str, Any]:
        now = now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO asset_sync_jobs(
                    job_id, run_id, status, current_step,
                    steps_json, log_text, error_text, created_at, finished_at
                ) VALUES(?, ?, 'pending', NULL, ?, '', NULL, ?, NULL)
                """,
                (job_id, run_id, json.dumps(steps, ensure_ascii=False), now),
            )
            row = conn.execute(
                "SELECT * FROM asset_sync_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return self._asset_sync_job_item(row)

    def get_asset_sync_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM asset_sync_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return self._asset_sync_job_item(row) if row else None

    def get_asset_sync_job_for_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM asset_sync_jobs
                WHERE run_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        return self._asset_sync_job_item(row) if row else None

    def append_asset_sync_log(self, job_id: str, chunk: str) -> None:
        if not chunk:
            return
        with self.connect() as conn:
            conn.execute(
                "UPDATE asset_sync_jobs SET log_text = log_text || ? WHERE job_id = ?",
                (chunk, job_id),
            )

    def update_asset_sync_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        current_step: str | None = None,
        steps: list[dict[str, Any]] | None = None,
        error_text: str | None = None,
        finished: bool = False,
    ) -> dict[str, Any] | None:
        now = now_iso()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM asset_sync_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if not row:
                return None
            next_status = status or str(row["status"])
            next_step = current_step if current_step is not None else row["current_step"]
            next_steps_json = (
                json.dumps(steps, ensure_ascii=False)
                if steps is not None
                else str(row["steps_json"])
            )
            next_error = error_text if error_text is not None else row["error_text"]
            finished_at = now if finished else row["finished_at"]
            conn.execute(
                """
                UPDATE asset_sync_jobs
                SET status = ?, current_step = ?, steps_json = ?,
                    error_text = ?, finished_at = ?
                WHERE job_id = ?
                """,
                (next_status, next_step, next_steps_json, next_error, finished_at, job_id),
            )
            updated = conn.execute(
                "SELECT * FROM asset_sync_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return self._asset_sync_job_item(updated)

    def _asset_sync_job_item(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["steps"] = json.loads(item.pop("steps_json"))
        item["log_tail"] = item["log_text"][-8192:] if item.get("log_text") else ""
        return item

    def list_workers(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM workers ORDER BY worker_id").fetchall()
        return [self._decorate_worker(self._worker_item(row)) for row in rows]

    @staticmethod
    def _batch_target_worker_id(batch: dict[str, Any], run: dict[str, Any] | None) -> str:
        target = str(batch.get("assigned_worker_id") or batch.get("preferred_worker_id") or "").strip()
        if not target and run:
            target = str(run.get("bound_worker_id") or "").strip()
        return target

    def worker_has_active_batches(self, worker_id: str) -> dict[str, int]:
        runs = {item["run_id"]: item for item in self.list_runs()}
        running = 0
        queued = 0
        for batch in self.list_batches():
            status = str(batch["status"])
            run = runs.get(str(batch["run_id"]))
            target = self._batch_target_worker_id(batch, run)
            if target != worker_id:
                continue
            if status == "running":
                running += 1
            elif status == "queued":
                queued += 1
        return {"runningCount": running, "queuedCount": queued}

    def list_worker_runtime_status(self) -> dict[str, Any]:
        workers = {item["worker_id"]: item for item in self.list_workers()}
        runs = {item["run_id"]: item for item in self.list_runs()}
        templates = {item["template_id"]: item for item in self.list_task_templates()}
        worker_states: dict[str, dict[str, Any]] = {}
        for worker_id, worker in workers.items():
            worker_states[worker_id] = {
                "workerId": worker_id,
                "workerName": worker["display_name"],
                "status": worker["status"],
                "slotsTotal": worker["slots_total"],
                "slotsUsed": worker["slots_used"],
                "availableSlots": max(0, int(worker["slots_total"]) - int(worker["slots_used"])),
                "runningBatches": [],
                "queuedBatches": [],
            }
        shared_queue: list[dict[str, Any]] = []

        def batch_runtime_item(batch: dict[str, Any], *, queue_position: int | None = None) -> dict[str, Any]:
            run = runs.get(str(batch["run_id"]))
            template = templates.get(str(run["template_id"])) if run else None
            return {
                "batchId": batch["batch_id"],
                "runId": batch["run_id"],
                "runName": run["display_name"] if run else None,
                "taskName": template["name"] if template else None,
                "datasetRef": template["dataset_ref"] if template else None,
                "status": batch["status"],
                "currentStep": batch["current_step"],
                "preferredWorkerId": batch["preferred_worker_id"],
                "assignedWorkerId": batch["assigned_worker_id"],
                "caseCount": len(batch.get("selected_case_ids") or []),
                "createdAt": batch["created_at"],
                "startedAt": batch["started_at"],
                "finishedAt": batch["finished_at"],
                "queuePosition": queue_position,
            }

        queued_positions: dict[str, int] = {}
        for batch in sorted(self.list_batches(), key=lambda item: str(item["created_at"])):
            status = str(batch["status"])
            run = runs.get(str(batch["run_id"]))
            target_worker = self._batch_target_worker_id(batch, run)
            if status == "running" and target_worker in worker_states:
                worker_states[target_worker]["runningBatches"].append(batch_runtime_item(batch))
            elif status == "queued":
                if target_worker in worker_states:
                    queued_positions[target_worker] = queued_positions.get(target_worker, 0) + 1
                    worker_states[target_worker]["queuedBatches"].append(
                        batch_runtime_item(batch, queue_position=queued_positions[target_worker])
                    )
                else:
                    shared_queue.append(batch_runtime_item(batch, queue_position=len(shared_queue) + 1))

        for item in worker_states.values():
            item["runningCount"] = len(item["runningBatches"])
            item["queuedCount"] = len(item["queuedBatches"])
            item["currentBatch"] = item["runningBatches"][0] if item["runningBatches"] else None

        return {
            "time": now_iso(),
            "workers": list(worker_states.values()),
            "sharedQueue": shared_queue,
            "summary": {
                "runningBatches": sum(len(item["runningBatches"]) for item in worker_states.values()),
                "queuedBatches": sum(len(item["queuedBatches"]) for item in worker_states.values()) + len(shared_queue),
                "sharedQueuedBatches": len(shared_queue),
            },
        }

    def claim_next_batch(self, worker_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            worker = conn.execute("SELECT * FROM workers WHERE worker_id = ?", (worker_id,)).fetchone()
            if not worker:
                return None
            rows = conn.execute(
                """
                SELECT b.*, r.template_id, r.bound_worker_id, t.dataset_ref, t.executor_config_json
                FROM batches b
                JOIN runs r ON r.run_id = b.run_id
                JOIN task_templates t ON t.template_id = r.template_id
                WHERE b.status = 'queued'
                ORDER BY b.created_at ASC
                """
            ).fetchall()
            chosen = None
            if int(worker["enabled"] or 1) != 1:
                return None
            for row in rows:
                preferred = str(row["preferred_worker_id"] or "").strip()
                bound = str(row["bound_worker_id"] or "").strip()
                if preferred and preferred != worker_id:
                    continue
                if not preferred and bound and bound != worker_id:
                    continue
                chosen = row
                break
            if not chosen:
                return None
            now = now_iso()
            conn.execute(
                """
                UPDATE batches
                SET status = 'running', current_step = 'executor-starting',
                    assigned_worker_id = ?, started_at = COALESCE(started_at, ?)
                WHERE batch_id = ?
                """,
                (worker_id, now, str(chosen["batch_id"])),
            )
            conn.execute(
                """
                UPDATE runs
                SET bound_worker_id = COALESCE(bound_worker_id, ?), updated_at = ?
                WHERE run_id = ?
                """,
                (worker_id, now, str(chosen["run_id"])),
            )
            batch = conn.execute("SELECT * FROM batches WHERE batch_id = ?", (str(chosen["batch_id"]),)).fetchone()
        batch_item = self._batch_item(batch)
        run_item = self.get_run(batch_item["run_id"])
        template_item = self.get_task_template(run_item["template_id"]) if run_item else None
        return {
            "batch": batch_item,
            "run": run_item,
            "template": template_item,
            "datasetRef": str(chosen["dataset_ref"]),
            "executorConfig": json.loads(str(chosen["executor_config_json"])),
        }

    def update_batch_progress(
        self,
        *,
        batch_id: str,
        worker_id: str,
        status: str,
        current_step: str | None,
        finished: bool,
        error_text: str | None = None,
        summary: dict[str, Any] | None = None,
        cases: list[dict[str, Any]] | None = None,
        executor_metadata: dict[str, Any] | None = None,
        artifact_index: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        now = now_iso()
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM batches WHERE batch_id = ?", (batch_id,)).fetchone()
            if not row:
                return None
            conn.execute(
                """
                UPDATE batches
                SET status = ?, current_step = ?, error_text = ?,
                    finished_at = CASE WHEN ? THEN ? ELSE finished_at END,
                    summary_json = CASE WHEN ? THEN ? ELSE summary_json END,
                    executor_metadata_json = CASE WHEN ? THEN ? ELSE executor_metadata_json END,
                    artifact_index_json = CASE WHEN ? THEN ? ELSE artifact_index_json END
                WHERE batch_id = ?
                """,
                (
                    status,
                    current_step,
                    error_text,
                    1 if finished else 0,
                    now,
                    1 if summary is not None else 0,
                    json.dumps(summary or {}, ensure_ascii=False),
                    1 if executor_metadata is not None else 0,
                    json.dumps(executor_metadata or {}, ensure_ascii=False),
                    1 if artifact_index is not None else 0,
                    json.dumps(artifact_index or {}, ensure_ascii=False),
                    batch_id,
                ),
            )
            if cases is not None:
                conn.execute("DELETE FROM case_runs WHERE batch_id = ?", (batch_id,))
                for case in cases:
                    case_id = str(case["caseId"])
                    metrics = dict(case.get("metrics") or {})
                    if case.get("errorType"):
                        metrics["errorType"] = case.get("errorType")
                    conn.execute(
                        """
                        INSERT INTO case_runs(
                            case_run_id, batch_id, case_id, status, score, metrics_json,
                            artifact_index_json, error_text, created_at, updated_at
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            new_id("case"),
                            batch_id,
                            case_id,
                            str(case.get("status") or "pending"),
                            case.get("score"),
                            json.dumps(metrics, ensure_ascii=False),
                            json.dumps(case.get("artifactIndex") or {}, ensure_ascii=False),
                            case.get("errorText"),
                            now,
                            now,
                        ),
                    )
            conn.execute(
                """
                UPDATE workers
                SET last_heartbeat_at = ?, updated_at = ?, status = 'online'
                WHERE worker_id = ?
                """,
                (now, now, worker_id),
            )
            updated = conn.execute("SELECT * FROM batches WHERE batch_id = ?", (batch_id,)).fetchone()
        return self._batch_item(updated) if updated else None

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM batches WHERE batch_id = ?", (batch_id,)).fetchone()
        return self._batch_item(row) if row else None

    def list_batches(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM batches ORDER BY created_at DESC").fetchall()
        return [self._batch_item(row) for row in rows]

    def update_worker_settings(
        self,
        *,
        worker_id: str,
        display_name: str | None = None,
        slots_total: int | None = None,
        allocation_weight: float | None = None,
        enabled: bool | None = None,
        note: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT * FROM workers WHERE worker_id = ?",
                (worker_id,),
            ).fetchone()
            if not existing:
                return None
            conn.execute(
                """
                UPDATE workers
                SET display_name = COALESCE(?, display_name),
                    slots_total = COALESCE(?, slots_total),
                    allocation_weight = COALESCE(?, allocation_weight),
                    enabled = COALESCE(?, enabled),
                    note = COALESCE(?, note),
                    tags_json = COALESCE(?, tags_json),
                    updated_at = ?
                WHERE worker_id = ?
                """,
                (
                    display_name,
                    slots_total,
                    allocation_weight,
                    1 if enabled is True else 0 if enabled is False else None,
                    note,
                    json.dumps(tags, ensure_ascii=False) if tags is not None else None,
                    now_iso(),
                    worker_id,
                ),
            )
            row = conn.execute("SELECT * FROM workers WHERE worker_id = ?", (worker_id,)).fetchone()
        return self._decorate_worker(self._worker_item(row)) if row else None

    def list_eval_task_summaries(self) -> list[dict[str, Any]]:
        templates = {item["template_id"]: item for item in self.list_task_templates()}
        runs = self.list_runs()
        batches_by_run: dict[str, list[dict[str, Any]]] = {}
        for batch in self.list_batches():
            batches_by_run.setdefault(str(batch["run_id"]), []).append(batch)
        cases_by_batch: dict[str, list[dict[str, Any]]] = {}
        with self.connect() as conn:
            for row in conn.execute("SELECT * FROM case_runs ORDER BY updated_at DESC").fetchall():
                case = self._case_item(row)
                cases_by_batch.setdefault(str(case["batch_id"]), []).append(case)

        summaries: list[dict[str, Any]] = []
        for run in runs:
            template = templates.get(str(run["template_id"]))
            run_batches = batches_by_run.get(str(run["run_id"]), [])
            primary_batches = [
                batch for batch in run_batches if str(batch.get("batch_kind") or "primary") == "primary"
            ]
            status_counts = {
                "queued": 0, "pending_sync": 0, "sync_failed": 0,
                "running": 0, "succeeded": 0, "failed": 0, "stopped": 0,
            }
            workers = []
            case_total = 0
            case_succeeded = 0
            case_failed = 0
            case_errored = 0
            latest_batch = primary_batches[0] if primary_batches else None
            for batch in primary_batches:
                status = str(batch["status"])
                if status in status_counts:
                    status_counts[status] += 1
                worker_id = str(batch.get("assigned_worker_id") or "").strip()
                if worker_id and worker_id not in workers:
                    workers.append(worker_id)
                batch_cases = cases_by_batch.get(str(batch["batch_id"]), [])
                case_total += len(batch_cases)
                case_succeeded += sum(1 for case in batch_cases if case["status"] == "succeeded")
                case_failed += sum(1 for case in batch_cases if self._case_is_failed(case))
                case_errored += sum(1 for case in batch_cases if self._case_is_errored(case))

            overall_status = "idle"
            if status_counts["running"] > 0:
                overall_status = "running"
            elif status_counts["pending_sync"] > 0:
                overall_status = "syncing"
            elif status_counts["failed"] > 0 or status_counts["sync_failed"] > 0:
                overall_status = "failed"
            elif status_counts["queued"] > 0:
                overall_status = "queued"
            elif status_counts["succeeded"] > 0 and sum(status_counts.values()) == status_counts["succeeded"]:
                overall_status = "finished"
            elif primary_batches:
                overall_status = "mixed"

            summaries.append(
                {
                    "evalTaskId": run["run_id"],
                    "runId": run["run_id"],
                    "name": run["display_name"],
                    "templateId": template["template_id"] if template else None,
                    "templateName": template["name"] if template else None,
                    "owner": run["owner"],
                    "executorKind": template["executor_kind"] if template else None,
                    "datasetRef": template["dataset_ref"] if template else None,
                    "batchCount": len(primary_batches),
                    "status": overall_status,
                    "statusCounts": status_counts,
                    "workers": workers,
                    "caseTotal": case_total,
                    "caseSucceeded": case_succeeded,
                    "caseFailed": case_failed,
                    "caseErrored": case_errored,
                    "latestBatchId": latest_batch["batch_id"] if latest_batch else None,
                    "latestUpdatedAt": latest_batch["finished_at"] if latest_batch and latest_batch["finished_at"] else latest_batch["started_at"] if latest_batch else None,
                    "syncStatus": str(run.get("sync_status") or ""),
                }
            )
        return summaries

    def get_eval_task_detail(self, run_id: str) -> dict[str, Any] | None:
        run = self.get_run(run_id)
        if not run:
            return None
        template = self.get_task_template(str(run["template_id"]))
        batches = self.list_batches_for_run(run_id)
        primary_batches = [batch for batch in batches if str(batch.get("batch_kind") or "primary") == "primary"]
        worker_groups: dict[str, dict[str, Any]] = {}
        for batch in primary_batches:
            worker_id = str(batch.get("assigned_worker_id") or batch.get("preferred_worker_id") or "unassigned")
            worker = next((item for item in self.list_workers() if item["worker_id"] == worker_id), None)
            group = worker_groups.setdefault(
                worker_id,
                {
                    "workerId": worker_id,
                    "workerName": (worker["display_name"] if worker else worker_id),
                    "workerStatus": (worker["status"] if worker else "unknown"),
                    "batches": [],
                    "cases": [],
                    "statusCounts": {"queued": 0, "running": 0, "succeeded": 0, "failed": 0, "stopped": 0},
                },
            )
            group["batches"].append(batch)
            status = str(batch["status"])
            if status in group["statusCounts"]:
                group["statusCounts"][status] += 1
            actual_cases = self.list_case_runs(str(batch["batch_id"]))
            unmatched_actual = list(actual_cases)
            for case in actual_cases:
                group["cases"].append(
                    {
                        **case,
                        "batchId": batch["batch_id"],
                        "batchStatus": batch["status"],
                        "runId": run["run_id"],
                        "runName": run["display_name"],
                    }
                )
            for case_id in batch.get("selected_case_ids") or []:
                match_idx = next(
                    (
                        index
                        for index, case in enumerate(unmatched_actual)
                        if self._case_covers_selected(case, case_id)
                    ),
                    None,
                )
                if match_idx is not None:
                    unmatched_actual.pop(match_idx)
                    continue
                placeholder_status = (
                    "running" if batch["status"] == "running"
                    else "queued" if batch["status"] == "queued"
                    else "interrupted" if batch["status"] == "failed"
                    else "missing-result" if batch["status"] == "succeeded"
                    else "pending"
                )
                group["cases"].append(
                    {
                        "case_run_id": None,
                        "batch_id": batch["batch_id"],
                        "case_id": case_id,
                        "status": placeholder_status,
                        "score": None,
                        "error_text": None,
                        "created_at": batch["created_at"],
                        "updated_at": batch["started_at"] or batch["created_at"],
                        "metrics": {},
                        "artifact_index": {},
                        "batchId": batch["batch_id"],
                        "batchStatus": batch["status"],
                        "runId": run["run_id"],
                        "runName": run["display_name"],
                    }
                )
        worker_group_list = sorted(
            worker_groups.values(),
            key=lambda item: (item["workerName"], item["workerId"]),
        )
        exception_count = len(self.list_exception_cases_for_run(run_id))
        exception_summary = self.summarize_exception_types_for_run(run_id)
        merged_jobs_dir: Path | None = None
        if template:
            raw_jobs_dir = str((template.get("executor_config") or {}).get("combinedJobsDir") or "").strip()
            if raw_jobs_dir:
                merged_jobs_dir = Path(raw_jobs_dir)
        exception_display = self.summarize_exception_display_for_run(
            run_id,
            merged_jobs_dir=merged_jobs_dir,
        )
        rerun_status = str(run.get("rerun_status") or "idle")
        can_rerun = (
            self.is_run_primary_terminal(run_id)
            and exception_count > 0
            and rerun_status not in {"syncing", "running"}
        )
        return {
            "run": run,
            "template": template,
            "batches": batches,
            "workerGroups": worker_group_list,
            "canRerunExceptions": can_rerun,
            "exceptionCount": exception_count,
            "exceptionDisplay": exception_display,
            "exceptionSummary": exception_summary,
            "rerunStatus": rerun_status,
            "rerunJobId": run.get("rerun_job_id"),
        }

    def list_batch_summaries(self) -> list[dict[str, Any]]:
        templates = {item["template_id"]: item for item in self.list_task_templates()}
        runs = {item["run_id"]: item for item in self.list_runs()}
        workers = {item["worker_id"]: item for item in self.list_workers()}
        items: list[dict[str, Any]] = []
        for batch in self.list_batches():
            run = runs.get(str(batch["run_id"]))
            template = templates.get(str(run["template_id"])) if run else None
            assigned_worker = workers.get(str(batch.get("assigned_worker_id") or ""))
            cases = self.list_case_runs(str(batch["batch_id"]))
            items.append(
                {
                    "batchId": batch["batch_id"],
                    "runId": batch["run_id"],
                    "runName": run["display_name"] if run else None,
                    "templateId": template["template_id"] if template else None,
                    "taskName": template["name"] if template else None,
                    "datasetRef": template["dataset_ref"] if template else None,
                    "executorKind": batch["executor_kind"],
                    "status": batch["status"],
                    "currentStep": batch["current_step"],
                    "preferredWorkerId": batch["preferred_worker_id"],
                    "assignedWorkerId": batch["assigned_worker_id"],
                    "assignedWorkerName": assigned_worker["display_name"] if assigned_worker else None,
                    "selectedCaseIds": batch["selected_case_ids"],
                    "summary": batch["summary"],
                    "caseCount": len(cases),
                    "caseSucceeded": sum(1 for case in cases if case["status"] == "succeeded"),
                    "caseFailed": sum(1 for case in cases if self._case_is_failed(case)),
                    "caseErrored": sum(1 for case in cases if self._case_is_errored(case)),
                    "createdAt": batch["created_at"],
                    "startedAt": batch["started_at"],
                    "finishedAt": batch["finished_at"],
                    "errorText": batch["error_text"],
                }
            )
        return items

    def get_batch_detail(self, batch_id: str) -> dict[str, Any] | None:
        batch = self.get_batch(batch_id)
        if not batch:
            return None
        run = self.get_run(str(batch["run_id"]))
        template = self.get_task_template(str(run["template_id"])) if run else None
        worker = None
        if batch.get("assigned_worker_id"):
            worker = next(
                (item for item in self.list_workers() if item["worker_id"] == batch["assigned_worker_id"]),
                None,
            )
        cases = self.list_case_runs(batch_id)
        return {
            "batch": batch,
            "run": run,
            "template": template,
            "worker": worker,
            "cases": cases,
        }

    def list_case_runs(self, batch_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM case_runs WHERE batch_id = ? ORDER BY case_id",
                (batch_id,),
            ).fetchall()
        return [self._case_item(row) for row in rows]

    def _template_item(self, row: sqlite3.Row | None) -> dict[str, Any]:
        item = dict(row)
        item["executor_config"] = json.loads(item.pop("executor_config_json"))
        return item

    def _run_item(self, row: sqlite3.Row | None) -> dict[str, Any]:
        item = dict(row)
        manifest_raw = item.pop("sync_manifest_json", "{}")
        item["sync_manifest"] = json.loads(manifest_raw or "{}")
        if not item.get("sync_status"):
            item["sync_status"] = ""
        if not item.get("rerun_status"):
            item["rerun_status"] = "idle"
        return item

    def _batch_item(self, row: sqlite3.Row | None) -> dict[str, Any]:
        item = dict(row)
        item["executor_metadata"] = json.loads(item.pop("executor_metadata_json"))
        item["selected_case_ids"] = json.loads(item.pop("selected_case_ids_json"))
        item["batch_options"] = json.loads(item.pop("batch_options_json"))
        item["summary"] = json.loads(item.pop("summary_json"))
        item["artifact_index"] = json.loads(item.pop("artifact_index_json"))
        if not item.get("batch_kind"):
            item["batch_kind"] = "primary"
        return item

    def _case_item(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["metrics"] = json.loads(item.pop("metrics_json"))
        item["artifact_index"] = json.loads(item.pop("artifact_index_json"))
        original_case_id = str(item["case_id"])
        inferred_case_id = self._infer_case_id(original_case_id, item["artifact_index"])
        item["original_case_id"] = original_case_id
        item["case_id"] = inferred_case_id
        return item

    @staticmethod
    def _infer_case_id(original_case_id: str, artifact_index: dict[str, Any]) -> str:
        trial_dir = str((artifact_index or {}).get("trialDir") or "").strip()
        if not trial_dir:
            return original_case_id
        stem = Path(trial_dir).name
        if "__" not in stem:
            return original_case_id
        candidate = stem.rsplit("__", 1)[0].strip()
        return candidate or original_case_id

    def resolve_dataset_case_id(
        self,
        *,
        dataset_path: Path,
        case: dict[str, Any],
        selected_case_ids: list[str] | None = None,
    ) -> str | None:
        root = dataset_path.expanduser().resolve()
        candidates: list[str] = []
        for key in ("original_case_id", "case_id"):
            value = str(case.get(key) or "").strip()
            if value and value not in candidates:
                candidates.append(value)
        for candidate in candidates:
            if (root / candidate).is_dir():
                return candidate
        for selected_id in selected_case_ids or []:
            selected = str(selected_id or "").strip()
            if not selected:
                continue
            if self._case_covers_selected(case, selected) and (root / selected).is_dir():
                return selected
        if root.is_dir():
            for entry in sorted(root.iterdir()):
                if entry.is_dir() and self._case_covers_selected(case, entry.name):
                    return entry.name
        return candidates[0] if candidates else None

    @staticmethod
    def _case_covers_selected(actual_case: dict[str, Any], selected_id: str) -> bool:
        selected_id = str(selected_id or "").strip()
        if not selected_id:
            return False
        actual_ids = {
            str(actual_case.get("case_id") or ""),
            str(actual_case.get("original_case_id") or ""),
        }
        if selected_id in actual_ids:
            return True
        for actual_id in actual_ids:
            if not actual_id:
                continue
            if actual_id in selected_id or selected_id in actual_id:
                return True
        trial_dir = str((actual_case.get("artifact_index") or {}).get("trialDir") or "")
        if trial_dir and selected_id in trial_dir:
            return True
        return False

    def _worker_item(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["capabilities"] = json.loads(item.pop("capabilities_json"))
        item["tags"] = json.loads(item.pop("tags_json"))
        item["enabled"] = bool(item["enabled"])
        return item

    def _decorate_worker(
        self,
        item: dict[str, Any],
        *,
        heartbeat_timeout_sec: int = DEFAULT_HEARTBEAT_TIMEOUT_SEC,
    ) -> dict[str, Any]:
        provision_status = str(item.get("provision_status") or "none")
        if provision_status == "provisioning":
            item["status"] = "provisioning"
            item["manualStatus"] = "enabled" if item.get("enabled", True) else "disabled"
            item["allocationScore"] = round(self._worker_allocation_score(item), 2)
            latest = self.get_latest_provision_job_for_worker(str(item["worker_id"]))
            if latest:
                item["last_provision_job_id"] = latest["job_id"]
            latest_update = self.get_latest_worker_update_job_for_worker(str(item["worker_id"]))
            if latest_update:
                item["last_update_job_id"] = latest_update["job_id"]
                if str(latest_update["status"]) in {"pending", "running"}:
                    item["update_status"] = "updating"
            return item
        if provision_status == "failed":
            item["status"] = "provision_failed"
            item["manualStatus"] = "enabled" if item.get("enabled", True) else "disabled"
            item["allocationScore"] = round(self._worker_allocation_score(item), 2)
            latest = self.get_latest_provision_job_for_worker(str(item["worker_id"]))
            if latest:
                item["last_provision_job_id"] = latest["job_id"]
            latest_update = self.get_latest_worker_update_job_for_worker(str(item["worker_id"]))
            if latest_update:
                item["last_update_job_id"] = latest_update["job_id"]
                if str(latest_update["status"]) in {"pending", "running"}:
                    item["update_status"] = "updating"
            return item
        status = str(item.get("status") or "online")
        last = item.get("last_heartbeat_at")
        if status != "removed":
            if last:
                try:
                    delta = datetime.now(timezone.utc) - datetime.fromisoformat(str(last))
                    status = "unavailable" if delta.total_seconds() > heartbeat_timeout_sec else "online"
                except ValueError:
                    status = "unavailable"
            else:
                status = "unavailable"
        item["status"] = status
        item["manualStatus"] = "enabled" if item.get("enabled", True) else "disabled"
        item["allocationScore"] = round(self._worker_allocation_score(item), 2)
        latest = self.get_latest_provision_job_for_worker(str(item["worker_id"]))
        if latest:
            item["last_provision_job_id"] = latest["job_id"]
        latest_update = self.get_latest_worker_update_job_for_worker(str(item["worker_id"]))
        if latest_update:
            item["last_update_job_id"] = latest_update["job_id"]
            if str(latest_update["status"]) in {"pending", "running"}:
                item["update_status"] = "updating"
        return item
