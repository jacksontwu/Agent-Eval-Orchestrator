# External Harbor Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make AEO Harbor Viewer buttons use a manually started external Harbor Viewer configured by URL, without starting viewer processes.

**Architecture:** Add a small controller-side resolver/checker for `AEO_HARBOR_VIEWER_URL`. Global and batch viewer endpoints will rebuild/validate local job data as before, then return the configured external URL only if `/api/health` is reachable. Existing front-end popup behavior remains unchanged.

**Tech Stack:** Python standard library `os`, `urllib.request`, `urllib.parse`; existing `BaseHTTPRequestHandler` controller; pytest controller tests; `.env.example` documentation.

---

## File Structure

- Modify `src/agent_eval_orchestrator/controller/server.py`
  - Add `resolve_external_harbor_viewer_url()` and `_external_harbor_viewer_response()`.
  - Change `_ensure_global_harbor_viewer()` so it never starts a process.
  - Change `/api/batches/{batchId}/viewer` so it never calls `viewer_manager.ensure_viewer()`.
- Modify `tests/controller/test_global_harbor_viewer_paths.py`
  - Replace the old proxy-session test with external-viewer tests for missing config, healthy config, and unhealthy config.
- Modify `tests/controller/test_rerun_exceptions_api.py`
  - Add one endpoint-level batch viewer test that proves the batch endpoint returns the external URL and does not call `ensure_viewer()`.
- Modify `.env.example`
  - Document `AEO_HARBOR_VIEWER_URL`.

## Task 1: External Viewer URL Resolver And Global Endpoint

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/server.py:404-493`
- Test: `tests/controller/test_global_harbor_viewer_paths.py`

- [ ] **Step 1: Write failing resolver/global tests**

Edit `tests/controller/test_global_harbor_viewer_paths.py`. Remove the unused `Path` and `SimpleNamespace` imports if no longer needed, and replace `test_global_harbor_viewer_uses_proxy_session_for_requested_jobs_dir` with these tests:

```python
def test_global_harbor_viewer_requires_external_url(tmp_path, monkeypatch) -> None:
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    monkeypatch.delenv("AEO_HARBOR_VIEWER_URL", raising=False)

    handler = SimpleNamespace(
        _rebuild_merged_jobs=lambda jobs_dir, run_id=None: None,
    )

    result = Handler._ensure_global_harbor_viewer(handler, str(jobs_dir))

    assert result["available"] is False
    assert "AEO_HARBOR_VIEWER_URL" in result["reason"]
    assert result["jobsDir"] == str(jobs_dir.resolve())
```

Add a healthy-response test:

```python
def test_global_harbor_viewer_returns_configured_external_url(tmp_path, monkeypatch) -> None:
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    monkeypatch.setenv("AEO_HARBOR_VIEWER_URL", "http://viewer.example.test:7369/")
    seen_urls = []

    class HealthyResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(url, timeout):
        seen_urls.append(url)
        return HealthyResponse()

    monkeypatch.setattr(server.request, "urlopen", fake_urlopen)
    handler = SimpleNamespace(
        _rebuild_merged_jobs=lambda jobs_dir, run_id=None: None,
    )

    result = Handler._ensure_global_harbor_viewer(handler, str(jobs_dir), run_id="run-1")

    assert result["available"] is True
    assert result["url"] == "http://viewer.example.test:7369/"
    assert result["embeddedUrl"] == "http://viewer.example.test:7369/"
    assert result["jobsDir"] == str(jobs_dir.resolve())
    assert seen_urls == ["http://viewer.example.test:7369/api/health"]
```

Add an unhealthy-response test:

```python
def test_global_harbor_viewer_reports_unhealthy_external_url(tmp_path, monkeypatch) -> None:
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    monkeypatch.setenv("AEO_HARBOR_VIEWER_URL", "http://127.0.0.1:65530")

    def fake_urlopen(url, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr(server.request, "urlopen", fake_urlopen)
    handler = SimpleNamespace(
        _rebuild_merged_jobs=lambda jobs_dir, run_id=None: None,
    )

    result = Handler._ensure_global_harbor_viewer(handler, str(jobs_dir))

    assert result["available"] is False
    assert "http://127.0.0.1:65530" in result["reason"]
    assert "connection refused" in result["reason"]
```

Keep the existing path resolver tests above these tests.

- [ ] **Step 2: Run the tests and verify they fail for the current behavior**

Run:

```bash
uv run pytest tests/controller/test_global_harbor_viewer_paths.py -q
```

Expected: the new tests fail because `_ensure_global_harbor_viewer()` still starts/proxies a viewer instead of requiring `AEO_HARBOR_VIEWER_URL`.

- [ ] **Step 3: Implement external URL helpers**

In `src/agent_eval_orchestrator/controller/server.py`, add this import near the existing `urllib` imports:

```python
from urllib.parse import parse_qs, urljoin, urlparse
```

Replace the existing `from urllib.parse import parse_qs, urlparse` line with that combined import.

Add these helpers near `resolve_global_harbor_viewer_paths()`:

```python
def resolve_external_harbor_viewer_url() -> str | None:
    raw_url = str(os.environ.get("AEO_HARBOR_VIEWER_URL") or "").strip()
    if not raw_url:
        return None
    parsed = urlparse(raw_url)
    if not parsed.scheme or not parsed.netloc:
        raise RuntimeError("AEO_HARBOR_VIEWER_URL must be a full URL, for example http://127.0.0.1:7369")
    return raw_url.rstrip("/") + "/"


def external_harbor_viewer_health_url(base_url: str) -> str:
    return urljoin(base_url, "api/health")
```

- [ ] **Step 4: Replace global endpoint process startup**

Replace the body of `Handler._ensure_global_harbor_viewer()` with:

```python
    def _ensure_global_harbor_viewer(self, jobs_dir: str | None = None, *, run_id: str | None = None) -> dict[str, object]:
        harbor_repo, jobs_path = resolve_global_harbor_viewer_paths(jobs_dir)
        try:
            jobs_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return {
                "available": False,
                "reason": f"无法访问 Jobs Dir: {jobs_path} ({exc})",
                "jobsDir": str(jobs_path),
                "harborRepo": str(harbor_repo),
            }

        try:
            self._rebuild_merged_jobs(jobs_path, run_id=run_id)
            normalize_jobs_dir(jobs_path)
        except Exception as exc:
            return {
                "available": False,
                "reason": str(exc),
                "jobsDir": str(jobs_path),
                "harborRepo": str(harbor_repo),
            }

        try:
            viewer_url = resolve_external_harbor_viewer_url()
        except RuntimeError as exc:
            return {
                "available": False,
                "reason": str(exc),
                "jobsDir": str(jobs_path),
                "harborRepo": str(harbor_repo),
            }
        if not viewer_url:
            return {
                "available": False,
                "reason": "Harbor Viewer 未配置，请设置 AEO_HARBOR_VIEWER_URL 并手动启动 harbor view",
                "jobsDir": str(jobs_path),
                "harborRepo": str(harbor_repo),
            }

        try:
            with request.urlopen(external_harbor_viewer_health_url(viewer_url), timeout=1):
                return {
                    "available": True,
                    "url": viewer_url,
                    "embeddedUrl": viewer_url,
                    "jobsDir": str(jobs_path),
                    "harborRepo": str(harbor_repo),
                }
        except Exception as exc:
            return {
                "available": False,
                "reason": f"Harbor Viewer 不可用: {viewer_url} ({exc})",
                "jobsDir": str(jobs_path),
                "harborRepo": str(harbor_repo),
            }
```

This removes `subprocess.Popen()` and `viewer_manager.ensure_viewer()` from global viewer handling.

- [ ] **Step 5: Run global tests and verify they pass**

Run:

```bash
uv run pytest tests/controller/test_global_harbor_viewer_paths.py -q
```

Expected: all tests in `test_global_harbor_viewer_paths.py` pass.

- [ ] **Step 6: Commit Task 1**

Run:

```bash
git add src/agent_eval_orchestrator/controller/server.py tests/controller/test_global_harbor_viewer_paths.py
git commit -m "Use external Harbor viewer for global viewer endpoint"
```

## Task 2: Batch Viewer Endpoint Uses External Viewer

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/server.py:1489-1527`
- Test: `tests/controller/test_rerun_exceptions_api.py`

- [ ] **Step 1: Write failing batch endpoint test**

Append this test near the existing job archive/viewer tests in `tests/controller/test_rerun_exceptions_api.py`:

```python
def test_batch_viewer_returns_external_viewer_without_starting_process(store, tmp_path, monkeypatch):
    run, batch = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "case-a", "status": "failed", "score": 0.0}],
    )
    jobs_dir = tmp_path / "harbor" / "jobs"
    job_dir = jobs_dir / sanitize_name(str(run["display_name"]))
    _write_jobs_trial(job_dir, "case-a__new", task_name="case-a")
    store.update_batch_progress(
        batch_id=batch["batch_id"],
        worker_id="worker-a",
        status="succeeded",
        current_step="completed",
        finished=True,
        artifact_index={"jobDir": str(job_dir)},
    )
    monkeypatch.setenv("AEO_HARBOR_VIEWER_URL", "http://viewer.example.test:7369")

    class HealthyResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("agent_eval_orchestrator.controller.server.request.urlopen", lambda url, timeout: HealthyResponse())

    class FailingViewerManager:
        def ensure_viewer(self, *, viewer_id, jobs_dir):
            raise AssertionError("batch viewer endpoint must not start Harbor Viewer")

    server = start_test_server(store, tmp_path, 9908)
    from agent_eval_orchestrator.controller.server import Handler

    Handler.viewer_manager = FailingViewerManager()
    conn = HTTPConnection("127.0.0.1", 9908)
    conn.request(
        "POST",
        f"/api/batches/{batch['batch_id']}/viewer",
        body="{}",
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    payload = json.loads(resp.read().decode("utf-8"))

    assert resp.status == 200
    assert payload["available"] is True
    assert payload["url"] == "http://viewer.example.test:7369/"
    assert payload["embeddedUrl"] == "http://viewer.example.test:7369/"
    server.shutdown()
```

- [ ] **Step 2: Run the batch test and verify it fails for the current behavior**

Run:

```bash
uv run pytest tests/controller/test_rerun_exceptions_api.py::test_batch_viewer_returns_external_viewer_without_starting_process -q
```

Expected: FAIL because the current batch endpoint calls `viewer_manager.ensure_viewer()`.

- [ ] **Step 3: Implement batch endpoint external viewer response**

In `src/agent_eval_orchestrator/controller/server.py`, add this `Handler` method near `_ensure_global_harbor_viewer()`:

```python
    def _external_harbor_viewer_response(
        self,
        *,
        jobs_dir: Path,
        harbor_repo: Path,
    ) -> dict[str, object]:
        try:
            viewer_url = resolve_external_harbor_viewer_url()
        except RuntimeError as exc:
            return {
                "available": False,
                "reason": str(exc),
                "jobsDir": str(jobs_dir),
                "harborRepo": str(harbor_repo),
            }
        if not viewer_url:
            return {
                "available": False,
                "reason": "Harbor Viewer 未配置，请设置 AEO_HARBOR_VIEWER_URL 并手动启动 harbor view",
                "jobsDir": str(jobs_dir),
                "harborRepo": str(harbor_repo),
            }
        try:
            with request.urlopen(external_harbor_viewer_health_url(viewer_url), timeout=1):
                return {
                    "available": True,
                    "url": viewer_url,
                    "embeddedUrl": viewer_url,
                    "jobsDir": str(jobs_dir),
                    "harborRepo": str(harbor_repo),
                }
        except Exception as exc:
            return {
                "available": False,
                "reason": f"Harbor Viewer 不可用: {viewer_url} ({exc})",
                "jobsDir": str(jobs_dir),
                "harborRepo": str(harbor_repo),
            }
```

Then simplify `_ensure_global_harbor_viewer()` by replacing its duplicated external viewer config and health-check block with:

```python
        return self._external_harbor_viewer_response(jobs_dir=jobs_path, harbor_repo=harbor_repo)
```

After the existing `normalize_jobs_dir(jobs_path)` call.

- [ ] **Step 4: Replace batch endpoint `ensure_viewer()` call**

In the `/api/batches/{batchId}/viewer` block, replace this code:

```python
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
```

With:

```python
            jobs_dir = local_job_dir.parent
            normalize_jobs_dir(jobs_dir)
            _json_response(
                self,
                self._external_harbor_viewer_response(
                    jobs_dir=jobs_dir,
                    harbor_repo=resolve_controller_viewer_harbor_repo(),
                ),
            )
            return
```

- [ ] **Step 5: Run batch endpoint test and global tests**

Run:

```bash
uv run pytest tests/controller/test_rerun_exceptions_api.py::test_batch_viewer_returns_external_viewer_without_starting_process tests/controller/test_global_harbor_viewer_paths.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit Task 2**

Run:

```bash
git add src/agent_eval_orchestrator/controller/server.py tests/controller/test_rerun_exceptions_api.py
git commit -m "Use external Harbor viewer for batch viewer endpoint"
```

## Task 3: Configuration Documentation And Final Verification

**Files:**
- Modify: `.env.example`
- Verify: `src/agent_eval_orchestrator/controller/server.py`
- Verify: `tests/controller/test_global_harbor_viewer_paths.py`
- Verify: `tests/controller/test_rerun_exceptions_api.py`

- [ ] **Step 1: Document the environment variable**

Append this line to `.env.example` after `AEO_SSH_CONFIG=~/.ssh/config`:

```bash
# Optional: external Harbor Viewer that AEO opens but does not start.
# AEO_HARBOR_VIEWER_URL=http://127.0.0.1:7369
```

- [ ] **Step 2: Search for remaining automatic process starts on request paths**

Run:

```bash
rg -n "ensure_viewer|global_viewer_process|subprocess.Popen|uv run harbor view" src/agent_eval_orchestrator/controller
```

Expected:

- `HarborViewerManager.ensure_viewer()` may still exist in `harbor_viewer.py`.
- `subprocess.Popen` may still exist in `harbor_viewer.py`.
- `server.py` request handlers must not call `viewer_manager.ensure_viewer()`.
- `server.py` must not call `subprocess.Popen()` for Harbor Viewer.

If `server.py` still starts Harbor Viewer from `_ensure_global_harbor_viewer()` or `/api/batches/{batchId}/viewer`, remove that code and rerun Task 1 and Task 2 tests.

- [ ] **Step 3: Run focused tests**

Run:

```bash
uv run pytest tests/controller/test_global_harbor_viewer_paths.py tests/controller/test_rerun_exceptions_api.py::test_batch_viewer_returns_external_viewer_without_starting_process -q
```

Expected: all selected tests pass.

- [ ] **Step 4: Check working tree**

Run:

```bash
git status --short
```

Expected: only `.env.example` is modified for this task. Existing unrelated untracked directories such as `frontend/` or `runtime-v2/` may still appear and must not be committed.

- [ ] **Step 5: Commit Task 3**

Run:

```bash
git add .env.example
git commit -m "Document external Harbor viewer configuration"
```

## Final Verification

- [ ] **Step 1: Run all focused Harbor viewer tests**

Run:

```bash
uv run pytest tests/controller/test_global_harbor_viewer_paths.py tests/controller/test_rerun_exceptions_api.py::test_batch_viewer_returns_external_viewer_without_starting_process -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Confirm no request path starts Harbor Viewer**

Run:

```bash
rg -n "viewer_manager\\.ensure_viewer|global_viewer_process|uv run harbor view|subprocess\\.Popen" src/agent_eval_orchestrator/controller/server.py
```

Expected: no output.

- [ ] **Step 3: Confirm git history**

Run:

```bash
git log --oneline -3
git status --short
```

Expected: three implementation commits above the plan/spec commits. `git status --short` may show unrelated untracked `frontend/` and `runtime-v2/`, but no uncommitted tracked implementation files.
