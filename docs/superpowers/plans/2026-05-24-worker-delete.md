# Worker Delete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a synchronous **Delete Worker** API and dashboard flow that hard-deletes worker rows, optionally stops remote daemon/tunnel via SSH, blocks delete when active batches exist, and allows `worker_id` reuse.

**Architecture:** Extract shared batch-to-worker assignment logic in `Store`, add `worker_has_active_batches` and `delete_worker`. Refactor tunnel kill + remote `pkill` from `Provisioner.cancel_job` into `decommission_worker`. Expose `DELETE /api/workers/{workerId}` in `server.py`. Add delete button + confirmation modal in `static.py`.

**Tech Stack:** Python 3.10+, stdlib (`http.server`, `sqlite3`, `subprocess`), embedded HTML/JS dashboard, pytest

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/agent_eval_orchestrator/storage/store.py` | `_batch_target_worker_id`, `worker_has_active_batches`, `delete_worker`; refactor `list_worker_runtime_status` |
| `src/agent_eval_orchestrator/controller/provisioner.py` | `decommission_worker`; refactor `cancel_job` to call it |
| `src/agent_eval_orchestrator/controller/server.py` | `do_DELETE` handler for `DELETE /api/workers/{workerId}` |
| `src/agent_eval_orchestrator/controller/static.py` | Delete button, confirmation modal, post-delete reload + feedback |
| `tests/storage/test_worker_delete_store.py` | Store-level unit tests for active-batch detection and hard delete |
| `tests/controller/test_provisioner_decommission.py` | Unit tests for `decommission_worker` |
| `tests/controller/test_delete_worker_api.py` | HTTP integration tests covering full spec test matrix |

No schema migration required.

---

### Task 1: Batch assignment helper + `worker_has_active_batches`

**Files:**
- Modify: `src/agent_eval_orchestrator/storage/store.py:700-754`
- Create: `tests/storage/test_worker_delete_store.py`

- [ ] **Step 1: Write the failing test**

Create `tests/storage/test_worker_delete_store.py`:

```python
from agent_eval_orchestrator.core.ids import new_id


def _seed_template_and_worker(store, worker_id: str = "ecs-worker-del"):
    store.register_worker(
        worker_id=worker_id,
        display_name=worker_id,
        host="10.0.0.1",
        slots_total=2,
        slots_used=0,
        capabilities={},
    )
    template = store.create_task_template(
        owner="default",
        name="delete-test",
        dataset_ref="/tmp/dataset",
        executor_kind="harbor",
        executor_config={"jobsDir": "/tmp/jobs"},
        model_profile_ref=None,
        note="",
    )
    return template


def test_worker_has_active_batches_empty(store):
    _seed_template_and_worker(store)
    counts = store.worker_has_active_batches("ecs-worker-del")
    assert counts == {"runningCount": 0, "queuedCount": 0}


def test_worker_has_active_batches_queued(store):
    template = _seed_template_and_worker(store)
    run = store.create_run(template_id=template["template_id"])
    store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["case-a"],
        preferred_worker_id="ecs-worker-del",
        batch_options={},
    )
    counts = store.worker_has_active_batches("ecs-worker-del")
    assert counts == {"runningCount": 0, "queuedCount": 1}


def test_worker_has_active_batches_running(store):
    template = _seed_template_and_worker(store)
    run = store.create_run(template_id=template["template_id"])
    batch = store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["case-a"],
        preferred_worker_id="ecs-worker-del",
        batch_options={},
    )
    with store.connect() as conn:
        conn.execute(
            """
            UPDATE batches
            SET status = 'running', assigned_worker_id = ?, current_step = 'executor-starting'
            WHERE batch_id = ?
            """,
            ("ecs-worker-del", batch["batch_id"]),
        )
    counts = store.worker_has_active_batches("ecs-worker-del")
    assert counts == {"runningCount": 1, "queuedCount": 0}


def test_worker_has_active_batches_uses_bound_worker_id(store):
    template = _seed_template_and_worker(store)
    run = store.create_run(template_id=template["template_id"])
    with store.connect() as conn:
        conn.execute(
            "UPDATE runs SET bound_worker_id = ? WHERE run_id = ?",
            ("ecs-worker-del", run["run_id"]),
        )
    store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["case-a"],
        preferred_worker_id=None,
        batch_options={},
    )
    counts = store.worker_has_active_batches("ecs-worker-del")
    assert counts == {"runningCount": 0, "queuedCount": 1}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/djn/code/Agent-Eval-Orchestrator && uv run --extra dev pytest tests/storage/test_worker_delete_store.py -v`
Expected: FAIL with `AttributeError: 'Store' object has no attribute 'worker_has_active_batches'`

- [ ] **Step 3: Write minimal implementation**

In `src/agent_eval_orchestrator/storage/store.py`, add before `list_worker_runtime_status`:

```python
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
```

Refactor `list_worker_runtime_status` to use the helper — replace lines 742-744:

```python
            target_worker = self._batch_target_worker_id(batch, run)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/storage/test_worker_delete_store.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/storage/store.py tests/storage/test_worker_delete_store.py
git commit -m "feat: add worker_has_active_batches with shared batch assignment logic"
```

---

### Task 2: `delete_worker` hard delete

**Files:**
- Modify: `src/agent_eval_orchestrator/storage/store.py` (after `worker_exists`)
- Modify: `tests/storage/test_worker_delete_store.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/storage/test_worker_delete_store.py`:

```python
def test_delete_worker_not_found(store):
    assert store.delete_worker("missing-worker") is False


def test_delete_worker_removes_row_and_provision_jobs(store):
    store.create_provisioning_worker(
        worker_id="ecs-worker-del",
        display_name="ecs-worker-del",
        slots_total=1,
        ssh_host_alias="aeo-ecs-0004",
        ssh_bootstrap_host_alias=None,
        tunnel_remote_port=17380,
    )
    job_id = new_id("prov")
    store.create_provision_job(
        job_id=job_id,
        worker_id="ecs-worker-del",
        mode="join",
        steps=[{"id": "validate_ssh", "label": "校验 SSH 连接", "status": "pending"}],
    )
    assert store.delete_worker("ecs-worker-del") is True
    assert store.worker_exists("ecs-worker-del") is False
    assert store.get_provision_job(job_id) is None
    assert store.list_workers() == []


def test_delete_worker_id_reusable(store):
    store.register_worker(
        worker_id="ecs-worker-del",
        display_name="ecs-worker-del",
        host="10.0.0.1",
        slots_total=1,
        slots_used=0,
        capabilities={},
    )
    assert store.delete_worker("ecs-worker-del") is True
    store.register_worker(
        worker_id="ecs-worker-del",
        display_name="ecs-worker-del",
        host="10.0.0.2",
        slots_total=1,
        slots_used=0,
        capabilities={},
    )
    assert store.worker_exists("ecs-worker-del") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/storage/test_worker_delete_store.py::test_delete_worker_not_found -v`
Expected: FAIL with `AttributeError: 'Store' object has no attribute 'delete_worker'`

- [ ] **Step 3: Write minimal implementation**

Add after `worker_exists` in `store.py`:

```python
    def delete_worker(self, worker_id: str) -> bool:
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM workers WHERE worker_id = ?",
                (worker_id,),
            ).fetchone()
            if not existing:
                return False
            conn.execute("DELETE FROM provision_jobs WHERE worker_id = ?", (worker_id,))
            conn.execute("DELETE FROM workers WHERE worker_id = ?", (worker_id,))
        return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/storage/test_worker_delete_store.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/storage/store.py tests/storage/test_worker_delete_store.py
git commit -m "feat: hard-delete worker row and provision jobs"
```

---

### Task 3: `Provisioner.decommission_worker` + refactor `cancel_job`

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/provisioner.py:194-199,296-308`
- Create: `tests/controller/test_provisioner_decommission.py`

- [ ] **Step 1: Write the failing test**

Create `tests/controller/test_provisioner_decommission.py`:

```python
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_eval_orchestrator.controller.provisioner import Provisioner


def _provisioner(store, ssh_config: Path) -> Provisioner:
    return Provisioner(
        store=store,
        ssh_config_path=ssh_config,
        auth_token="secret",
        controller_port=7380,
        bootstrap_script_path=ssh_config.parent / "bootstrap.sh",
        tunnel_state_path=store.layout.controller_dir / "tunnels.json",
    )


def test_decommission_worker_skipped_without_ssh_alias(store, sample_ssh_config):
    provisioner = _provisioner(store, sample_ssh_config)
    result = provisioner.decommission_worker(worker_id="w1", ssh_host_alias=None)
    assert result == {"remoteCleanup": "skipped", "warnings": []}


def test_decommission_worker_done(store, sample_ssh_config):
    provisioner = _provisioner(store, sample_ssh_config)
    provisioner.tunnels.kill_tunnel = MagicMock()
    with patch.object(provisioner, "_ssh_run") as ssh_run:
        ssh_run.return_value.returncode = 0
        ssh_run.return_value.stderr = ""
        result = provisioner.decommission_worker(worker_id="w1", ssh_host_alias="aeo-ecs-0004")
    provisioner.tunnels.kill_tunnel.assert_called_once_with("w1")
    ssh_run.assert_called_once()
    assert result["remoteCleanup"] == "done"
    assert result["warnings"] == []


def test_decommission_worker_partial_on_tunnel_failure(store, sample_ssh_config):
    provisioner = _provisioner(store, sample_ssh_config)
    provisioner.tunnels.kill_tunnel = MagicMock(side_effect=RuntimeError("no pid"))
    with patch.object(provisioner, "_ssh_run") as ssh_run:
        ssh_run.return_value.returncode = 0
        ssh_run.return_value.stderr = ""
        result = provisioner.decommission_worker(worker_id="w1", ssh_host_alias="aeo-ecs-0004")
    assert result["remoteCleanup"] == "partial"
    assert any("failed to kill tunnel" in item for item in result["warnings"])


def test_cancel_job_uses_decommission_worker(store, sample_ssh_config):
    provisioner = _provisioner(store, sample_ssh_config)
    job_id = "prov-test"
    store.create_provision_job(
        job_id=job_id,
        worker_id="w1",
        mode="join",
        steps=[{"id": "validate_ssh", "label": "校验 SSH 连接", "status": "pending"}],
    )
    with patch.object(provisioner, "decommission_worker", return_value={"remoteCleanup": "done", "warnings": []}) as decommission:
        provisioner.cancel_job(job_id, worker_id="w1", ssh_host_alias="aeo-ecs-0004")
    decommission.assert_called_once_with(worker_id="w1", ssh_host_alias="aeo-ecs-0004")
    job = store.get_provision_job(job_id)
    assert job is not None
    assert job["status"] == "cancelled"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/controller/test_provisioner_decommission.py -v`
Expected: FAIL with `AttributeError: 'Provisioner' object has no attribute 'decommission_worker'`

- [ ] **Step 3: Write minimal implementation**

Add `decommission_worker` and refactor `cancel_job` in `provisioner.py`:

```python
    def decommission_worker(
        self,
        *,
        worker_id: str,
        ssh_host_alias: str | None,
    ) -> dict[str, object]:
        if not ssh_host_alias:
            return {"remoteCleanup": "skipped", "warnings": []}
        warnings: list[str] = []
        try:
            self.tunnels.kill_tunnel(worker_id)
        except Exception as exc:
            warnings.append(f"failed to kill tunnel: {exc}")
        remote_cmd = f"pkill -f 'worker.daemon.*--worker-id {worker_id}' || true"
        try:
            result = self._ssh_run(
                ssh_host_alias,
                remote_cmd,
                check=False,
                connect_timeout_sec=10,
            )
            if result.returncode != 0 and result.stderr.strip():
                warnings.append(f"ssh pkill failed: {result.stderr.strip()}")
        except Exception as exc:
            warnings.append(f"ssh pkill failed: {exc}")
        remote_cleanup = "partial" if warnings else "done"
        return {"remoteCleanup": remote_cleanup, "warnings": warnings}

    def cancel_job(self, job_id: str, *, worker_id: str, ssh_host_alias: str) -> None:
        self._cancelled.add(job_id)
        self.decommission_worker(
            worker_id=worker_id,
            ssh_host_alias=ssh_host_alias or None,
        )
        self.store.update_provision_job(job_id, status="cancelled", finished=True)
```

Update `_ssh_run` signature to accept optional timeout:

```python
    def _ssh_run(
        self,
        host_alias: str,
        remote_command: str,
        *,
        check: bool = True,
        connect_timeout_sec: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        cmd = [*self._ssh_base()]
        if connect_timeout_sec is not None:
            cmd.extend(["-o", f"ConnectTimeout={connect_timeout_sec}"])
        cmd.extend([host_alias, remote_command])
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self._log(result.stdout + result.stderr)
        if check and result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "ssh command failed")
        return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/controller/test_provisioner_decommission.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/controller/provisioner.py tests/controller/test_provisioner_decommission.py
git commit -m "feat: extract decommission_worker for remote cleanup"
```

---

### Task 4: `DELETE /api/workers/{workerId}` endpoint

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/server.py` (add `do_DELETE` before `ThreadedServer`)
- Create: `tests/controller/test_delete_worker_api.py`

- [ ] **Step 1: Write the failing test**

Create `tests/controller/test_delete_worker_api.py`:

```python
import json
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread
from unittest.mock import patch

from agent_eval_orchestrator.controller.provisioner import Provisioner
from agent_eval_orchestrator.controller.server import Handler, ThreadedServer
from agent_eval_orchestrator.core.ids import new_id
from agent_eval_orchestrator.storage.store import Store


def start_test_server(store: Store, ssh_config: Path, port: int) -> ThreadedServer:
    bootstrap = ssh_config.parent / "bootstrap.sh"
    bootstrap.write_text("#!/bin/bash\n", encoding="utf-8")
    provisioner = Provisioner(
        store=store,
        ssh_config_path=ssh_config,
        auth_token="secret",
        controller_port=port,
        bootstrap_script_path=bootstrap,
        tunnel_state_path=store.layout.controller_dir / "tunnels.json",
    )
    server = ThreadedServer(("127.0.0.1", port), Handler)
    Handler.store = store
    Handler.auth_token = "secret"
    Handler.viewer_manager = None
    Handler.provisioner = provisioner
    Handler.ssh_config_path = ssh_config
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def delete_worker(port: int, worker_id: str) -> tuple[int, dict]:
    conn = HTTPConnection("127.0.0.1", port)
    conn.request(
        "DELETE",
        f"/api/workers/{worker_id}",
        headers={"X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    body = json.loads(resp.read().decode("utf-8"))
    return resp.status, body


def _seed_worker(store, worker_id: str = "ecs-worker-del", *, ssh_alias: str | None = None):
    if ssh_alias:
        store.create_provisioning_worker(
            worker_id=worker_id,
            display_name=worker_id,
            slots_total=1,
            ssh_host_alias=ssh_alias,
            ssh_bootstrap_host_alias=None,
            tunnel_remote_port=17380,
        )
        store.set_worker_provision_status(worker_id, provision_status="ready")
    else:
        store.register_worker(
            worker_id=worker_id,
            display_name=worker_id,
            host="10.0.0.1",
            slots_total=1,
            slots_used=0,
            capabilities={},
        )


def _seed_template(store):
    return store.create_task_template(
        owner="default",
        name="delete-api-test",
        dataset_ref="/tmp/dataset",
        executor_kind="harbor",
        executor_config={"jobsDir": "/tmp/jobs"},
        model_profile_ref=None,
        note="",
    )


def test_delete_worker_not_found(store, sample_ssh_config):
    server = start_test_server(store, sample_ssh_config, 9878)
    status, body = delete_worker(9878, "missing-worker")
    assert status == 404
    assert body == {"error": "worker not found"}
    server.shutdown()


def test_delete_worker_with_running_batch(store, sample_ssh_config):
    _seed_worker(store)
    template = _seed_template(store)
    run = store.create_run(template_id=template["template_id"])
    batch = store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["case-a"],
        preferred_worker_id="ecs-worker-del",
        batch_options={},
    )
    with store.connect() as conn:
        conn.execute(
            """
            UPDATE batches
            SET status = 'running', assigned_worker_id = ?, current_step = 'executor-starting'
            WHERE batch_id = ?
            """,
            ("ecs-worker-del", batch["batch_id"]),
        )
    server = start_test_server(store, sample_ssh_config, 9879)
    status, body = delete_worker(9879, "ecs-worker-del")
    assert status == 409
    assert body["error"] == "worker has active batches"
    assert body["runningCount"] == 1
    assert body["queuedCount"] == 0
    server.shutdown()


def test_delete_worker_with_queued_batch(store, sample_ssh_config):
    _seed_worker(store)
    template = _seed_template(store)
    run = store.create_run(template_id=template["template_id"])
    store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["case-a"],
        preferred_worker_id="ecs-worker-del",
        batch_options={},
    )
    server = start_test_server(store, sample_ssh_config, 9880)
    status, body = delete_worker(9880, "ecs-worker-del")
    assert status == 409
    assert body["runningCount"] == 0
    assert body["queuedCount"] == 1
    server.shutdown()


def test_delete_worker_success_no_ssh(store, sample_ssh_config):
    _seed_worker(store, ssh_alias=None)
    server = start_test_server(store, sample_ssh_config, 9881)
    status, body = delete_worker(9881, "ecs-worker-del")
    assert status == 200
    assert body == {
        "ok": True,
        "workerId": "ecs-worker-del",
        "remoteCleanup": "skipped",
    }
    assert store.worker_exists("ecs-worker-del") is False
    server.shutdown()


def test_delete_worker_success_with_ssh(store, sample_ssh_config):
    _seed_worker(store, ssh_alias="aeo-ecs-0004")
    server = start_test_server(store, sample_ssh_config, 9882)
    with patch.object(server.RequestHandlerClass.provisioner, "decommission_worker", return_value={"remoteCleanup": "done", "warnings": []}):
        status, body = delete_worker(9882, "ecs-worker-del")
    assert status == 200
    assert body["remoteCleanup"] == "done"
    assert store.worker_exists("ecs-worker-del") is False
    server.shutdown()


def test_delete_worker_cancels_provision_job(store, sample_ssh_config):
    store.create_provisioning_worker(
        worker_id="ecs-worker-del",
        display_name="ecs-worker-del",
        slots_total=1,
        ssh_host_alias="aeo-ecs-0004",
        ssh_bootstrap_host_alias=None,
        tunnel_remote_port=17380,
    )
    job_id = new_id("prov")
    store.create_provision_job(
        job_id=job_id,
        worker_id="ecs-worker-del",
        mode="join",
        steps=[{"id": "validate_ssh", "label": "校验 SSH 连接", "status": "pending"}],
    )
    store.update_provision_job(job_id, status="running")
    server = start_test_server(store, sample_ssh_config, 9883)
    with patch.object(
        server.RequestHandlerClass.provisioner,
        "decommission_worker",
        return_value={"remoteCleanup": "done", "warnings": []},
    ):
        status, body = delete_worker(9883, "ecs-worker-del")
    assert status == 200
    cancelled = store.get_provision_job(job_id)
    assert cancelled is None
    server.shutdown()


def test_delete_worker_id_reusable(store, sample_ssh_config):
    _seed_worker(store, ssh_alias=None)
    server = start_test_server(store, sample_ssh_config, 9884)
    delete_worker(9884, "ecs-worker-del")
    server.shutdown()
    assert store.worker_exists("ecs-worker-del") is False
    store.register_worker(
        worker_id="ecs-worker-del",
        display_name="ecs-worker-del",
        host="10.0.0.2",
        slots_total=1,
        slots_used=0,
        capabilities={},
    )
    assert store.worker_exists("ecs-worker-del") is True


def test_historical_batch_keeps_worker_id(store, sample_ssh_config):
    _seed_worker(store, ssh_alias=None)
    template = _seed_template(store)
    run = store.create_run(template_id=template["template_id"])
    batch = store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["case-a"],
        preferred_worker_id="ecs-worker-del",
        batch_options={},
    )
    with store.connect() as conn:
        conn.execute(
            """
            UPDATE batches
            SET status = 'finished', assigned_worker_id = ?, finished_at = ?
            WHERE batch_id = ?
            """,
            ("ecs-worker-del", "2026-05-24T00:00:00+00:00", batch["batch_id"]),
        )
    server = start_test_server(store, sample_ssh_config, 9885)
    delete_worker(9885, "ecs-worker-del")
    server.shutdown()
    detail = store.get_batch_detail(batch["batch_id"])
    assert detail is not None
    assert detail["batch"]["assigned_worker_id"] == "ecs-worker-del"
    assert detail["worker"] is None
```

Note: For `test_delete_worker_success_with_ssh` and `test_delete_worker_cancels_provision_job`, patch via the module-level `Handler.provisioner` instead if `server.RequestHandlerClass.provisioner` is unavailable:

```python
with patch.object(Handler.provisioner, "decommission_worker", ...):
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/controller/test_delete_worker_api.py::test_delete_worker_not_found -v`
Expected: FAIL — HTTP 404 from routing or `405 Method Not Allowed`

- [ ] **Step 3: Write minimal implementation**

Add to `server.py` before `class ThreadedServer`:

```python
    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if not self._is_authorized():
            _json_response(self, {"error": "forbidden"}, 403)
            return
        parts = path.split("/")
        if len(parts) == 4 and parts[1] == "api" and parts[2] == "workers":
            worker_id = parts[3]
            reserved = {"provision", "runtime", "register", "claim", "heartbeat", "job-archive"}
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
            if self.provisioner is not None:
                latest = self.store.get_latest_provision_job_for_worker(worker_id)
                if latest and str(latest["status"]) in {"pending", "running"}:
                    ssh_alias = str(worker.get("ssh_host_alias") or "")
                    self.provisioner.cancel_job(
                        str(latest["job_id"]),
                        worker_id=worker_id,
                        ssh_host_alias=ssh_alias,
                    )
                cleanup = self.provisioner.decommission_worker(
                    worker_id=worker_id,
                    ssh_host_alias=str(worker.get("ssh_host_alias") or "") or None,
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/controller/test_delete_worker_api.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/controller/server.py tests/controller/test_delete_worker_api.py
git commit -m "feat: add DELETE /api/workers/{workerId} endpoint"
```

---

### Task 5: Dashboard delete button + confirmation modal

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/static.py:311-330,598-609,1165-1245`

- [ ] **Step 1: Add danger button style and delete modal markup**

After `.ghost` styles (~line 327), add:

```css
    .danger {
      background: var(--bad);
      color: #fff;
      border: 1px solid var(--bad);
    }
    .danger:disabled {
      opacity: 0.45;
      cursor: not-allowed;
    }
    .toast {
      position: fixed;
      right: 24px;
      bottom: 24px;
      background: #111827;
      color: #fff;
      padding: 12px 16px;
      border-radius: 10px;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.18);
      z-index: 1000;
      max-width: 420px;
    }
    .toast.hidden { display: none; }
```

After `addWorkerModal` div (~line 609), add:

```html
  <div class="modal hidden" id="deleteWorkerModal">
    <div class="modal-card">
      <div class="modal-header">
        <div>
          <h3 id="deleteWorkerModalTitle">删除 Worker</h3>
          <div class="subtle" id="deleteWorkerModalSubtitle"></div>
        </div>
        <button class="modal-close" id="deleteWorkerModalClose" aria-label="关闭">×</button>
      </div>
      <div class="modal-body">
        <p id="deleteWorkerModalBody"></p>
        <div class="actions">
          <button class="danger" type="button" id="confirmDeleteWorkerBtn">确认删除</button>
          <button class="ghost" type="button" id="cancelDeleteWorkerBtn">取消</button>
        </div>
      </div>
    </div>
  </div>
  <div class="toast hidden" id="toast"></div>
```

- [ ] **Step 2: Add toast helper and delete modal logic**

After the `api()` function (~line 696), add:

```javascript
    let toastTimer = null;
    function showToast(message) {
      const el = document.getElementById("toast");
      el.textContent = message;
      el.classList.remove("hidden");
      if (toastTimer) clearTimeout(toastTimer);
      toastTimer = setTimeout(() => el.classList.add("hidden"), 4000);
    }

    function closeDeleteWorkerModal() {
      document.getElementById("deleteWorkerModal").classList.add("hidden");
      state.pendingDeleteWorkerId = null;
    }

    function openDeleteWorkerModal(worker) {
      state.pendingDeleteWorkerId = worker.worker_id;
      const runtime = runtimeForWorker(worker.worker_id) || {};
      const hasSsh = Boolean(worker.ssh_host_alias);
      document.getElementById("deleteWorkerModalTitle").textContent =
        '删除 Worker "' + worker.display_name + '"？';
      let body = hasSsh
        ? "将停止远程 daemon 和 SSH 隧道，并从列表移除。ECS 实例不会被销毁。"
        : "该 worker 无 SSH 配置，仅会从 controller 移除，不会执行远程清理。";
      if (worker.provision_status === "provisioning") {
        body += " 将取消进行中的部署任务。";
      }
      document.getElementById("deleteWorkerModalBody").textContent = body;
      document.getElementById("deleteWorkerModal").classList.remove("hidden");
    }

    async function confirmDeleteWorker() {
      const workerId = state.pendingDeleteWorkerId;
      if (!workerId) return;
      const result = await api("/api/workers/" + encodeURIComponent(workerId), { method: "DELETE" });
      closeDeleteWorkerModal();
      state.selectedWorkerId = null;
      await loadDashboard();
      if (result.remoteCleanup === "skipped") {
        showToast("Worker 已删除（未执行远程清理）");
      } else if (result.remoteCleanup === "partial") {
        const extra = (result.warnings || []).join("; ");
        showToast("Worker 已删除，远程清理部分失败" + (extra ? "：" + extra : ""));
      } else {
        showToast("Worker 已删除");
      }
    }
```

Wire modal close buttons once at startup (inside existing DOMContentLoaded / init block near other modal listeners):

```javascript
    document.getElementById("deleteWorkerModalClose").addEventListener("click", closeDeleteWorkerModal);
    document.getElementById("cancelDeleteWorkerBtn").addEventListener("click", closeDeleteWorkerModal);
    document.getElementById("confirmDeleteWorkerBtn").addEventListener("click", () => {
      confirmDeleteWorker().catch(err => alert(err.message || String(err)));
    });
```

Add `pendingDeleteWorkerId: null` to `state` object.

- [ ] **Step 3: Add delete button in `renderWorkerDetail`**

In the actions row (~line 1201), replace:

```javascript
          '<div class="actions">' +
            '<button class="primary" type="submit">保存配置</button>' +
            '<button class="ghost" type="button" id="toggleEnabledBtn">' + (worker.enabled ? "设为禁用" : "设为启用") + '</button>' +
          '</div>' +
```

With:

```javascript
          '<div class="actions">' +
            '<button class="primary" type="submit">保存配置</button>' +
            '<button class="ghost" type="button" id="toggleEnabledBtn">' + (worker.enabled ? "设为禁用" : "设为启用") + '</button>' +
            '<button class="danger" type="button" id="deleteWorkerBtn"' +
              ((runtime.runningCount || 0) > 0 || (runtime.queuedCount || 0) > 0 ? ' disabled title="请先等待或停止运行中的 batch"' : '') +
            '>删除 Worker</button>' +
          '</div>' +
```

After the `#toggleEnabledBtn` listener block, add:

```javascript
      const deleteBtn = root.querySelector("#deleteWorkerBtn");
      if (deleteBtn && !deleteBtn.disabled) {
        deleteBtn.addEventListener("click", () => openDeleteWorkerModal(worker));
      }
```

- [ ] **Step 4: Manual smoke test**

Run controller: `uv run aeo-controller --port 7380`
Open Workers tab → select a worker with no active batches → click **删除 Worker** → confirm.
Expected: worker disappears from list; toast shows appropriate message.

- [ ] **Step 5: Run full test suite**

Run: `uv run --extra dev pytest tests/storage/test_worker_delete_store.py tests/controller/test_provisioner_decommission.py tests/controller/test_delete_worker_api.py -v`
Expected: PASS (all new tests)

- [ ] **Step 6: Commit**

```bash
git add src/agent_eval_orchestrator/controller/static.py
git commit -m "feat: add delete worker button and confirmation modal to dashboard"
```

---

## Self-Review

**Spec coverage:**

| Requirement | Task |
|-------------|------|
| `DELETE /api/workers/{workerId}` with 404/409/200 | Task 4 |
| Active batch blocking (running + queued, all assignment paths) | Task 1 + Task 4 |
| Cancel active provision job before delete | Task 4 |
| Remote cleanup via `decommission_worker` | Task 3 + Task 4 |
| Hard delete workers + provision_jobs | Task 2 + Task 4 |
| `worker_id` reuse | Task 2 + Task 4 |
| Historical batches keep string references | Task 4 (`test_historical_batch_keeps_worker_id`) |
| Dashboard delete button + modal + disabled state + toasts | Task 5 |
| No ECS destroy, no bulk delete, no filesystem cleanup | Non-goals — not implemented |

**Placeholder scan:** No TBD/TODO/similar-to placeholders.

**Type consistency:** `remoteCleanup` values (`done`/`skipped`/`partial`), response keys (`runningCount`, `queuedCount`, `workerId`, `ok`), and store method signatures match spec throughout.

---

Plan complete and saved to `docs/superpowers/plans/2026-05-24-worker-delete.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
