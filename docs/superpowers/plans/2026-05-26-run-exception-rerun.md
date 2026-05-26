# Run Exception Rerun Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a run fully completes, let operators rerun all exception cases from the Task detail page on their original workers, with scoped asset re-sync and merged overwrite into parent batch records.

**Architecture:** A `RunRerunCoordinator` (mirroring `AssetSyncer` / `Provisioner`) validates finished runs, groups exception cases by worker, creates `exception_rerun` child batches linked via `parent_batch_id`, runs scoped `AssetSyncer.sync_rerun_job()`, and on worker heartbeat completion merges rerun case results back into parent batches inside a DB transaction. UI adds a **重跑 Exception** button with rerun status polling.

**Tech Stack:** Python 3.10+, stdlib (`http.server`, `sqlite3`, `threading`, `shutil`), existing `AssetSyncer` / `SshRunner`, embedded HTML/JS dashboard, pytest

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/agent_eval_orchestrator/storage/store.py` | Schema migrations (`rerun_status`, `batch_kind`, `run_rerun_jobs`), exception listing, merge helper, primary-batch terminal checks |
| `src/agent_eval_orchestrator/controller/run_rerun_coordinator.py` | Orchestrate rerun lifecycle: validate → create job/batches → start scoped sync |
| `src/agent_eval_orchestrator/controller/asset_syncer.py` | New `sync_rerun_job()` entry point for subset re-sync |
| `src/agent_eval_orchestrator/controller/server.py` | `POST /api/runs/{runId}/rerun-exceptions`, `GET /api/runs/{runId}/rerun`, heartbeat merge branch, harbor trial copy |
| `src/agent_eval_orchestrator/controller/static.py` | **重跑 Exception** button, confirm dialog, rerun status panel + polling |
| `tests/storage/test_rerun_store.py` | Schema, exception listing, merge semantics |
| `tests/controller/test_run_rerun_coordinator.py` | Coordinator validation branches |
| `tests/controller/test_asset_syncer_rerun.py` | Scoped sync with mocked subprocess |
| `tests/controller/test_rerun_exceptions_api.py` | HTTP integration for POST/GET + 409/400 paths |

---

### Task 1: Rerun schema & store CRUD

**Files:**
- Modify: `src/agent_eval_orchestrator/storage/store.py` (`_ensure_schema_migrations`, `_run_item`, `_batch_item`, new CRUD)
- Create: `tests/storage/test_rerun_store.py`

- [ ] **Step 1: Write the failing test**

Create `tests/storage/test_rerun_store.py`:

```python
import json

from agent_eval_orchestrator.core.ids import new_id


def test_rerun_schema_and_crud(store):
    template = store.create_task_template(
        owner="default",
        name="rerun-test",
        dataset_ref="/tmp/dataset",
        executor_kind="harbor-docker",
        executor_config={},
    )
    run = store.create_run(template_id=template["template_id"], display_name="rerun-run")
    job_id = new_id("rerun")

    store.update_run_rerun_fields(
        run_id=run["run_id"],
        rerun_status="syncing",
        rerun_job_id=job_id,
    )
    updated_run = store.get_run(run["run_id"])
    assert updated_run["rerun_status"] == "syncing"
    assert updated_run["rerun_job_id"] == job_id

    job = store.create_run_rerun_job(
        job_id=job_id,
        run_id=run["run_id"],
        case_ids=["case-a", "case-b"],
        worker_shards={"worker-a": ["case-a"], "worker-b": ["case-b"]},
        rerun_batches={"worker-a": "batch-rerun-a", "worker-b": "batch-rerun-b"},
    )
    assert job["status"] == "pending"
    assert job["case_ids"] == ["case-a", "case-b"]
    assert job["worker_shards"]["worker-a"] == ["case-a"]

    store.update_run_rerun_job(job_id, status="running", sync_job_id="sync-1")
    fetched = store.get_run_rerun_job(job_id)
    assert fetched["status"] == "running"
    assert fetched["sync_job_id"] == "sync-1"

    active = store.get_active_run_rerun_job(run["run_id"])
    assert active is not None
    assert active["job_id"] == job_id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/djn/code/Agent-Eval-Orchestrator && uv run --extra dev pytest tests/storage/test_rerun_store.py::test_rerun_schema_and_crud -v`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'create_run_rerun_job'`

- [ ] **Step 3: Write minimal implementation**

In `store.py` `_ensure_schema_migrations`, after the `asset_sync_jobs` block, add:

```python
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
```

Update `_run_item`:

```python
    def _run_item(self, row: sqlite3.Row | None) -> dict[str, Any]:
        item = dict(row)
        manifest_raw = item.pop("sync_manifest_json", "{}")
        item["sync_manifest"] = json.loads(manifest_raw or "{}")
        if not item.get("sync_status"):
            item["sync_status"] = ""
        if not item.get("rerun_status"):
            item["rerun_status"] = "idle"
        return item
```

Update `_batch_item`:

```python
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
```

Add CRUD methods:

```python
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
    ) -> dict[str, Any]:
        now = now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO run_rerun_jobs(
                    job_id, run_id, status, sync_job_id,
                    case_ids_json, worker_shards_json, rerun_batches_json,
                    error_text, created_at, finished_at
                ) VALUES(?, ?, 'pending', NULL, ?, ?, ?, NULL, ?, NULL)
                """,
                (
                    job_id,
                    run_id,
                    json.dumps(case_ids, ensure_ascii=False),
                    json.dumps(worker_shards, ensure_ascii=False),
                    json.dumps(rerun_batches, ensure_ascii=False),
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
        return item
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/storage/test_rerun_store.py::test_rerun_schema_and_crud -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/storage/test_rerun_store.py src/agent_eval_orchestrator/storage/store.py
git commit -m "feat: add run rerun job schema and store CRUD"
```

---

### Task 2: Exception case listing & primary-batch terminal check

**Files:**
- Modify: `src/agent_eval_orchestrator/storage/store.py` (`create_batch`, `is_run_terminal`, new helpers)
- Modify: `tests/storage/test_rerun_store.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/storage/test_rerun_store.py`:

```python
def _seed_finished_run_with_cases(store, *, cases):
    template = store.create_task_template(
        owner="default",
        name="exc-test",
        dataset_ref="/tmp/dataset",
        executor_kind="harbor-docker",
        executor_config={},
    )
    run = store.create_run(template_id=template["template_id"])
    store.register_worker(
        worker_id="worker-a",
        display_name="worker-a",
        host="localhost",
        slots_total=1,
        slots_used=0,
        capabilities={},
    )
    batch = store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=[item["case_id"] for item in cases],
        preferred_worker_id="worker-a",
        batch_options={},
    )
    store.update_batch_progress(
        batch_id=batch["batch_id"],
        worker_id="worker-a",
        status="succeeded",
        current_step=None,
        finished=True,
        cases=[
            {
                "caseId": item["case_id"],
                "status": item["status"],
                "score": item.get("score"),
                "errorText": item.get("error_text"),
                "metrics": item.get("metrics") or {},
                "artifactIndex": item.get("artifact_index") or {},
            }
            for item in cases
        ],
    )
    return run, batch


def test_list_exception_cases_for_run(store):
    run, batch = _seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
            {"case_id": "fail", "status": "failed", "score": 0.0},
            {"case_id": "exc", "status": "errored", "error_text": "boom"},
            {"case_id": "legacy", "status": "failed", "error_text": "timeout"},
        ],
    )
    exceptions = store.list_exception_cases_for_run(run["run_id"])
    case_ids = sorted(item["case_id"] for item in exceptions)
    assert case_ids == ["exc", "legacy"]
    assert all(item["parent_batch_id"] == batch["batch_id"] for item in exceptions)
    assert all(item["worker_id"] == "worker-a" for item in exceptions)


def test_is_run_primary_terminal_ignores_active_rerun_batches(store):
    run, batch = _seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc", "status": "errored", "error_text": "boom"}],
    )
    assert store.is_run_primary_terminal(run["run_id"]) is True
    store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["exc"],
        preferred_worker_id="worker-a",
        batch_options={},
        initial_status="running",
        batch_kind="exception_rerun",
        parent_batch_id=batch["batch_id"],
    )
    assert store.is_run_primary_terminal(run["run_id"]) is True
    assert store.is_run_terminal(run["run_id"]) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/storage/test_rerun_store.py::test_list_exception_cases_for_run tests/storage/test_rerun_store.py::test_is_run_primary_terminal_ignores_active_rerun_batches -v`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'list_exception_cases_for_run'`

- [ ] **Step 3: Write minimal implementation**

Extend `create_batch` signature and INSERT:

```python
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
        # ... existing setup ...
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
```

Add helpers:

```python
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

    def group_exception_cases_by_worker(self, run_id: str) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in self.list_exception_cases_for_run(run_id):
            worker_id = str(item["worker_id"] or "").strip()
            if not worker_id:
                continue
            grouped.setdefault(worker_id, []).append(item)
        return grouped
```

Update `is_run_terminal` to consider all batches (unchanged behavior for sync cleanup), but document that rerun API uses `is_run_primary_terminal`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/storage/test_rerun_store.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/storage/test_rerun_store.py src/agent_eval_orchestrator/storage/store.py
git commit -m "feat: add exception case listing and primary batch terminal check"
```

---

### Task 3: Merge rerun cases into parent batch

**Files:**
- Modify: `src/agent_eval_orchestrator/storage/store.py`
- Modify: `tests/storage/test_rerun_store.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/storage/test_rerun_store.py`:

```python
def test_merge_rerun_cases_into_parent_overwrites_exceptions_only(store):
    run, parent = _seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
            {"case_id": "exc", "status": "errored", "error_text": "boom"},
        ],
    )
    rerun = store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["exc"],
        preferred_worker_id="worker-a",
        batch_options={},
        batch_kind="exception_rerun",
        parent_batch_id=parent["batch_id"],
    )
    merged = store.merge_rerun_cases_into_parent(
        parent_batch_id=parent["batch_id"],
        rerun_cases=[
            {
                "caseId": "exc",
                "status": "succeeded",
                "score": 1.0,
                "metrics": {},
                "artifactIndex": {},
            }
        ],
        rerun_batch_id=rerun["batch_id"],
    )
    assert merged is not None
    parent_cases = store.list_case_runs(parent["batch_id"])
    by_id = {case["case_id"]: case for case in parent_cases}
    assert by_id["ok"]["status"] == "succeeded"
    assert by_id["exc"]["status"] == "succeeded"
    assert by_id["exc"]["score"] == 1.0
    assert merged["summary"]["succeeded"] == 2
    assert merged["summary"]["errored"] == 0
    rerun_cases = store.list_case_runs(rerun["batch_id"])
    assert rerun_cases == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/storage/test_rerun_store.py::test_merge_rerun_cases_into_parent_overwrites_exceptions_only -v`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'merge_rerun_cases_into_parent'`

- [ ] **Step 3: Write minimal implementation**

Add summary helper and merge method:

```python
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
            rerun_case_ids = {str(case["caseId"]) for case in rerun_cases}
            existing_rows = conn.execute(
                "SELECT * FROM case_runs WHERE batch_id = ?",
                (parent_batch_id,),
            ).fetchall()
            kept = [self._case_item(row) for row in existing_rows if str(row["case_id"]) not in rerun_case_ids]
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/storage/test_rerun_store.py::test_merge_rerun_cases_into_parent_overwrites_exceptions_only -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/storage/test_rerun_store.py src/agent_eval_orchestrator/storage/store.py
git commit -m "feat: merge rerun case results into parent batch"
```

---

### Task 4: RunRerunCoordinator validation & batch creation

**Files:**
- Create: `src/agent_eval_orchestrator/controller/run_rerun_coordinator.py`
- Create: `tests/controller/test_run_rerun_coordinator.py`

- [ ] **Step 1: Write the failing test**

Create `tests/controller/test_run_rerun_coordinator.py`:

```python
import pytest

from agent_eval_orchestrator.controller.run_rerun_coordinator import RunRerunCoordinator, RerunValidationError
from tests.storage.test_rerun_store import _seed_finished_run_with_cases


@pytest.fixture()
def coordinator(store):
    return RunRerunCoordinator(store=store, asset_syncer=None)


def test_start_rerun_rejects_unfinished_run(coordinator, store):
    template = store.create_task_template(
        owner="default",
        name="x",
        dataset_ref="/tmp/dataset",
        executor_kind="harbor-docker",
        executor_config={},
    )
    run = store.create_run(template_id=template["template_id"])
    store.register_worker(
        worker_id="worker-a",
        display_name="worker-a",
        host="localhost",
        slots_total=1,
        slots_used=0,
        capabilities={},
    )
    store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["case-a"],
        preferred_worker_id="worker-a",
        batch_options={},
        initial_status="running",
    )
    with pytest.raises(RerunValidationError) as exc:
        coordinator.start_rerun(run["run_id"])
    assert exc.value.code == 409
    assert "not finished" in exc.value.message.lower()


def test_start_rerun_creates_batches_and_job(coordinator, store):
    run, parent = _seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "boom"},
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
        ],
    )
    result = coordinator.start_rerun(run["run_id"])
    assert result["exceptionCount"] == 1
    assert result["rerunStatus"] == "syncing"
    updated = store.get_run(run["run_id"])
    assert updated["rerun_status"] == "syncing"
    job = store.get_run_rerun_job(result["rerunJobId"])
    assert job is not None
    assert job["rerun_batches"]["worker-a"]
    rerun_batch = store.get_batch(job["rerun_batches"]["worker-a"])
    assert rerun_batch["batch_kind"] == "exception_rerun"
    assert rerun_batch["parent_batch_id"] == parent["batch_id"]
    assert rerun_batch["status"] == "pending_sync"
    assert rerun_batch["selected_case_ids"] == ["exc-a"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/controller/test_run_rerun_coordinator.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_eval_orchestrator.controller.run_rerun_coordinator'`

- [ ] **Step 3: Write minimal implementation**

Create `src/agent_eval_orchestrator/controller/run_rerun_coordinator.py`:

```python
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent_eval_orchestrator.core.ids import new_id

if TYPE_CHECKING:
    from agent_eval_orchestrator.controller.asset_syncer import AssetSyncer
    from agent_eval_orchestrator.storage.store import Store


class RerunValidationError(Exception):
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class RunRerunCoordinator:
    def __init__(self, *, store: Store, asset_syncer: AssetSyncer | None) -> None:
        self.store = store
        self.asset_syncer = asset_syncer

    def start_rerun(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if not run:
            raise RerunValidationError(404, "run not found")
        if not self.store.is_run_primary_terminal(run_id):
            raise RerunValidationError(409, "run not finished")
        rerun_status = str(run.get("rerun_status") or "idle")
        if rerun_status in {"syncing", "running"}:
            raise RerunValidationError(409, "rerun already in progress")
        grouped = self.store.group_exception_cases_by_worker(run_id)
        if not grouped:
            raise RerunValidationError(400, "no exception cases")

        job_id = new_id("rerun")
        rerun_batches: dict[str, str] = {}
        worker_shards: dict[str, list[str]] = {}
        all_case_ids: list[str] = []
        for worker_id, items in grouped.items():
            case_ids = [str(item["case_id"]) for item in items]
            parent_batch_id = str(items[0]["parent_batch_id"])
            parent = self.store.get_batch(parent_batch_id)
            batch = self.store.create_batch(
                run_id=run_id,
                selected_case_ids=case_ids,
                preferred_worker_id=worker_id,
                batch_options=dict((parent or {}).get("batch_options") or {}),
                initial_status="pending_sync",
                batch_kind="exception_rerun",
                parent_batch_id=parent_batch_id,
            )
            rerun_batches[worker_id] = str(batch["batch_id"])
            worker_shards[worker_id] = case_ids
            all_case_ids.extend(case_ids)

        self.store.create_run_rerun_job(
            job_id=job_id,
            run_id=run_id,
            case_ids=all_case_ids,
            worker_shards=worker_shards,
            rerun_batches=rerun_batches,
        )
        self.store.update_run_rerun_fields(
            run_id=run_id,
            rerun_status="syncing",
            rerun_job_id=job_id,
        )
        if self.asset_syncer is not None:
            self.asset_syncer.start_rerun_sync_async(job_id=job_id, run_id=run_id)

        return {
            "rerunJobId": job_id,
            "rerunStatus": "syncing",
            "exceptionCount": len(all_case_ids),
            "workerShards": {worker_id: len(case_ids) for worker_id, case_ids in worker_shards.items()},
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/controller/test_run_rerun_coordinator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/controller/run_rerun_coordinator.py tests/controller/test_run_rerun_coordinator.py
git commit -m "feat: add RunRerunCoordinator start_rerun orchestration"
```

---

### Task 5: AssetSyncer scoped rerun sync

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/asset_syncer.py`
- Create: `tests/controller/test_asset_syncer_rerun.py`

- [ ] **Step 1: Write the failing test**

Create `tests/controller/test_asset_syncer_rerun.py`:

```python
from unittest.mock import patch

from agent_eval_orchestrator.controller.asset_syncer import AssetSyncer
from tests.storage.test_rerun_store import _seed_finished_run_with_cases


def test_sync_rerun_job_promotes_rerun_batches(store, tmp_path, sample_ssh_config):
    run, parent = _seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom"}],
    )
    store.update_run_sync_fields(
        run_id=run["run_id"],
        sync_status="succeeded",
        sync_manifest={
            "datasetPath": str(tmp_path / "dataset"),
            "bitfunCliPath": str(tmp_path / "bitfun-cli"),
            "bitfunConfigDir": str(tmp_path / "bitfun-config"),
            "workers": {
                "worker-a": {
                    "caseIds": ["exc-a"],
                    "targetRoot": str(tmp_path / "shared" / "sync" / run["run_id"]),
                    "transport": "local",
                }
            },
        },
    )
    rerun = store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["exc-a"],
        preferred_worker_id="worker-a",
        batch_options={},
        initial_status="pending_sync",
        batch_kind="exception_rerun",
        parent_batch_id=parent["batch_id"],
    )
    job = store.create_run_rerun_job(
        job_id="rerun-1",
        run_id=run["run_id"],
        case_ids=["exc-a"],
        worker_shards={"worker-a": ["exc-a"]},
        rerun_batches={"worker-a": rerun["batch_id"]},
    )
    syncer = AssetSyncer(
        store=store,
        ssh_config_path=sample_ssh_config,
        controller_shared_root=tmp_path,
    )
    dataset = tmp_path / "dataset" / "exc-a"
    dataset.mkdir(parents=True)
    (dataset / "task.toml").write_text("", encoding="utf-8")
    bitfun_cli = tmp_path / "bitfun-cli"
    bitfun_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    bitfun_cli.chmod(0o755)
    (tmp_path / "bitfun-config").mkdir()

    with patch.object(syncer, "_sync_cases"), patch.object(syncer, "_sync_bitfun"):
        syncer.sync_rerun_job(job_id=job["job_id"], run_id=run["run_id"])

    promoted = store.get_batch(rerun["batch_id"])
    assert promoted["status"] == "queued"
    updated_run = store.get_run(run["run_id"])
    assert updated_run["rerun_status"] == "running"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/controller/test_asset_syncer_rerun.py::test_sync_rerun_job_promotes_rerun_batches -v`
Expected: FAIL — `AttributeError: 'AssetSyncer' object has no attribute 'sync_rerun_job'`

- [ ] **Step 3: Write minimal implementation**

In `asset_syncer.py`, add:

```python
    def start_rerun_sync_async(self, *, job_id: str, run_id: str) -> None:
        thread = threading.Thread(
            target=self.sync_rerun_job,
            kwargs={"job_id": job_id, "run_id": run_id},
            daemon=True,
        )
        thread.start()

    def sync_rerun_job(self, *, job_id: str, run_id: str) -> None:
        rerun_job = self.store.get_run_rerun_job(job_id)
        if not rerun_job:
            raise RuntimeError("rerun job not found")
        run = self.store.get_run(run_id)
        if not run:
            raise RuntimeError("run not found")
        manifest = dict(run.get("sync_manifest") or {})
        template = self.store.get_task_template(str(run["template_id"]))
        executor_config = dict(template.get("executor_config") or {}) if template else {}
        worker_shards = dict(rerun_job["worker_shards"])
        worker_ids = list(worker_shards.keys())
        steps = initial_worker_steps(worker_ids)
        sync_job_id = new_id("sync")
        self.store.update_run_rerun_job(job_id, status="running", sync_job_id=sync_job_id)
        self.store.create_asset_sync_job(job_id=sync_job_id, run_id=run_id, steps=steps)
        self.store.update_asset_sync_job(sync_job_id, status="running", steps=steps)

        errors: list[str] = []
        lock = threading.Lock()

        def worker_thread(worker_id: str) -> None:
            nonlocal steps
            case_ids = list(worker_shards[worker_id])
            base_entry = dict((manifest.get("workers") or {}).get(worker_id) or {})
            entry = {**base_entry, "caseIds": case_ids}
            try:
                with lock:
                    steps = set_worker_step_status(steps, worker_id, "sync_cases", "running")
                    self.store.update_asset_sync_job(sync_job_id, steps=steps)
                self._sync_cases(entry, manifest)
                with lock:
                    steps = set_worker_step_status(steps, worker_id, "sync_cases", "succeeded")
                    steps = set_worker_step_status(steps, worker_id, "sync_bitfun", "running")
                    self.store.update_asset_sync_job(sync_job_id, steps=steps)
                self._sync_bitfun(entry, manifest)
                with lock:
                    steps = set_worker_step_status(steps, worker_id, "sync_bitfun", "succeeded")
                    self.store.update_asset_sync_job(sync_job_id, steps=steps)
                self.store.promote_worker_batches_to_queued(run_id=run_id, worker_id=worker_id)
            except Exception as exc:
                with lock:
                    steps = set_worker_step_status(steps, worker_id, "sync_cases", "failed")
                    steps = set_worker_step_status(steps, worker_id, "sync_bitfun", "failed")
                    self.store.update_asset_sync_job(sync_job_id, steps=steps)
                self.store.mark_worker_batches_sync_failed(run_id=run_id, worker_id=worker_id)
                errors.append(f"{worker_id}: {exc}")

        threads = [threading.Thread(target=worker_thread, args=(worker_id,), daemon=True) for worker_id in worker_ids]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        if errors:
            self.store.update_run_rerun_fields(run_id=run_id, rerun_status="failed")
            self.store.update_run_rerun_job(job_id, status="failed", error_text="; ".join(errors), finished=True)
            self.store.update_asset_sync_job(
                sync_job_id,
                status="failed",
                steps=steps,
                error_text="; ".join(errors),
                finished=True,
            )
            return

        self.store.update_run_rerun_fields(run_id=run_id, rerun_status="running")
        self.store.update_run_rerun_job(job_id, status="running")
        self.store.update_asset_sync_job(sync_job_id, status="succeeded", steps=steps, finished=True)
```

Add `from agent_eval_orchestrator.core.ids import new_id` at top of `asset_syncer.py` if not already imported.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/controller/test_asset_syncer_rerun.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/controller/asset_syncer.py tests/controller/test_asset_syncer_rerun.py
git commit -m "feat: add scoped asset sync for exception rerun jobs"
```

---

### Task 6: Rerun API endpoints

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/server.py`
- Create: `tests/controller/test_rerun_exceptions_api.py`

- [ ] **Step 1: Write the failing test**

Create `tests/controller/test_rerun_exceptions_api.py`:

```python
import json
from http.client import HTTPConnection
from threading import Thread
from unittest.mock import patch

from agent_eval_orchestrator.controller.asset_syncer import AssetSyncer
from agent_eval_orchestrator.controller.run_rerun_coordinator import RunRerunCoordinator
from agent_eval_orchestrator.controller.server import Handler, ThreadedServer
from tests.storage.test_rerun_store import _seed_finished_run_with_cases


def start_test_server(store, tmp_path, port):
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text("Host test\n  HostName 127.0.0.1\n  User test\n", encoding="utf-8")
    asset_syncer = AssetSyncer(
        store=store,
        ssh_config_path=ssh_config,
        controller_shared_root=tmp_path,
    )
    coordinator = RunRerunCoordinator(store=store, asset_syncer=asset_syncer)
    server = ThreadedServer(("127.0.0.1", port), Handler)
    Handler.store = store
    Handler.auth_token = "secret"
    Handler.viewer_manager = None
    Handler.provisioner = None
    Handler.worker_updater = None
    Handler.asset_syncer = asset_syncer
    Handler.run_rerun_coordinator = coordinator
    Handler.ssh_config_path = ssh_config
    Handler.controller_shared_root = tmp_path
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_post_rerun_exceptions_happy_path(store, tmp_path):
    run, _ = _seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom"}],
    )
    server = start_test_server(store, tmp_path, 9891)
    conn = HTTPConnection("127.0.0.1", 9891)
    with patch.object(AssetSyncer, "start_rerun_sync_async"):
        conn.request(
            "POST",
            f"/api/runs/{run['run_id']}/rerun-exceptions",
            body="{}",
            headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
        )
        resp = conn.getresponse()
    assert resp.status == 201
    payload = json.loads(resp.read().decode("utf-8"))
    assert payload["exceptionCount"] == 1
    assert payload["rerunStatus"] == "syncing"
    server.shutdown()


def test_post_rerun_exceptions_rejects_active_rerun(store, tmp_path):
    run, _ = _seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom"}],
    )
    store.update_run_rerun_fields(run_id=run["run_id"], rerun_status="running")
    server = start_test_server(store, tmp_path, 9892)
    conn = HTTPConnection("127.0.0.1", 9892)
    conn.request(
        "POST",
        f"/api/runs/{run['run_id']}/rerun-exceptions",
        body="{}",
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    assert resp.status == 409
    server.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/controller/test_rerun_exceptions_api.py -v`
Expected: FAIL — HTTP 404

- [ ] **Step 3: Write minimal implementation**

In `server.py`:

1. Import coordinator:

```python
from agent_eval_orchestrator.controller.run_rerun_coordinator import RunRerunCoordinator, RerunValidationError
```

2. Add class attribute on `Handler`:

```python
    run_rerun_coordinator: RunRerunCoordinator | None = None
```

3. In `do_GET`, before generic `/api/runs/{runId}` handler, add:

```python
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
                for worker_id, batch_id in (job.get("rerun_batches") or {}).items():
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
            _json_response(
                self,
                {
                    "runId": run_id,
                    "rerunStatus": run.get("rerun_status") or "idle",
                    "rerunJobId": run.get("rerun_job_id"),
                    "job": job,
                    "rerunBatches": rerun_batches,
                    "remainingExceptionCount": remaining,
                },
            )
            return
```

4. In `do_POST`, add before fallback 404:

```python
        if path.startswith("/api/runs/") and path.endswith("/rerun-exceptions"):
            if self.run_rerun_coordinator is None:
                _json_response(self, {"error": "rerun coordinator unavailable"}, 500)
                return
            run_id = path.split("/")[3]
            try:
                result = self.run_rerun_coordinator.start_rerun(run_id)
            except RerunValidationError as exc:
                _json_response(self, {"error": exc.message}, exc.code)
                return
            _json_response(self, result, 201)
            return
```

5. Wire in `main()`:

```python
    run_rerun_coordinator = RunRerunCoordinator(store=store, asset_syncer=asset_syncer)
    Handler.run_rerun_coordinator = run_rerun_coordinator
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/controller/test_rerun_exceptions_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/controller/server.py tests/controller/test_rerun_exceptions_api.py
git commit -m "feat: add rerun exceptions API endpoints"
```

---

### Task 7: Heartbeat merge path for exception_rerun batches

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/server.py`
- Modify: `src/agent_eval_orchestrator/storage/store.py`
- Modify: `tests/controller/test_rerun_exceptions_api.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/controller/test_rerun_exceptions_api.py`:

```python
def test_heartbeat_merges_exception_rerun_into_parent(store, tmp_path):
    run, parent = _seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
            {"case_id": "exc-a", "status": "errored", "error_text": "boom"},
        ],
    )
    rerun = store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["exc-a"],
        preferred_worker_id="worker-a",
        batch_options={},
        initial_status="running",
        batch_kind="exception_rerun",
        parent_batch_id=parent["batch_id"],
    )
    store.create_run_rerun_job(
        job_id="rerun-1",
        run_id=run["run_id"],
        case_ids=["exc-a"],
        worker_shards={"worker-a": ["exc-a"]},
        rerun_batches={"worker-a": rerun["batch_id"]},
    )
    store.update_run_rerun_fields(run_id=run["run_id"], rerun_status="running", rerun_job_id="rerun-1")
    server = start_test_server(store, tmp_path, 9893)
    conn = HTTPConnection("127.0.0.1", 9893)
    body = json.dumps(
        {
            "batchId": rerun["batch_id"],
            "workerId": "worker-a",
            "status": "succeeded",
            "finished": True,
            "cases": [
                {
                    "caseId": "exc-a",
                    "status": "succeeded",
                    "score": 1.0,
                    "metrics": {},
                    "artifactIndex": {},
                }
            ],
            "summary": {"succeeded": 1, "failed": 0, "errored": 0, "total": 1},
        }
    )
    conn.request(
        "POST",
        "/api/workers/heartbeat",
        body=body,
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    assert resp.status == 200
    parent_cases = store.list_case_runs(parent["batch_id"])
    by_id = {case["case_id"]: case for case in parent_cases}
    assert by_id["exc-a"]["status"] == "succeeded"
    updated_run = store.get_run(run["run_id"])
    assert updated_run["rerun_status"] == "succeeded"
    server.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/controller/test_rerun_exceptions_api.py::test_heartbeat_merges_exception_rerun_into_parent -v`
Expected: FAIL — parent case still `errored`

- [ ] **Step 3: Write minimal implementation**

Add to `store.py`:

```python
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
```

In `server.py` heartbeat handler, after `update_batch_progress`, add:

```python
            batch_row = self.store.get_batch(str(body["batchId"]))
            if (
                batch_row
                and str(batch_row.get("batch_kind") or "") == "exception_rerun"
                and bool(body.get("finished"))
                and isinstance(body.get("cases"), list)
            ):
                parent_batch_id = str(batch_row.get("parent_batch_id") or "")
                if parent_batch_id:
                    self.store.merge_rerun_cases_into_parent(
                        parent_batch_id=parent_batch_id,
                        rerun_cases=body["cases"],
                        rerun_batch_id=str(body["batchId"]),
                    )
                    parent_batch = self.store.get_batch(parent_batch_id)
                    if parent_batch:
                        parent_job_dir = Path(str(parent_batch["batch_root"])) / "harbor" / "jobs" / parent_batch_id
                        rerun_job_dir = Path(str(batch_row["batch_root"])) / "harbor" / "jobs" / str(body["batchId"])
                        if rerun_job_dir.exists():
                            parent_job_dir.mkdir(parents=True, exist_ok=True)
                            _copy_trial_dirs(rerun_job_dir, parent_job_dir)
                    self.store.finish_rerun_batch_if_complete(rerun_batch_id=str(body["batchId"]))
```

Keep existing sync cleanup branch unchanged; rerun completion must **not** trigger another sync cleanup (spec).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/controller/test_rerun_exceptions_api.py::test_heartbeat_merges_exception_rerun_into_parent -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/controller/server.py src/agent_eval_orchestrator/storage/store.py tests/controller/test_rerun_exceptions_api.py
git commit -m "feat: merge exception rerun results on worker heartbeat"
```

---

### Task 8: Eval task detail extensions

**Files:**
- Modify: `src/agent_eval_orchestrator/storage/store.py` (`get_eval_task_detail`, `list_eval_task_summaries`)
- Modify: `tests/storage/test_rerun_store.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/storage/test_rerun_store.py`:

```python
def test_eval_task_detail_includes_rerun_fields(store):
    run, _ = _seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom"}],
    )
    store.update_run_rerun_fields(run_id=run["run_id"], rerun_status="idle")
    detail = store.get_eval_task_detail(run["run_id"])
    assert detail["canRerunExceptions"] is True
    assert detail["run"]["rerun_status"] == "idle"
    assert detail["batches"][0]["batch_kind"] == "primary"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/storage/test_rerun_store.py::test_eval_task_detail_includes_rerun_fields -v`
Expected: FAIL — `KeyError: 'canRerunExceptions'`

- [ ] **Step 3: Write minimal implementation**

Update `get_eval_task_detail`:

```python
    def get_eval_task_detail(self, run_id: str) -> dict[str, Any] | None:
        run = self.get_run(run_id)
        if not run:
            return None
        template = self.get_task_template(str(run["template_id"]))
        batches = self.list_batches_for_run(run_id)
        primary_batches = [batch for batch in batches if str(batch.get("batch_kind") or "primary") == "primary"]
        worker_groups: dict[str, dict[str, Any]] = {}
        for batch in primary_batches:
            # ... existing worker group logic unchanged ...
            pass
        exception_count = len(self.list_exception_cases_for_run(run_id))
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
            "rerunStatus": rerun_status,
            "rerunJobId": run.get("rerun_job_id"),
        }
```

Update `list_eval_task_summaries` to aggregate case counts from **primary batches only** (filter `batch_kind == "primary"`) so exception_rerun batches do not double-count cases or affect overall run status.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/storage/test_rerun_store.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/storage/store.py tests/storage/test_rerun_store.py
git commit -m "feat: expose rerun fields on eval task detail"
```

---

### Task 9: Task detail UI — 重跑 Exception button & status panel

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/static.py`

- [ ] **Step 1: Manual verification checklist (no automated test)**

Add to `state` init:

```javascript
      rerunPollTimer: null,
```

Add helpers after `syncStatusBadge`:

```javascript
    function rerunStatusBadge(rerunStatus) {
      if (!rerunStatus || rerunStatus === "idle") return "";
      const map = {
        syncing: ["rerun syncing", "warn"],
        running: ["rerun running", "warn"],
        succeeded: ["rerun ok", "ok"],
        failed: ["rerun failed", "bad"],
      };
      const entry = map[rerunStatus] || [rerunStatus, "warn"];
      return badge(entry[0], entry[1]);
    }

    function rerunDisabledReason(detail) {
      if (!detail) return "Run 尚未全部完成";
      const status = String(detail.run?.status || detail.status || "").toLowerCase();
      const primaryFinished = detail.canRerunExceptions !== undefined
        ? detail.canRerunExceptions || detail.rerunStatus === "syncing" || detail.rerunStatus === "running"
        : status === "finished";
      if (!primaryFinished && !detail.canRerunExceptions) return "Run 尚未全部完成";
      if ((detail.exceptionCount || 0) <= 0) return "没有需要重跑的 exception case";
      if (["syncing", "running"].includes(String(detail.rerunStatus || ""))) return "已有重跑任务进行中";
      return "";
    }
```

In `renderTaskDetail()`, add button in actions row:

```javascript
        '<div class="actions" style="margin-bottom:16px">' +
          '<button class="primary" type="button" id="openGlobalViewerBtn">打开 Harbor Viewer</button>' +
          '<button class="secondary" type="button" id="rerunExceptionsBtn">重跑 Exception</button>' +
        '</div>' +
        renderRerunStatusPanel(detail) +
```

Add functions:

```javascript
    function renderRerunStatusPanel(detail) {
      const status = String(detail.rerunStatus || "idle");
      if (status === "idle") return "";
      return '<div class="panel" style="margin-bottom:16px"><div class="panel-body detail">' +
        '<div class="item-title"><strong>Exception 重跑</strong>' + rerunStatusBadge(status) + '</div>' +
        '<div class="subtle">remaining exceptions: ' + esc(detail.exceptionCount ?? 0) + '</div>' +
        (state.rerunJobDetail?.errorText ? '<pre class="error-text">' + esc(state.rerunJobDetail.errorText) + '</pre>' : '') +
      '</div></div>';
    }

    async function pollRerunJob(runId) {
      const detail = await api("/api/runs/" + encodeURIComponent(runId) + "/rerun");
      state.rerunJobDetail = detail;
      if (state.selectedTaskId === runId) {
        await loadTaskDetail(runId);
      }
      if (["succeeded", "failed", "idle"].includes(String(detail.rerunStatus || ""))) {
        clearInterval(state.rerunPollTimer);
        state.rerunPollTimer = null;
        await loadDashboard();
      }
    }

    async function startRerunExceptions(runId, detail) {
      const reason = rerunDisabledReason(detail);
      if (reason) {
        alert(reason);
        return;
      }
      const workerCount = Object.keys(detail.workerGroups || {}).length || 1;
      const msg = "将重跑 " + (detail.exceptionCount || 0) + " 个 exception case，分布在 " + workerCount + " 个 worker。是否继续？";
      if (!confirm(msg)) return;
      await api("/api/runs/" + encodeURIComponent(runId) + "/rerun-exceptions", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: "{}",
      });
      if (state.rerunPollTimer) clearInterval(state.rerunPollTimer);
      state.rerunPollTimer = setInterval(() => pollRerunJob(runId), 2500);
      await pollRerunJob(runId);
    }
```

Wire button in `renderTaskDetail()` after rendering:

```javascript
      const rerunBtn = root.querySelector("#rerunExceptionsBtn");
      if (rerunBtn) {
        const reason = rerunDisabledReason(detail);
        rerunBtn.disabled = Boolean(reason);
        rerunBtn.title = reason || "";
        rerunBtn.addEventListener("click", async () => {
          await startRerunExceptions(run.run_id, detail);
        });
      }
```

- [ ] **Step 2: Run existing tests**

Run: `uv run --extra dev pytest tests/ -q --ignore=tests/e2e`
Expected: PASS (no regressions)

- [ ] **Step 3: Commit**

```bash
git add src/agent_eval_orchestrator/controller/static.py
git commit -m "feat: add exception rerun button and status panel to task detail UI"
```

---

### Task 10: Integration coverage & coordinator edge cases

**Files:**
- Modify: `tests/controller/test_run_rerun_coordinator.py`
- Modify: `tests/controller/test_rerun_exceptions_api.py`

- [ ] **Step 1: Write failing tests**

Add to `test_run_rerun_coordinator.py`:

```python
def test_start_rerun_rejects_no_exceptions(coordinator, store):
    run, _ = _seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "ok", "status": "succeeded", "score": 1.0}],
    )
    with pytest.raises(RerunValidationError) as exc:
        coordinator.start_rerun(run["run_id"])
    assert exc.value.code == 400
```

Add to `test_rerun_exceptions_api.py`:

```python
def test_post_rerun_before_run_finished(store, tmp_path):
    template = store.create_task_template(
        owner="default",
        name="x",
        dataset_ref="/tmp/dataset",
        executor_kind="harbor-docker",
        executor_config={},
    )
    run = store.create_run(template_id=template["template_id"])
    store.register_worker(
        worker_id="worker-a",
        display_name="worker-a",
        host="localhost",
        slots_total=1,
        slots_used=0,
        capabilities={},
    )
    store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["case-a"],
        preferred_worker_id="worker-a",
        batch_options={},
        initial_status="running",
    )
    server = start_test_server(store, tmp_path, 9894)
    conn = HTTPConnection("127.0.0.1", 9894)
    conn.request(
        "POST",
        f"/api/runs/{run['run_id']}/rerun-exceptions",
        body="{}",
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    assert resp.status == 409
    server.shutdown()
```

- [ ] **Step 2: Run tests**

Run: `uv run --extra dev pytest tests/controller/test_run_rerun_coordinator.py tests/controller/test_rerun_exceptions_api.py tests/storage/test_rerun_store.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/controller/test_run_rerun_coordinator.py tests/controller/test_rerun_exceptions_api.py
git commit -m "test: cover rerun validation edge cases"
```

---

## Self-Review

**Spec coverage**

| Requirement | Task |
|-------------|------|
| Run-level exception rerun after completion | Tasks 4, 6, 9 |
| Exception cases only (`_case_is_errored`) | Task 2 |
| Same worker assignment | Task 4 (`preferred_worker_id` from parent) |
| Scoped asset re-sync | Task 5 |
| `exception_rerun` batches + `parent_batch_id` | Tasks 1, 4 |
| One in-progress rerun per run | Tasks 4, 6 |
| Merge overwrite into parent | Tasks 3, 7 |
| Harbor trial dir copy | Task 7 |
| API POST/GET | Task 6 |
| Eval task detail extensions | Task 8 |
| UI button + polling | Task 9 |
| Error codes 400/409 | Tasks 4, 6, 10 |
| Sync failure → `rerun_status=failed` | Task 5 |
| Primary-batch-only terminal check | Task 2 |

**Placeholder scan:** No TBD/TODO steps remain.

**Type consistency:** `batch_kind`, `parent_batch_id`, `rerun_status`, `rerunJobId`, and merge payload keys (`caseId`, `artifactIndex`, `errorText`) match existing heartbeat and store conventions.

---

## Manual Test Plan

1. Finish a Harbor eval run with at least one exception case; open Task detail — **重跑 Exception** enabled.
2. Click button, confirm dialog, observe rerun status `syncing` → `running` → terminal.
3. Verify exception cases in case list update to `succeeded` or `failed`; worker counters refresh.
4. While rerun is active, button disabled with tooltip **已有重跑任务进行中**.
5. If exceptions remain after rerun, start a second rerun after the first completes.
