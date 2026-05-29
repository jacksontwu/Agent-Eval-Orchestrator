# Exception Type Display and Selective Rerun Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show concrete Harbor exception types on case cards and let operators rerun a multi-selected subset of exception types at task scope via the existing rerun config modal (default all types checked).

**Architecture:** Add `Store.case_error_type()` and `summarize_exception_types_for_run()` over already-normalized `metrics_json.errorType` data. Extend `RunRerunCoordinator.start_rerun()` to filter exception cases before worker sharding; persist `selected_error_types_json` on `run_rerun_jobs`. Extend embedded dashboard JS to show type badges and a type checklist in `rerunConfigModal`.

**Tech Stack:** Python 3.10+, stdlib (`sqlite3`, `http.server`), embedded HTML/JS in `static.py`, pytest

**Spec:** [docs/superpowers/specs/2026-05-29-exception-type-rerun-design.md](../specs/2026-05-29-exception-type-rerun-design.md)

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/agent_eval_orchestrator/storage/store.py` | `case_error_type()`, type summary, filter helper, schema migration, `get_eval_task_detail()` extension, `create_run_rerun_job()` audit field |
| `src/agent_eval_orchestrator/controller/run_rerun_coordinator.py` | Parse `selectedErrorTypes`, validate, filter exceptions before batch creation |
| `src/agent_eval_orchestrator/controller/server.py` | No handler change expected (body passed through); verify only |
| `src/agent_eval_orchestrator/controller/static.py` | Case badge, rerun modal type checklist, POST payload |
| `tests/storage/test_exception_type_store.py` | New — type extraction, summary, eval detail |
| `tests/controller/test_run_rerun_coordinator.py` | Extend — type filter branches |
| `tests/controller/test_rerun_exceptions_api.py` | Extend — API subset + validation |

---

### Task 1: `case_error_type()` helper

**Files:**
- Modify: `src/agent_eval_orchestrator/storage/store.py` (near `_case_is_errored`)
- Create: `tests/storage/test_exception_type_store.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/storage/test_exception_type_store.py`:

```python
from agent_eval_orchestrator.storage.store import Store


def test_case_error_type_from_metrics():
    case = {"metrics": {"errorType": "TimeoutError"}}
    assert Store.case_error_type(case) == "TimeoutError"


def test_case_error_type_top_level_field():
    case = {"errorType": "RewardFileNotFoundError", "metrics": {}}
    assert Store.case_error_type(case) == "RewardFileNotFoundError"


def test_case_error_type_missing_returns_unknown():
    assert Store.case_error_type({"metrics": {}}) == "(unknown)"
    assert Store.case_error_type({"metrics": {"errorType": ""}}) == "(unknown)"
    assert Store.case_error_type({"metrics": {"errorType": "   "}}) == "(unknown)"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/storage/test_exception_type_store.py -v`

Expected: FAIL with `AttributeError: type object 'Store' has no attribute 'case_error_type'`

- [ ] **Step 3: Write minimal implementation**

In `src/agent_eval_orchestrator/storage/store.py`, add after `_case_is_failed`:

```python
    @staticmethod
    def case_error_type(case: dict[str, Any]) -> str:
        metrics = case.get("metrics") or {}
        raw = case.get("errorType") or metrics.get("errorType")
        if raw is None or str(raw).strip() == "":
            return "(unknown)"
        return str(raw).strip()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/storage/test_exception_type_store.py -v`

Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/storage/store.py tests/storage/test_exception_type_store.py
git commit -m "feat: add Store.case_error_type helper for exception classification"
```

---

### Task 2: Exception type summary and filtering

**Files:**
- Modify: `src/agent_eval_orchestrator/storage/store.py`
- Modify: `tests/storage/test_exception_type_store.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/storage/test_exception_type_store.py`:

```python
from conftest import seed_finished_run_with_cases


def test_summarize_exception_types_for_run(store):
    run, _ = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
            {"case_id": "exc-a", "status": "errored", "error_text": "a", "metrics": {"errorType": "TimeoutError"}},
            {"case_id": "exc-b", "status": "errored", "error_text": "b", "metrics": {"errorType": "TimeoutError"}},
            {"case_id": "exc-c", "status": "errored", "error_text": "c", "metrics": {"errorType": "RewardFileNotFoundError"}},
            {"case_id": "exc-d", "status": "errored", "error_text": "d", "metrics": {}},
        ],
    )
    summary = store.summarize_exception_types_for_run(run["run_id"])
    assert summary["total"] == 4
    assert summary["byType"] == [
        {"errorType": "TimeoutError", "count": 2},
        {"errorType": "RewardFileNotFoundError", "count": 1},
        {"errorType": "(unknown)", "count": 1},
    ]


def test_summarize_exception_types_empty_run(store):
    template = store.create_task_template(
        owner="default",
        name="empty",
        dataset_ref="/tmp/dataset",
        executor_kind="harbor-docker",
        executor_config={},
        model_profile_ref=None,
        note="",
    )
    run = store.create_run(template_id=template["template_id"])
    summary = store.summarize_exception_types_for_run(run["run_id"])
    assert summary == {"total": 0, "byType": []}


def test_filter_exception_cases_by_types(store):
    run, _ = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "a", "metrics": {"errorType": "TimeoutError"}},
            {"case_id": "exc-b", "status": "errored", "error_text": "b", "metrics": {"errorType": "OtherError"}},
        ],
    )
    filtered = store.filter_exception_cases_by_types(
        run["run_id"],
        ["TimeoutError"],
    )
    assert [item["case_id"] for item in filtered] == ["exc-a"]

    all_items = store.filter_exception_cases_by_types(run["run_id"], None)
    assert len(all_items) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/storage/test_exception_type_store.py::test_summarize_exception_types_for_run -v`

Expected: FAIL with `AttributeError: 'Store' object has no attribute 'summarize_exception_types_for_run'`

- [ ] **Step 3: Write minimal implementation**

Add to `Store` in `store.py`:

```python
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
```

Refactor existing `group_exception_cases_by_worker` to reuse the helper:

```python
    def group_exception_cases_by_worker(self, run_id: str) -> dict[str, list[dict[str, Any]]]:
        return self.group_exception_items_by_worker(self.list_exception_cases_for_run(run_id))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/storage/test_exception_type_store.py -v`

Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/storage/store.py tests/storage/test_exception_type_store.py
git commit -m "feat: summarize and filter exception cases by error type"
```

---

### Task 3: Expose `exceptionSummary` on eval task detail

**Files:**
- Modify: `src/agent_eval_orchestrator/storage/store.py` (`get_eval_task_detail`)
- Modify: `tests/storage/test_exception_type_store.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/storage/test_exception_type_store.py`:

```python
def test_eval_task_detail_includes_exception_summary(store):
    run, _ = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "a", "metrics": {"errorType": "TimeoutError"}},
            {"case_id": "exc-b", "status": "errored", "error_text": "b", "metrics": {"errorType": "OtherError"}},
        ],
    )
    detail = store.get_eval_task_detail(run["run_id"])
    assert detail["exceptionSummary"]["total"] == 2
    assert {entry["errorType"] for entry in detail["exceptionSummary"]["byType"]} == {
        "TimeoutError",
        "OtherError",
    }
    assert detail["exceptionCount"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/storage/test_exception_type_store.py::test_eval_task_detail_includes_exception_summary -v`

Expected: FAIL with `KeyError: 'exceptionSummary'`

- [ ] **Step 3: Write minimal implementation**

In `get_eval_task_detail()`, before the return dict, add:

```python
        exception_summary = self.summarize_exception_types_for_run(run_id)
```

Extend the return dict:

```python
        return {
            "run": run,
            "template": template,
            "batches": batches,
            "workerGroups": worker_group_list,
            "exceptionCount": exception_count,
            "exceptionSummary": exception_summary,
            "canRerunExceptions": can_rerun,
            "rerunStatus": rerun_status,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/storage/test_exception_type_store.py tests/storage/test_rerun_store.py::test_eval_task_detail_includes_rerun_fields -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/storage/store.py tests/storage/test_exception_type_store.py
git commit -m "feat: expose exceptionSummary on eval task detail"
```

---

### Task 4: Persist `selected_error_types` on rerun jobs

**Files:**
- Modify: `src/agent_eval_orchestrator/storage/store.py` (migration + CRUD)
- Modify: `tests/storage/test_rerun_store.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/storage/test_rerun_store.py`:

```python
def test_create_run_rerun_job_persists_selected_error_types(store):
    template = store.create_task_template(
        owner="default",
        name="rerun-test",
        dataset_ref="/tmp/dataset",
        executor_kind="harbor-docker",
        executor_config={},
        model_profile_ref=None,
        note="",
    )
    run = store.create_run(template_id=template["template_id"], display_name="rerun-run")
    job_id = new_id("rerun")
    job = store.create_run_rerun_job(
        job_id=job_id,
        run_id=run["run_id"],
        case_ids=["case-a"],
        worker_shards={"worker-a": ["case-a"]},
        rerun_batches={"worker-a": "batch-rerun-a"},
        selected_error_types=["TimeoutError", "OtherError"],
    )
    assert job["selected_error_types"] == ["TimeoutError", "OtherError"]
    fetched = store.get_run_rerun_job(job_id)
    assert fetched["selected_error_types"] == ["TimeoutError", "OtherError"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/storage/test_rerun_store.py::test_create_run_rerun_job_persists_selected_error_types -v`

Expected: FAIL — unexpected keyword argument `selected_error_types`

- [ ] **Step 3: Write minimal implementation**

In `_init_db` migration block (after `run_rerun_jobs` CREATE TABLE), add column migration:

```python
            rerun_job_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(run_rerun_jobs)").fetchall()
            }
            if "selected_error_types_json" not in rerun_job_columns:
                conn.execute(
                    "ALTER TABLE run_rerun_jobs ADD COLUMN selected_error_types_json TEXT"
                )
```

Update `create_run_rerun_job` signature and INSERT:

```python
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
        ...
                INSERT INTO run_rerun_jobs(
                    job_id, run_id, status, sync_job_id,
                    case_ids_json, worker_shards_json, rerun_batches_json,
                    selected_error_types_json,
                    error_text, created_at, finished_at
                ) VALUES(?, ?, 'pending', NULL, ?, ?, ?, ?, NULL, ?, NULL)
        ...
                    json.dumps(selected_error_types or [], ensure_ascii=False),
```

Update `_run_rerun_job_item`:

```python
        selected_raw = item.pop("selected_error_types_json", None)
        item["selected_error_types"] = json.loads(selected_raw) if selected_raw else []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/storage/test_rerun_store.py::test_create_run_rerun_job_persists_selected_error_types tests/storage/test_rerun_store.py::test_rerun_schema_and_crud -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/storage/store.py tests/storage/test_rerun_store.py
git commit -m "feat: persist selected error types on run rerun jobs"
```

---

### Task 5: Coordinator type filtering and validation

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/run_rerun_coordinator.py`
- Modify: `tests/controller/test_run_rerun_coordinator.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/controller/test_run_rerun_coordinator.py`:

```python
def test_start_rerun_filters_by_selected_error_types(coordinator, store):
    run, parent = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "a", "metrics": {"errorType": "TimeoutError"}},
            {"case_id": "exc-b", "status": "errored", "error_text": "b", "metrics": {"errorType": "OtherError"}},
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
        ],
    )
    result = coordinator.start_rerun(
        run["run_id"],
        config={"selectedErrorTypes": ["TimeoutError"]},
    )
    assert result["exceptionCount"] == 1
    assert result["selectedErrorTypes"] == ["TimeoutError"]
    job = store.get_run_rerun_job(result["rerunJobId"])
    assert job["selected_error_types"] == ["TimeoutError"]
    rerun_batch = store.get_batch(job["rerun_batches"]["worker-a"])
    assert rerun_batch["selected_case_ids"] == ["exc-a"]


def test_start_rerun_rejects_empty_selected_error_types(coordinator, store):
    run, _ = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom"}],
    )
    with pytest.raises(RerunValidationError) as exc:
        coordinator.start_rerun(run["run_id"], config={"selectedErrorTypes": []})
    assert exc.value.code == 400
    assert "at least one" in exc.value.message.lower()


def test_start_rerun_rejects_unknown_error_type(coordinator, store):
    run, _ = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom", "metrics": {"errorType": "TimeoutError"}}],
    )
    with pytest.raises(RerunValidationError) as exc:
        coordinator.start_rerun(run["run_id"], config={"selectedErrorTypes": ["DoesNotExist"]})
    assert exc.value.code == 400
    assert "invalid error type" in exc.value.message.lower()


def test_start_rerun_omitted_types_reruns_all(coordinator, store):
    run, _ = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "a", "metrics": {"errorType": "TimeoutError"}},
            {"case_id": "exc-b", "status": "errored", "error_text": "b", "metrics": {"errorType": "OtherError"}},
        ],
    )
    result = coordinator.start_rerun(run["run_id"], config={})
    assert result["exceptionCount"] == 2
    assert set(result["selectedErrorTypes"]) == {"TimeoutError", "OtherError"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/controller/test_run_rerun_coordinator.py::test_start_rerun_filters_by_selected_error_types -v`

Expected: FAIL — `exceptionCount` is 2 instead of 1, or missing `selectedErrorTypes`

- [ ] **Step 3: Write minimal implementation**

Add module-level constant in `run_rerun_coordinator.py`:

```python
RERUN_CONFIG_KEYS = ("datasetPath", "bitfunCliPath", "bitfunConfigDir", "jobsDir", "executorConfig")
RERUN_SCOPE_KEYS = ("selectedErrorTypes",)
```

Add helper methods to `RunRerunCoordinator`:

```python
    def _resolve_selected_error_types(
        self,
        *,
        run_id: str,
        config: dict[str, Any] | None,
    ) -> list[str]:
        summary = self.store.summarize_exception_types_for_run(run_id)
        available = [entry["errorType"] for entry in summary["byType"]]
        if not available:
            return []
        raw = (config or {}).get("selectedErrorTypes")
        if raw is None:
            return available
        if not isinstance(raw, list):
            raise RerunValidationError(400, "selectedErrorTypes must be an array")
        selected = [str(item).strip() for item in raw if str(item).strip()]
        if not selected:
            raise RerunValidationError(400, "at least one error type required")
        available_set = set(available)
        invalid = sorted({item for item in selected if item not in available_set})
        if invalid:
            raise RerunValidationError(400, f"invalid error type(s): {', '.join(invalid)}")
        return selected

    def _filter_config_for_assets(self, config: dict[str, Any] | None) -> dict[str, Any] | None:
        if not config:
            return None
        filtered = {
            key: value
            for key, value in config.items()
            if key in RERUN_CONFIG_KEYS
        }
        return filtered or None
```

Replace the start of `start_rerun()` after rerun-in-progress check:

```python
        selected_error_types = self._resolve_selected_error_types(run_id=run_id, config=config)
        if not selected_error_types:
            raise RerunValidationError(400, "no exception cases")

        filtered_items = self.store.filter_exception_cases_by_types(run_id, selected_error_types)
        if not filtered_items:
            raise RerunValidationError(400, "no matching exception cases")

        grouped = self.store.group_exception_items_by_worker(filtered_items)
        if not grouped:
            raise RerunValidationError(400, "no exception cases")
```

Replace `config_supplied` / `_apply_config` to use asset-only config:

```python
        asset_config = self._filter_config_for_assets(dict(config or {}))
        config_supplied = self._has_applicable_config(asset_config)
        ...
            rerun_concurrency = self._apply_config(
                run=run,
                config=dict(asset_config or {}),
                ...
            )
```

Pass `selected_error_types` to job creation and response:

```python
        self.store.create_run_rerun_job(
            ...
            selected_error_types=selected_error_types,
        )
        return {
            ...
            "exceptionCount": len(all_case_ids),
            "selectedErrorTypes": selected_error_types,
            ...
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/controller/test_run_rerun_coordinator.py -v`

Expected: PASS (all coordinator tests)

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/controller/run_rerun_coordinator.py tests/controller/test_run_rerun_coordinator.py
git commit -m "feat: filter exception rerun by selected error types"
```

---

### Task 6: API integration tests

**Files:**
- Modify: `tests/controller/test_rerun_exceptions_api.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/controller/test_rerun_exceptions_api.py`:

```python
def test_post_rerun_exceptions_filters_by_selected_error_types(store, tmp_path):
    run, _ = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "a", "metrics": {"errorType": "TimeoutError"}},
            {"case_id": "exc-b", "status": "errored", "error_text": "b", "metrics": {"errorType": "OtherError"}},
        ],
    )
    server = start_test_server(store, tmp_path, 9896)
    conn = HTTPConnection("127.0.0.1", 9896)
    with patch.object(AssetSyncer, "start_rerun_sync_async"):
        conn.request(
            "POST",
            f"/api/runs/{run['run_id']}/rerun-exceptions",
            body=json.dumps({"selectedErrorTypes": ["TimeoutError"]}),
            headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
        )
        resp = conn.getresponse()
    assert resp.status == 201
    payload = json.loads(resp.read().decode("utf-8"))
    assert payload["exceptionCount"] == 1
    assert payload["selectedErrorTypes"] == ["TimeoutError"]
    server.shutdown()


def test_post_rerun_exceptions_rejects_empty_selected_error_types(store, tmp_path):
    run, _ = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom"}],
    )
    server = start_test_server(store, tmp_path, 9897)
    conn = HTTPConnection("127.0.0.1", 9897)
    conn.request(
        "POST",
        f"/api/runs/{run['run_id']}/rerun-exceptions",
        body=json.dumps({"selectedErrorTypes": []}),
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    assert resp.status == 400
    server.shutdown()
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/controller/test_rerun_exceptions_api.py::test_post_rerun_exceptions_filters_by_selected_error_types tests/controller/test_rerun_exceptions_api.py::test_post_rerun_exceptions_rejects_empty_selected_error_types -v`

Expected: PASS (coordinator wired through existing handler)

- [ ] **Step 3: Run full related test suite**

Run: `pytest tests/storage/test_exception_type_store.py tests/storage/test_rerun_store.py tests/controller/test_run_rerun_coordinator.py tests/controller/test_rerun_exceptions_api.py -v`

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add tests/controller/test_rerun_exceptions_api.py
git commit -m "test: cover selective exception rerun API"
```

---

### Task 7: Case card exception type badge

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/static.py`

- [ ] **Step 1: Update `caseErrorType` to use `(unknown)` sentinel**

Find `function caseErrorType(item)` (~line 749) and replace:

```javascript
    function caseErrorType(item) {
      const raw = item.errorType || (item.metrics && item.metrics.errorType);
      if (raw == null || String(raw).trim() === "") return "(unknown)";
      return String(raw).trim();
    }
```

- [ ] **Step 2: Add `exceptionStatusBadge` and wire into `caseStatusBadge`**

Replace `caseStatusBadge`:

```javascript
    function exceptionStatusBadge(item) {
      const errorType = caseErrorType(item);
      const cls = errorType === "(unknown)" ? "warn" : "err";
      const title = ' title="' + esc(errorType) + '"';
      return '<span class="badge ' + cls + ' case-error-badge"' + title + '>' + esc(errorType) + "</span>";
    }

    function caseStatusBadge(item) {
      if (caseIsErrored(item)) {
        return exceptionStatusBadge(item);
      }
      return badge(item.status);
    }
```

- [ ] **Step 3: Add CSS for long type names**

In the `<style>` block (~line 290, after `.badge` rules), add:

```css
    .case-error-badge {
      max-width: 160px;
      overflow: hidden;
      text-overflow: ellipsis;
    }
```

- [ ] **Step 4: Manual smoke check**

Run controller locally, open a task with mixed exception types, confirm cards show distinct badges (not generic `exception`).

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/controller/static.py
git commit -m "feat: show concrete exception type on case cards"
```

---

### Task 8: Rerun modal exception type multi-select

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/static.py`

- [ ] **Step 1: Extend rerun modal state in `openRerunConfigModal`**

Update `openRerunConfigModal(detail)` (~line 1513):

```javascript
    function openRerunConfigModal(detail) {
      const byType = (detail.exceptionSummary && detail.exceptionSummary.byType) || [];
      const defaultSelected = byType.map(entry => entry.errorType);
      state.rerunConfig = {
        runId: detail.run.run_id,
        detail,
        defaults: buildRerunFormDefaults(detail),
        selectedErrorTypes: defaultSelected,
        error: "",
        submitting: false,
      };
      renderRerunConfigModal();
      document.getElementById("rerunConfigModal").classList.remove("hidden");
    }
```

- [ ] **Step 2: Add helper functions for selected counts**

Insert before `renderRerunConfigModal`:

```javascript
    function rerunSelectedCaseCount(detail, selectedTypes) {
      const byType = (detail.exceptionSummary && detail.exceptionSummary.byType) || [];
      const selected = new Set(selectedTypes || []);
      return byType.reduce((sum, entry) => (
        selected.has(entry.errorType) ? sum + Number(entry.count || 0) : sum
      ), 0);
    }

    function renderRerunTypeSelectionHtml(modalState) {
      const detail = modalState.detail;
      const byType = (detail.exceptionSummary && detail.exceptionSummary.byType) || [];
      const selected = new Set(modalState.selectedErrorTypes || []);
      const total = Number((detail.exceptionSummary && detail.exceptionSummary.total) || detail.exceptionCount || 0);
      const selectedCount = rerunSelectedCaseCount(detail, modalState.selectedErrorTypes);
      const rows = byType.map(entry => {
        const checked = selected.has(entry.errorType) ? " checked" : "";
        return '' +
          '<label style="display:flex;gap:8px;align-items:center;margin-bottom:6px">' +
            '<input type="checkbox" name="rerunErrorType" value="' + esc(entry.errorType) + '"' + checked + ' />' +
            '<span><code>' + esc(entry.errorType) + '</code> (' + esc(entry.count) + ')</span>' +
          '</label>';
      }).join("") || '<div class="empty">暂无 exception 类型</div>';
      return '' +
        '<div style="margin-bottom:16px">' +
          '<div class="item-title"><strong>Exception 类型（Task 级）</strong></div>' +
          '<div class="subtle" style="margin-bottom:8px">默认全选；取消不需要的类型后确认重跑</div>' +
          '<div class="actions" style="margin-bottom:8px">' +
            '<button class="ghost" type="button" id="rerunSelectAllTypesBtn">全选</button>' +
            '<button class="ghost" type="button" id="rerunClearAllTypesBtn">全不选</button>' +
          '</div>' +
          rows +
          '<div class="subtle" style="margin-top:8px">已选 ' + esc(selectedCount) + ' / 共 ' + esc(total) + ' cases</div>' +
        '</div>';
    }
```

- [ ] **Step 3: Update `renderRerunConfigModal` body**

At the top of `body.innerHTML` concatenation (~line 1537), prepend `renderRerunTypeSelectionHtml(modalState) +`.

Update subtitle:

```javascript
      const selectedCount = rerunSelectedCaseCount(detail, modalState.selectedErrorTypes);
      const total = Number((detail.exceptionSummary && detail.exceptionSummary.total) || detail.exceptionCount || 0);
      document.getElementById("rerunConfigModalSubtitle").textContent =
        (detail.run?.display_name || "-") + " · 已选 " + selectedCount + " / exception: " + total;
```

Disable submit when nothing selected:

```javascript
      const submitDisabled = modalState.submitting || selectedCount <= 0;
      ...
            '<button class="primary" type="submit"' + (submitDisabled ? ' disabled' : '') + '>' + submitLabel + '</button>' +
```

After setting `body.innerHTML`, bind type checkbox handlers:

```javascript
      body.querySelectorAll('input[name="rerunErrorType"]').forEach(input => {
        input.addEventListener("change", () => {
          modalState.selectedErrorTypes = Array.from(
            body.querySelectorAll('input[name="rerunErrorType"]:checked')
          ).map(el => el.value);
          renderRerunConfigModal();
        });
      });
      const selectAllBtn = document.getElementById("rerunSelectAllTypesBtn");
      if (selectAllBtn) {
        selectAllBtn.addEventListener("click", () => {
          modalState.selectedErrorTypes = (detail.exceptionSummary?.byType || []).map(entry => entry.errorType);
          renderRerunConfigModal();
        });
      }
      const clearAllBtn = document.getElementById("rerunClearAllTypesBtn");
      if (clearAllBtn) {
        clearAllBtn.addEventListener("click", () => {
          modalState.selectedErrorTypes = [];
          renderRerunConfigModal();
        });
      }
```

- [ ] **Step 4: Include `selectedErrorTypes` in POST body**

In `submitRerunConfigForm` (~line 1594), extend the API call:

```javascript
        await api("/api/runs/" + encodeURIComponent(modalState.runId) + "/rerun-exceptions", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            ...payload,
            selectedErrorTypes: modalState.selectedErrorTypes || [],
          }),
        });
```

Add client-side guard before submit:

```javascript
      if (!modalState.selectedErrorTypes || !modalState.selectedErrorTypes.length) {
        modalState.error = "请至少选择一种 exception 类型";
        renderRerunConfigModal();
        return;
      }
```

- [ ] **Step 5: Manual smoke check**

1. Open task with ≥2 exception types.
2. Click **重跑 Exception** — all types checked, footer shows full count.
3. Uncheck one type — footer count drops, subtitle updates.
4. **全不选** disables confirm; **全选** restores.
5. Confirm rerun — network POST includes `selectedErrorTypes` array.

- [ ] **Step 6: Commit**

```bash
git add src/agent_eval_orchestrator/controller/static.py
git commit -m "feat: add exception type multi-select to rerun config modal"
```

---

## Final Verification

- [ ] Run full test suite: `pytest -q`

Expected: all tests pass

- [ ] Manual checklist (from spec):

1. Task with multiple exception types shows distinct badges on case cards.
2. Modal opens with all types checked; deselecting one type reduces selected count.
3. Confirm rerun only queues cases of selected types on correct workers.

---

## Spec Coverage Self-Review

| Spec requirement | Task |
|------------------|------|
| `case_error_type()` with `(unknown)` sentinel | Task 1 |
| `summarize_exception_types_for_run()` | Task 2 |
| `exceptionSummary` on eval task detail | Task 3 |
| `selectedErrorTypes` API filter + validation | Tasks 5–6 |
| `selected_error_types_json` persistence | Task 4 |
| Case card badge shows concrete type | Task 7 |
| Modal type multi-select, default all checked | Task 8 |
| Backward compat: omitted types = all | Task 5 (`test_start_rerun_omitted_types_reruns_all`) |
| Empty array → 400 | Tasks 5–6 |
| Invalid type → 400 | Task 5 |
| Filtered `exceptionCount` in response | Tasks 5–6 |

No placeholder steps remain. Type names (`case_error_type`, `selectedErrorTypes`, `exceptionSummary`) are consistent across tasks.
