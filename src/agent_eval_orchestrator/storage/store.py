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
            self._drop_case_details_column_if_present(conn)

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

    def create_run(self, *, template_id: str, display_name: str | None = None) -> dict[str, Any]:
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
                    latest_batch_id, created_at, updated_at
                ) VALUES(?, ?, ?, ?, NULL, NULL, ?, ?)
                """,
                (run_id, template_id, template["owner"], name, now, now),
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

    def list_batches_for_run(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM batches WHERE run_id = ? ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
        return [self._batch_item(row) for row in rows]

    def create_batch(
        self,
        *,
        run_id: str,
        selected_case_ids: list[str],
        preferred_worker_id: str | None,
        batch_options: dict[str, Any] | None,
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
                    error_text
                ) VALUES(?, ?, ?, 'queued', NULL, ?, NULL, ?, '{}', ?, ?, '{}', '{}', ?, ?, NULL, NULL, NULL)
                """,
                (
                    batch_id,
                    run_id,
                    run["owner"],
                    preferred_worker_id,
                    template["executor_kind"],
                    json.dumps(selected_case_ids, ensure_ascii=False),
                    json.dumps(batch_options or {}, ensure_ascii=False),
                    batch_root,
                    now,
                ),
            )
            conn.execute(
                "UPDATE runs SET latest_batch_id = ?, updated_at = ? WHERE run_id = ?",
                (batch_id, now, run_id),
            )
            row = conn.execute("SELECT * FROM batches WHERE batch_id = ?", (batch_id,)).fetchone()
        return self._batch_item(row)

    def create_sharded_batches(
        self,
        *,
        run_id: str,
        selected_case_ids: list[str],
        worker_ids: list[str],
        batch_options: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if not selected_case_ids:
            raise RuntimeError("selected_case_ids must not be empty")
        if not worker_ids:
            raise RuntimeError("worker_ids must not be empty")
        options = dict(batch_options or {})
        max_concurrency = int(options.get("concurrency") or DEFAULT_PER_WORKER_CONCURRENCY)
        shard_count = len(worker_ids)
        case_groups = [[] for _ in range(shard_count)]
        for index, case_id in enumerate(selected_case_ids):
            case_groups[index % shard_count].append(case_id)
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
                )
            )
        return created

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
                conn.execute(
                    """
                    UPDATE workers
                    SET display_name = ?, host = ?, slots_total = ?, slots_used = ?,
                        capabilities_json = ?, status = ?, last_heartbeat_at = ?, updated_at = ?
                    WHERE worker_id = ?
                    """,
                    (
                        display_name,
                        host,
                        slots_total,
                        slots_used,
                        json.dumps(capabilities, ensure_ascii=False),
                        status,
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
        return self._worker_item(row)

    def list_workers(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM workers ORDER BY worker_id").fetchall()
        return [self._decorate_worker(self._worker_item(row)) for row in rows]

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
            target_worker = str(batch.get("assigned_worker_id") or batch.get("preferred_worker_id") or "").strip()
            if not target_worker and run:
                target_worker = str(run.get("bound_worker_id") or "").strip()
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
                            json.dumps(case.get("metrics") or {}, ensure_ascii=False),
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
                    enabled = COALESCE(?, enabled),
                    note = COALESCE(?, note),
                    tags_json = COALESCE(?, tags_json),
                    updated_at = ?
                WHERE worker_id = ?
                """,
                (
                    display_name,
                    slots_total,
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
            status_counts = {"queued": 0, "running": 0, "succeeded": 0, "failed": 0, "stopped": 0}
            workers = []
            case_total = 0
            case_succeeded = 0
            case_failed = 0
            latest_batch = run_batches[0] if run_batches else None
            for batch in run_batches:
                status = str(batch["status"])
                if status in status_counts:
                    status_counts[status] += 1
                worker_id = str(batch.get("assigned_worker_id") or "").strip()
                if worker_id and worker_id not in workers:
                    workers.append(worker_id)
                batch_cases = cases_by_batch.get(str(batch["batch_id"]), [])
                case_total += len(batch_cases)
                case_succeeded += sum(1 for case in batch_cases if case["status"] == "succeeded")
                case_failed += sum(1 for case in batch_cases if case["status"] == "failed")

            overall_status = "idle"
            if status_counts["running"] > 0:
                overall_status = "running"
            elif status_counts["failed"] > 0:
                overall_status = "failed"
            elif status_counts["queued"] > 0:
                overall_status = "queued"
            elif status_counts["succeeded"] > 0 and sum(status_counts.values()) == status_counts["succeeded"]:
                overall_status = "finished"
            elif run_batches:
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
                    "batchCount": len(run_batches),
                    "status": overall_status,
                    "statusCounts": status_counts,
                    "workers": workers,
                    "caseTotal": case_total,
                    "caseSucceeded": case_succeeded,
                    "caseFailed": case_failed,
                    "latestBatchId": latest_batch["batch_id"] if latest_batch else None,
                    "latestUpdatedAt": latest_batch["finished_at"] if latest_batch and latest_batch["finished_at"] else latest_batch["started_at"] if latest_batch else None,
                }
            )
        return summaries

    def get_eval_task_detail(self, run_id: str) -> dict[str, Any] | None:
        run = self.get_run(run_id)
        if not run:
            return None
        template = self.get_task_template(str(run["template_id"]))
        batches = self.list_batches_for_run(run_id)
        worker_groups: dict[str, dict[str, Any]] = {}
        for batch in batches:
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
            actual_case_ids = {str(case["case_id"]) for case in actual_cases}
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
                if case_id in actual_case_ids:
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
        return {"run": run, "template": template, "batches": batches, "workerGroups": worker_group_list}

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
                    "caseFailed": sum(1 for case in cases if case["status"] == "failed"),
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
        return dict(row)

    def _batch_item(self, row: sqlite3.Row | None) -> dict[str, Any]:
        item = dict(row)
        item["executor_metadata"] = json.loads(item.pop("executor_metadata_json"))
        item["selected_case_ids"] = json.loads(item.pop("selected_case_ids_json"))
        item["batch_options"] = json.loads(item.pop("batch_options_json"))
        item["summary"] = json.loads(item.pop("summary_json"))
        item["artifact_index"] = json.loads(item.pop("artifact_index_json"))
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
        return item
