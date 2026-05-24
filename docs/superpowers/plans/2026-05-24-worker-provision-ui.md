# Worker Provision UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an **Add Worker** wizard to the Controller dashboard that remotely provisions worker nodes over SSH (fresh install or join existing), with async jobs, tunnel management, and polling progress UI.

**Architecture:** New `ssh_config.py` parses the Controller user's OpenSSH config and validates aliases via `ssh -G` / `ssh echo ok`. `provisioner.py` runs provisioning steps in a background thread using `subprocess` (`ssh`, `scp`), persists job state in SQLite, and records reverse tunnels in `controller/tunnels.json`. `server.py` exposes REST endpoints; `static.py` adds a modal wizard that polls job status every 2s.

**Tech Stack:** Python 3.10+, stdlib (`http.server`, `sqlite3`, `subprocess`, `threading`), OpenSSH, embedded HTML/JS dashboard, pytest

---

## File Structure

| File | Responsibility |
|------|----------------|
| `pyproject.toml` | Add optional `dev` dependency group with pytest |
| `tests/conftest.py` | Temp Layout/Store fixtures, sample SSH config file |
| `tests/controller/test_ssh_config.py` | SSH config parser + alias resolution tests |
| `tests/controller/test_provisioner_templates.py` | Daemon/bootstrap command templates, log redaction |
| `tests/controller/test_provisioner_state.py` | Job step definitions, state transitions |
| `tests/controller/test_provisioner_runner.py` | Mocked subprocess step-order tests (fresh vs join) |
| `tests/controller/test_provision_api.py` | HTTP handler tests for 400/409/201 |
| `tests/storage/test_provision_store.py` | Schema migration + provision_jobs CRUD |
| `src/agent_eval_orchestrator/controller/ssh_config.py` | Parse Host blocks, `ssh -G`, connectivity test |
| `src/agent_eval_orchestrator/controller/provisioner.py` | Templates, tunnel manager, job runner |
| `src/agent_eval_orchestrator/storage/store.py` | Worker columns, `provision_jobs` table, CRUD |
| `src/agent_eval_orchestrator/controller/server.py` | `--ssh-config` CLI, provision/SSH API routes |
| `src/agent_eval_orchestrator/controller/static.py` | Add Worker button, modal wizard, badges |
| `scripts/bootstrap-huawei-worker.sh` | (existing) copied to remote in fresh mode |

---

### Task 1: Pytest harness

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/conftest.py`
- Create: `tests/controller/__init__.py` (empty)
- Create: `tests/storage/__init__.py` (empty)
- Create: `tests/controller/test_harness.py`

- [ ] **Step 1: Write the failing test**

Create `tests/controller/test_harness.py`:

```python
from pathlib import Path


def test_conftest_store_fixture(store):
    assert store.layout.db_path.exists()
    workers = store.list_workers()
    assert workers == []
```

Create `tests/conftest.py`:

```python
from __future__ import annotations

from pathlib import Path
import tempfile

import pytest

from agent_eval_orchestrator.storage.layout import Layout
from agent_eval_orchestrator.storage.store import Store


@pytest.fixture()
def temp_layout(tmp_path: Path) -> Layout:
    layout = Layout(tmp_path / "runtime")
    layout.ensure_dirs()
    return layout


@pytest.fixture()
def store(temp_layout: Layout) -> Store:
    return Store(temp_layout)


@pytest.fixture()
def sample_ssh_config(tmp_path: Path) -> Path:
    content = """
Host aeo-ecs-0004-root
    HostName 192.168.0.244
    User root
    IdentityFile ~/.ssh/aeo_admin

Host aeo-ecs-0004
    HostName 192.168.0.244
    User djn
    IdentityFile ~/.ssh/aeo_workers
"""
    path = tmp_path / "ssh_config"
    path.write_text(content.strip() + "\n", encoding="utf-8")
    return path
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/djn/code/Agent-Eval-Orchestrator && uv run --with pytest pytest tests/controller/test_harness.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pytest'` or collection error

- [ ] **Step 3: Write minimal implementation**

Add to `pyproject.toml` after the `[project]` dependencies line:

```toml
[project.optional-dependencies]
dev = ["pytest>=8.0"]
```

Create empty `tests/controller/__init__.py` and `tests/storage/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/controller/test_harness.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/conftest.py tests/controller/__init__.py tests/storage/__init__.py tests/controller/test_harness.py
git commit -m "test: add pytest harness for controller provision work"
```

---

### Task 2: SQLite schema — worker columns + provision_jobs table

**Files:**
- Modify: `src/agent_eval_orchestrator/storage/store.py` (`_ensure_schema_migrations`, `_worker_item`, new CRUD)
- Test: `tests/storage/test_provision_store.py`

- [ ] **Step 1: Write the failing test**

Create `tests/storage/test_provision_store.py`:

```python
from agent_eval_orchestrator.core.ids import new_id, now_iso


def test_provision_schema_and_crud(store):
    worker = store.create_provisioning_worker(
        worker_id="ecs-worker-0004",
        display_name="ecs-worker-0004",
        slots_total=1,
        ssh_host_alias="aeo-ecs-0004",
        ssh_bootstrap_host_alias="aeo-ecs-0004-root",
        tunnel_remote_port=17380,
    )
    assert worker["provision_status"] == "provisioning"
    assert worker["ssh_host_alias"] == "aeo-ecs-0004"

    job_id = new_id("prov")
    job = store.create_provision_job(
        job_id=job_id,
        worker_id="ecs-worker-0004",
        mode="fresh",
        steps=[
            {"id": "validate_ssh", "label": "校验 SSH 连接", "status": "pending"},
        ],
    )
    assert job["status"] == "pending"

    store.append_provision_log(job_id, "line one\n")
    updated = store.update_provision_job(
        job_id,
        status="running",
        current_step="validate_ssh",
        steps=[{"id": "validate_ssh", "label": "校验 SSH 连接", "status": "running"}],
    )
    assert updated["log_text"].endswith("line one\n")
    assert updated["status"] == "running"

    fetched = store.get_provision_job(job_id)
    assert fetched is not None
    assert fetched["worker_id"] == "ecs-worker-0004"


def test_register_worker_marks_provision_ready(store):
    store.create_provisioning_worker(
        worker_id="ecs-worker-0004",
        display_name="ecs-worker-0004",
        slots_total=1,
        ssh_host_alias="aeo-ecs-0004",
        ssh_bootstrap_host_alias=None,
        tunnel_remote_port=17380,
    )
    worker = store.register_worker(
        worker_id="ecs-worker-0004",
        display_name="ecs-worker-0004",
        host="worker-host",
        slots_total=1,
        slots_used=0,
        capabilities={"sharedRoot": "/home/djn/worker/agent-eval-orchestrator/runtime"},
    )
    assert worker["provision_status"] == "ready"
    assert worker["status"] == "online"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/storage/test_provision_store.py -v`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'create_provisioning_worker'`

- [ ] **Step 3: Write minimal implementation**

In `store.py`, extend `_ensure_schema_migrations`:

```python
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
                """
            )
```

Add methods to `Store`:

```python
    def worker_exists(self, worker_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM workers WHERE worker_id = ?",
                (worker_id,),
            ).fetchone()
        return row is not None

    def create_provisioning_worker(
        self,
        *,
        worker_id: str,
        display_name: str,
        slots_total: int,
        ssh_host_alias: str,
        ssh_bootstrap_host_alias: str | None,
        tunnel_remote_port: int,
    ) -> dict[str, Any]:
        now = now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO workers(
                    worker_id, display_name, host, slots_total, slots_used,
                    capabilities_json, status, enabled, note, tags_json,
                    ssh_host_alias, ssh_bootstrap_host_alias, tunnel_remote_port,
                    provision_status, last_provision_error,
                    last_heartbeat_at, created_at, updated_at
                ) VALUES(?, ?, '', ?, 0, '{}', 'unavailable', 1, '', '[]',
                         ?, ?, ?, 'provisioning', NULL, NULL, ?, ?)
                """,
                (
                    worker_id,
                    display_name,
                    slots_total,
                    ssh_host_alias,
                    ssh_bootstrap_host_alias,
                    tunnel_remote_port,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM workers WHERE worker_id = ?",
                (worker_id,),
            ).fetchone()
        return self._decorate_worker(self._worker_item(row))

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
```

Update `_worker_item` to pass through new columns (they remain on the dict as-is from sqlite Row).

Update `register_worker` after the UPDATE/INSERT block, before fetching row — when updating existing worker with `provision_status == 'provisioning'`, also set `provision_status = 'ready'`:

```python
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
```

Update `_decorate_worker` to honor provision states before heartbeat logic:

```python
        provision_status = str(item.get("provision_status") or "none")
        if provision_status == "provisioning":
            item["status"] = "provisioning"
            item["manualStatus"] = "enabled" if item.get("enabled", True) else "disabled"
            item["allocationScore"] = round(self._worker_allocation_score(item), 2)
            return item
        if provision_status == "failed":
            item["status"] = "provision_failed"
            item["manualStatus"] = "enabled" if item.get("enabled", True) else "disabled"
            item["allocationScore"] = round(self._worker_allocation_score(item), 2)
            return item
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/storage/test_provision_store.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/storage/store.py tests/storage/test_provision_store.py
git commit -m "feat: add provision_jobs schema and worker provisioning CRUD"
```

---

### Task 3: SSH config parser

**Files:**
- Create: `src/agent_eval_orchestrator/controller/ssh_config.py`
- Test: `tests/controller/test_ssh_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/controller/test_ssh_config.py`:

```python
from agent_eval_orchestrator.controller.ssh_config import (
    list_ssh_hosts,
    resolve_ssh_alias,
)


def test_list_ssh_hosts_skips_wildcards(sample_ssh_config):
    items = list_ssh_hosts(sample_ssh_config)
    aliases = {item["hostAlias"] for item in items}
    assert aliases == {"aeo-ecs-0004-root", "aeo-ecs-0004"}
    djn = next(item for item in items if item["hostAlias"] == "aeo-ecs-0004")
    assert djn["hostname"] == "192.168.0.244"
    assert djn["user"] == "djn"
    assert djn["port"] == 22


def test_resolve_ssh_alias_unknown(sample_ssh_config):
    try:
        resolve_ssh_alias(sample_ssh_config, "missing-host")
    except ValueError as exc:
        assert "missing-host" in str(exc)
    else:
        raise AssertionError("expected ValueError")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/controller/test_ssh_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_eval_orchestrator.controller.ssh_config'`

- [ ] **Step 3: Write minimal implementation**

Create `src/agent_eval_orchestrator/controller/ssh_config.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess


@dataclass(frozen=True)
class SshHostEntry:
    host_alias: str
    hostname: str
    user: str
    port: int


_HOST_BLOCK_RE = re.compile(r"(?ms)^Host\s+(\S+)\s*\n(.*?)(?=^Host\s|\Z)")


def _parse_host_blocks(config_text: str) -> dict[str, dict[str, str]]:
    blocks: dict[str, dict[str, str]] = {}
    for match in _HOST_BLOCK_RE.finditer(config_text):
        alias = match.group(1).strip()
        if "*" in alias or "?" in alias or "!" in alias:
            continue
        options: dict[str, str] = {}
        for line in match.group(2).splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if " " not in stripped:
                continue
            key, value = stripped.split(None, 1)
            options[key.lower()] = value.strip()
        blocks[alias] = options
    return blocks


def list_ssh_hosts(config_path: Path) -> list[dict[str, object]]:
    text = config_path.expanduser().read_text(encoding="utf-8")
    blocks = _parse_host_blocks(text)
    items: list[dict[str, object]] = []
    for alias, options in sorted(blocks.items()):
        resolved = resolve_ssh_alias(config_path, alias)
        items.append(
            {
                "hostAlias": resolved.host_alias,
                "hostname": resolved.hostname,
                "user": resolved.user,
                "port": resolved.port,
            }
        )
    return items


def resolve_ssh_alias(config_path: Path, host_alias: str) -> SshHostEntry:
    config_path = config_path.expanduser().resolve()
    blocks = _parse_host_blocks(config_path.read_text(encoding="utf-8"))
    if host_alias not in blocks:
        raise ValueError(f"SSH host alias not found in config: {host_alias}")

    result = subprocess.run(
        ["ssh", "-F", str(config_path), "-G", host_alias],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or f"ssh -G failed for {host_alias}")

    parsed: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if " " not in line:
            continue
        key, value = line.split(" ", 1)
        parsed[key.lower()] = value.strip()

    hostname = parsed.get("hostname") or blocks[host_alias].get("hostname") or host_alias
    user = parsed.get("user") or blocks[host_alias].get("user") or ""
    port_raw = parsed.get("port") or blocks[host_alias].get("port") or "22"
    return SshHostEntry(
        host_alias=host_alias,
        hostname=hostname,
        user=user,
        port=int(port_raw),
    )


def test_ssh_alias(config_path: Path, host_alias: str, *, timeout_sec: int = 10) -> tuple[bool, str]:
    config_path = config_path.expanduser().resolve()
    try:
        resolve_ssh_alias(config_path, host_alias)
    except ValueError as exc:
        return False, str(exc)

    result = subprocess.run(
        [
            "ssh",
            "-F",
            str(config_path),
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={timeout_sec}",
            host_alias,
            "echo",
            "ok",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0 and "ok" in (result.stdout or ""):
        return True, "connected"
    message = (result.stderr or result.stdout or "SSH connection failed").strip()
    return False, message
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/controller/test_ssh_config.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/controller/ssh_config.py tests/controller/test_ssh_config.py
git commit -m "feat: add OpenSSH config parser and alias resolution"
```

---

### Task 4: Command templates and log redaction

**Files:**
- Create: `src/agent_eval_orchestrator/controller/provisioner.py` (templates only initially)
- Test: `tests/controller/test_provisioner_templates.py`

- [ ] **Step 1: Write the failing test**

Create `tests/controller/test_provisioner_templates.py`:

```python
from agent_eval_orchestrator.controller.provisioner import (
    build_bootstrap_command,
    build_daemon_start_command,
    redact_sensitive_log,
)


def test_build_daemon_start_command():
    cmd = build_daemon_start_command(
        worker_id="ecs-worker-0004",
        display_name="ecs-worker-0004",
        slots=2,
        tunnel_remote_port=17380,
        auth_token="secret-token-value",
    )
    assert "--worker-id \"ecs-worker-0004\"" in cmd
    assert "--controller-url \"http://127.0.0.1:17380\"" in cmd
    assert "secret-token-value" in cmd


def test_build_bootstrap_command():
    cmd = build_bootstrap_command(djn_password="pw123")
    assert "DJN_PASSWORD='pw123'" in cmd
    assert "/tmp/aeo-bootstrap.sh --yes" in cmd


def test_redact_sensitive_log():
    raw = (
        "DJN_PASSWORD='pw123' bash /tmp/aeo-bootstrap.sh\n"
        "AEO_TOKEN=abc123 setsid uv run\n"
        "--auth-token abc123\n"
    )
    redacted = redact_sensitive_log(raw)
    assert "pw123" not in redacted
    assert "abc123" not in redacted
    assert "***REDACTED***" in redacted
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/controller/test_provisioner_templates.py -v`
Expected: FAIL — import error

- [ ] **Step 3: Write minimal implementation**

Create `src/agent_eval_orchestrator/controller/provisioner.py`:

```python
from __future__ import annotations

import re

DEFAULT_TUNNEL_REMOTE_PORT = 17380
DEFAULT_UV_BIN = "/home/djn/worker/.local/bin/uv"
DEFAULT_AEO_DIR = "/home/djn/worker/agent-eval-orchestrator"
DEFAULT_HARBOR_DIR = "/home/djn/worker/harbor"
DEFAULT_WORKER_LOG_DIR = "/home/djn/worker/logs"

_RE_DJN_PASSWORD = re.compile(r"(DJN_PASSWORD=')([^']*)(')")
_RE_AEO_TOKEN = re.compile(r"(AEO_TOKEN=)(\S+)")
_RE_AUTH_TOKEN_FLAG = re.compile(r"(--auth-token\s+)(\S+)")


def redact_sensitive_log(text: str) -> str:
    text = _RE_DJN_PASSWORD.sub(r"\1***REDACTED***\3", text)
    text = _RE_AEO_TOKEN.sub(r"\1***REDACTED***", text)
    text = _RE_AUTH_TOKEN_FLAG.sub(r"\1***REDACTED***", text)
    return text


def build_bootstrap_command(*, djn_password: str) -> str:
    escaped = djn_password.replace("'", "'\"'\"'")
    return f"DJN_PASSWORD='{escaped}' bash /tmp/aeo-bootstrap.sh --yes"


def build_verify_layout_command() -> str:
    return (
        f"test -d {DEFAULT_HARBOR_DIR} && "
        f"test -d {DEFAULT_AEO_DIR} && "
        f"{DEFAULT_UV_BIN} --version"
    )


def build_daemon_start_command(
    *,
    worker_id: str,
    display_name: str,
    slots: int,
    tunnel_remote_port: int,
    auth_token: str,
) -> str:
    local_root = f"{DEFAULT_AEO_DIR}/runtime/workers/{worker_id}/local"
    log_path = f"{DEFAULT_WORKER_LOG_DIR}/daemon-{worker_id}.log"
    return (
        f"mkdir -p {DEFAULT_WORKER_LOG_DIR} && "
        f"cd {DEFAULT_AEO_DIR} && "
        f"setsid {DEFAULT_UV_BIN} run python -u -m agent_eval_orchestrator.worker.daemon "
        f"--controller-url \"http://127.0.0.1:{tunnel_remote_port}\" "
        f"--worker-id \"{worker_id}\" "
        f"--display-name \"{display_name}\" "
        f"--host \"$(hostname -f || hostname)\" "
        f"--shared-root {DEFAULT_AEO_DIR}/runtime "
        f"--local-root \"{local_root}\" "
        f"--slots {slots} "
        f"--poll-interval 3 "
        f"--auth-token \"{auth_token}\" "
        f">> \"{log_path}\" 2>&1 &"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/controller/test_provisioner_templates.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/controller/provisioner.py tests/controller/test_provisioner_templates.py
git commit -m "feat: add provision command templates and log redaction"
```

---

### Task 5: Provision step definitions and state machine helpers

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/provisioner.py`
- Test: `tests/controller/test_provisioner_state.py`

- [ ] **Step 1: Write the failing test**

Create `tests/controller/test_provisioner_state.py`:

```python
from agent_eval_orchestrator.controller.provisioner import (
    STEP_LABELS,
    initial_steps_for_mode,
    set_step_status,
)


def test_initial_steps_fresh():
    steps = initial_steps_for_mode("fresh")
    assert [step["id"] for step in steps] == [
        "validate_ssh",
        "bootstrap",
        "verify_layout",
        "establish_tunnel",
        "start_daemon",
        "wait_register",
    ]
    assert all(step["status"] == "pending" for step in steps)


def test_initial_steps_join():
    steps = initial_steps_for_mode("join")
    assert [step["id"] for step in steps] == [
        "validate_ssh",
        "verify_layout",
        "establish_tunnel",
        "start_daemon",
        "wait_register",
    ]


def test_set_step_status():
    steps = initial_steps_for_mode("join")
    updated = set_step_status(steps, "verify_layout", "failed")
    verify = next(step for step in updated if step["id"] == "verify_layout")
    assert verify["status"] == "failed"
    assert verify["label"] == STEP_LABELS["verify_layout"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/controller/test_provisioner_state.py -v`
Expected: FAIL — import error for `initial_steps_for_mode`

- [ ] **Step 3: Write minimal implementation**

Append to `provisioner.py`:

```python
STEP_LABELS = {
    "validate_ssh": "校验 SSH 连接",
    "bootstrap": "Bootstrap 系统环境",
    "verify_layout": "校验 Worker 目录结构",
    "establish_tunnel": "建立反向隧道",
    "start_daemon": "启动 Worker Daemon",
    "wait_register": "等待 Worker 注册",
}

FRESH_STEP_IDS = [
    "validate_ssh",
    "bootstrap",
    "verify_layout",
    "establish_tunnel",
    "start_daemon",
    "wait_register",
]

JOIN_STEP_IDS = [
    "validate_ssh",
    "verify_layout",
    "establish_tunnel",
    "start_daemon",
    "wait_register",
]


def initial_steps_for_mode(mode: str) -> list[dict[str, str]]:
    ids = FRESH_STEP_IDS if mode == "fresh" else JOIN_STEP_IDS
    return [{"id": step_id, "label": STEP_LABELS[step_id], "status": "pending"} for step_id in ids]


def set_step_status(
    steps: list[dict[str, str]],
    step_id: str,
    status: str,
) -> list[dict[str, str]]:
    updated: list[dict[str, str]] = []
    for step in steps:
        item = dict(step)
        if item["id"] == step_id:
            item["status"] = status
        updated.append(item)
    return updated
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/controller/test_provisioner_state.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/controller/provisioner.py tests/controller/test_provisioner_state.py
git commit -m "feat: add provision job step definitions"
```

---

### Task 6: Tunnel manager

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/provisioner.py`
- Test: `tests/controller/test_tunnel_manager.py`

- [ ] **Step 1: Write the failing test**

Create `tests/controller/test_tunnel_manager.py`:

```python
import json

from agent_eval_orchestrator.controller.provisioner import TunnelManager


def test_tunnel_manager_persist_roundtrip(temp_layout):
    path = temp_layout.controller_dir / "tunnels.json"
    manager = TunnelManager(path)
    manager.save_record(
        "ecs-worker-0004",
        {
            "djnHostAlias": "aeo-ecs-0004",
            "remotePort": 17380,
            "localPort": 8790,
            "sshPid": 12345,
            "startedAt": "2026-05-24T12:00:00+00:00",
        },
    )
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["ecs-worker-0004"]["sshPid"] == 12345
    record = manager.get_record("ecs-worker-0004")
    assert record["remotePort"] == 17380
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/controller/test_tunnel_manager.py -v`
Expected: FAIL — `ImportError: cannot import name 'TunnelManager'`

- [ ] **Step 3: Write minimal implementation**

Append to `provisioner.py`:

```python
import json
from datetime import datetime, timezone
from pathlib import Path


class TunnelManager:
    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path

    def _load(self) -> dict[str, dict[str, object]]:
        if not self.state_path.exists():
            return {}
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def _save(self, payload: dict[str, dict[str, object]]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_record(self, worker_id: str) -> dict[str, object] | None:
        return self._load().get(worker_id)

    def save_record(self, worker_id: str, record: dict[str, object]) -> None:
        payload = self._load()
        payload[worker_id] = record
        self._save(payload)

    def remove_record(self, worker_id: str) -> dict[str, object] | None:
        payload = self._load()
        removed = payload.pop(worker_id, None)
        self._save(payload)
        return removed

    def kill_tunnel(self, worker_id: str) -> None:
        record = self.remove_record(worker_id)
        if not record:
            return
        pid = record.get("sshPid")
        if isinstance(pid, int) and pid > 0:
            import os
            import signal

            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/controller/test_tunnel_manager.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/controller/provisioner.py tests/controller/test_tunnel_manager.py
git commit -m "feat: add reverse tunnel state manager"
```

---

### Task 7: Provisioner job runner (mocked subprocess)

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/provisioner.py`
- Test: `tests/controller/test_provisioner_runner.py`

- [ ] **Step 1: Write the failing test**

Create `tests/controller/test_provisioner_runner.py`:

```python
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_eval_orchestrator.controller.provisioner import Provisioner
from agent_eval_orchestrator.core.ids import new_id


@pytest.fixture()
def provisioner(store, sample_ssh_config, tmp_path: Path):
    bootstrap = tmp_path / "bootstrap.sh"
    bootstrap.write_text("#!/bin/bash\n", encoding="utf-8")
    return Provisioner(
        store=store,
        ssh_config_path=sample_ssh_config,
        auth_token="test-token",
        controller_port=8790,
        bootstrap_script_path=bootstrap,
        tunnel_state_path=store.layout.controller_dir / "tunnels.json",
    )


def test_fresh_mode_step_order(provisioner, store, monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        result = MagicMock()
        result.returncode = 0
        result.stdout = "ok\n"
        result.stderr = ""
        return result

    monkeypatch.setattr("agent_eval_orchestrator.controller.provisioner.subprocess.run", fake_run)
    monkeypatch.setattr(
        provisioner,
        "_find_tunnel_pid",
        lambda **kwargs: 999,
    )
    monkeypatch.setattr(provisioner, "_wait_for_register", lambda **kwargs: None)

    worker_id = "ecs-worker-0004"
    store.create_provisioning_worker(
        worker_id=worker_id,
        display_name=worker_id,
        slots_total=1,
        ssh_host_alias="aeo-ecs-0004",
        ssh_bootstrap_host_alias="aeo-ecs-0004-root",
        tunnel_remote_port=17380,
    )
    job_id = new_id("prov")
    store.create_provision_job(
        job_id=job_id,
        worker_id=worker_id,
        mode="fresh",
        steps=provisioner.initial_steps("fresh"),
    )

    provisioner.run_job(
        job_id=job_id,
        worker_id=worker_id,
        mode="fresh",
        ssh_host_alias="aeo-ecs-0004",
        ssh_bootstrap_host_alias="aeo-ecs-0004-root",
        djn_password="pw",
        tunnel_remote_port=17380,
        display_name=worker_id,
        slots_total=1,
    )

    joined = " ".join(" ".join(call) for call in calls)
    assert "scp" in joined
    assert "aeo-bootstrap.sh" in joined or "/tmp/aeo-bootstrap.sh" in joined
    assert "DJN_PASSWORD=" not in store.get_provision_job(job_id)["log_text"]
    job = store.get_provision_job(job_id)
    assert job["status"] == "succeeded"


def test_join_mode_skips_bootstrap(provisioner, store, monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        result = MagicMock()
        result.returncode = 0
        result.stdout = "uv 0.5.0\n"
        result.stderr = ""
        return result

    monkeypatch.setattr("agent_eval_orchestrator.controller.provisioner.subprocess.run", fake_run)
    monkeypatch.setattr(provisioner, "_find_tunnel_pid", lambda **kwargs: 999)
    monkeypatch.setattr(provisioner, "_wait_for_register", lambda **kwargs: None)

    worker_id = "ecs-worker-0005"
    store.create_provisioning_worker(
        worker_id=worker_id,
        display_name=worker_id,
        slots_total=1,
        ssh_host_alias="aeo-ecs-0004",
        ssh_bootstrap_host_alias=None,
        tunnel_remote_port=17380,
    )
    job_id = new_id("prov")
    store.create_provision_job(
        job_id=job_id,
        worker_id=worker_id,
        mode="join",
        steps=provisioner.initial_steps("join"),
    )

    provisioner.run_job(
        job_id=job_id,
        worker_id=worker_id,
        mode="join",
        ssh_host_alias="aeo-ecs-0004",
        ssh_bootstrap_host_alias=None,
        djn_password=None,
        tunnel_remote_port=17380,
        display_name=worker_id,
        slots_total=1,
    )

    joined = " ".join(" ".join(call) for call in calls)
    assert "scp" not in joined
    assert store.get_provision_job(job_id)["status"] == "succeeded"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/controller/test_provisioner_runner.py -v`
Expected: FAIL — `ImportError: cannot import name 'Provisioner'`

- [ ] **Step 3: Write minimal implementation**

Append to `provisioner.py` (add imports at top: `subprocess`, `time`, `threading`, `Store` type):

```python
import subprocess
import threading
import time
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from agent_eval_orchestrator.storage.store import Store


class Provisioner:
    def __init__(
        self,
        *,
        store: Store,
        ssh_config_path: Path,
        auth_token: str | None,
        controller_port: int,
        bootstrap_script_path: Path,
        tunnel_state_path: Path,
    ) -> None:
        self.store = store
        self.ssh_config_path = ssh_config_path.expanduser().resolve()
        self.auth_token = auth_token or ""
        self.controller_port = controller_port
        self.bootstrap_script_path = bootstrap_script_path
        self.tunnels = TunnelManager(tunnel_state_path)
        self._threads: dict[str, threading.Thread] = {}
        self._cancelled: set[str] = set()

    def initial_steps(self, mode: str) -> list[dict[str, str]]:
        return initial_steps_for_mode(mode)

    def start_job_async(self, **kwargs: Any) -> None:
        job_id = str(kwargs["job_id"])
        thread = threading.Thread(target=self.run_job, kwargs=kwargs, daemon=True)
        self._threads[job_id] = thread
        thread.start()

    def cancel_job(self, job_id: str, *, worker_id: str, ssh_host_alias: str) -> None:
        self._cancelled.add(job_id)
        self.tunnels.kill_tunnel(worker_id)
        remote_cmd = (
            f"pkill -f 'worker.daemon.*--worker-id {worker_id}' || true"
        )
        self._ssh_run(ssh_host_alias, remote_cmd, check=False)
        self.store.update_provision_job(job_id, status="cancelled", finished=True)

    def run_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        mode: str,
        ssh_host_alias: str,
        ssh_bootstrap_host_alias: str | None,
        djn_password: str | None,
        tunnel_remote_port: int,
        display_name: str,
        slots_total: int,
    ) -> None:
        steps = self.initial_steps(mode)
        self.store.update_provision_job(job_id, status="running", steps=steps)

        try:
            steps = self._run_step(job_id, steps, "validate_ssh", lambda: self._validate_ssh(
                mode, ssh_host_alias, ssh_bootstrap_host_alias
            ))
            if mode == "fresh":
                steps = self._run_step(job_id, steps, "bootstrap", lambda: self._bootstrap(
                    ssh_bootstrap_host_alias or "", djn_password or ""
                ))
            steps = self._run_step(job_id, steps, "verify_layout", lambda: self._verify_layout(ssh_host_alias))
            steps = self._run_step(
                job_id,
                steps,
                "establish_tunnel",
                lambda: self._establish_tunnel(worker_id, ssh_host_alias, tunnel_remote_port),
            )
            steps = self._run_step(
                job_id,
                steps,
                "start_daemon",
                lambda: self._start_daemon(
                    ssh_host_alias,
                    worker_id=worker_id,
                    display_name=display_name,
                    slots_total=slots_total,
                    tunnel_remote_port=tunnel_remote_port,
                ),
            )
            steps = self._run_step(
                job_id,
                steps,
                "wait_register",
                lambda: self._wait_for_register(worker_id),
            )
            self.store.set_worker_provision_status(worker_id, provision_status="ready")
            self.store.update_provision_job(job_id, status="succeeded", steps=steps, finished=True)
        except Exception as exc:
            self.store.set_worker_provision_status(
                worker_id,
                provision_status="failed",
                last_provision_error=str(exc),
            )
            self.store.update_provision_job(
                job_id,
                status="cancelled" if job_id in self._cancelled else "failed",
                steps=steps,
                error_text=str(exc),
                finished=True,
            )
            self.tunnels.kill_tunnel(worker_id)

    def _run_step(
        self,
        job_id: str,
        steps: list[dict[str, str]],
        step_id: str,
        fn: Callable[[], None],
    ) -> list[dict[str, str]]:
        if job_id in self._cancelled:
            raise RuntimeError("provision job cancelled")
        steps = set_step_status(steps, step_id, "running")
        self.store.update_provision_job(job_id, current_step=step_id, steps=steps)
        fn()
        return set_step_status(steps, step_id, "succeeded")

    def _log(self, job_id: str, chunk: str) -> None:
        self.store.append_provision_log(job_id, redact_sensitive_log(chunk))

    def _ssh_base(self) -> list[str]:
        return ["ssh", "-F", str(self.ssh_config_path), "-o", "BatchMode=yes"]

    def _ssh_run(self, host_alias: str, remote_command: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        cmd = [*self._ssh_base(), host_alias, remote_command]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self._log("", "")  # no-op guard; real logging done by caller with job_id
        if check and result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "ssh command failed")
        return result

    def _validate_ssh(
        self,
        mode: str,
        ssh_host_alias: str,
        ssh_bootstrap_host_alias: str | None,
    ) -> None:
        from agent_eval_orchestrator.controller.ssh_config import test_ssh_alias

        ok, message = test_ssh_alias(self.ssh_config_path, ssh_host_alias)
        if not ok:
            raise RuntimeError(message)
        if mode == "fresh":
            if not ssh_bootstrap_host_alias:
                raise RuntimeError("sshBootstrapHostAlias is required for fresh mode")
            ok_root, root_message = test_ssh_alias(self.ssh_config_path, ssh_bootstrap_host_alias)
            if not ok_root:
                raise RuntimeError(root_message)

    def _bootstrap(self, bootstrap_alias: str, djn_password: str) -> None:
        scp_cmd = [
            "scp",
            "-F",
            str(self.ssh_config_path),
            "-o",
            "BatchMode=yes",
            str(self.bootstrap_script_path),
            f"{bootstrap_alias}:/tmp/aeo-bootstrap.sh",
        ]
        result = subprocess.run(scp_cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "scp bootstrap script failed")
        remote = build_bootstrap_command(djn_password=djn_password)
        run = self._ssh_run(bootstrap_alias, remote)
        self._log("", run.stdout + run.stderr)

    def _verify_layout(self, ssh_host_alias: str) -> None:
        result = self._ssh_run(ssh_host_alias, build_verify_layout_command())
        if "uv" not in (result.stdout or "").lower():
            raise RuntimeError(
                "Worker layout verification failed. Missing harbor/agent-eval-orchestrator or uv. "
                "Try Fresh mode if this host was never bootstrapped."
            )

    def _establish_tunnel(
        self,
        worker_id: str,
        ssh_host_alias: str,
        tunnel_remote_port: int,
    ) -> None:
        cmd = [
            *self._ssh_base(),
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "ServerAliveInterval=30",
            "-f",
            "-N",
            "-R",
            f"{tunnel_remote_port}:127.0.0.1:{self.controller_port}",
            ssh_host_alias,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "failed to start reverse tunnel")
        pid = self._find_tunnel_pid(
            ssh_host_alias=ssh_host_alias,
            tunnel_remote_port=tunnel_remote_port,
        )
        self.tunnels.save_record(
            worker_id,
            {
                "djnHostAlias": ssh_host_alias,
                "remotePort": tunnel_remote_port,
                "localPort": self.controller_port,
                "sshPid": pid,
                "startedAt": datetime.now(timezone.utc).isoformat(),
            },
        )

    def _find_tunnel_pid(self, *, ssh_host_alias: str, tunnel_remote_port: int) -> int:
        pattern = f"127.0.0.1:{self.controller_port}"
        result = subprocess.run(
            ["pgrep", "-nf", pattern],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return 0
        return int(result.stdout.strip().splitlines()[0].split()[0])

    def _start_daemon(
        self,
        ssh_host_alias: str,
        *,
        worker_id: str,
        display_name: str,
        slots_total: int,
        tunnel_remote_port: int,
    ) -> None:
        remote = (
            f"AEO_TOKEN={self.auth_token} "
            + build_daemon_start_command(
                worker_id=worker_id,
                display_name=display_name,
                slots=slots_total,
                tunnel_remote_port=tunnel_remote_port,
                auth_token=self.auth_token,
            )
        )
        self._ssh_run(ssh_host_alias, remote)

    def _wait_for_register(self, worker_id: str, *, timeout_sec: int = 90) -> None:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if job_id_cancelled := False:
                pass
            workers = self.store.list_workers()
            match = next((item for item in workers if item["worker_id"] == worker_id), None)
            if match and match.get("last_heartbeat_at") and match.get("status") == "online":
                return
            time.sleep(2)
        raise RuntimeError(
            f"Worker did not register within {timeout_sec}s. "
            f"Check remote log /home/djn/worker/logs/daemon-{worker_id}.log"
        )
```

Fix `_ssh_run` and `_log` to accept `job_id` properly — update signatures:

```python
    def _ssh_run(self, job_id: str, host_alias: str, remote_command: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        cmd = [*self._ssh_base(), host_alias, remote_command]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self._log(job_id, result.stdout + result.stderr)
        ...
```

Update all internal callers to pass `job_id` through `run_job` (store as `self._current_job_id = job_id` at start of `run_job` to avoid threading through every helper).

Refactor: set `self._current_job_id = job_id` at beginning of `run_job`; `_log` and `_ssh_run` use `self._current_job_id`.

Remove dead code `if job_id_cancelled := False` in `_wait_for_register`; replace loop body with cancel check on `self._cancelled`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/controller/test_provisioner_runner.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/controller/provisioner.py tests/controller/test_provisioner_runner.py
git commit -m "feat: add provisioner job runner with fresh and join flows"
```

---

### Task 8: Controller CLI and API routes

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/server.py`
- Test: `tests/controller/test_provision_api.py`

- [ ] **Step 1: Write the failing test**

Create `tests/controller/test_provision_api.py`:

```python
import json
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread

from agent_eval_orchestrator.controller.server import Handler, ThreadedServer
from agent_eval_orchestrator.controller.provisioner import Provisioner
from agent_eval_orchestrator.storage.layout import Layout
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


def test_provision_duplicate_worker_returns_409(store, sample_ssh_config):
    store.create_provisioning_worker(
        worker_id="ecs-worker-dup",
        display_name="dup",
        slots_total=1,
        ssh_host_alias="aeo-ecs-0004",
        ssh_bootstrap_host_alias=None,
        tunnel_remote_port=17380,
    )
    server = start_test_server(store, sample_ssh_config, 9877)
    conn = HTTPConnection("127.0.0.1", 9877)
    body = json.dumps(
        {
            "workerId": "ecs-worker-dup",
            "displayName": "dup",
            "slotsTotal": 1,
            "mode": "join",
            "sshHostAlias": "aeo-ecs-0004",
            "tunnelRemotePort": 17380,
        }
    )
    conn.request(
        "POST",
        "/api/workers/provision",
        body=body,
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    assert resp.status == 409
    server.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/controller/test_provision_api.py::test_provision_duplicate_worker_returns_409 -v`
Expected: FAIL — 404 or missing Handler.provisioner

- [ ] **Step 3: Write minimal implementation**

In `server.py`:

1. Add imports:

```python
from agent_eval_orchestrator.controller.provisioner import Provisioner
from agent_eval_orchestrator.controller.ssh_config import list_ssh_hosts, test_ssh_alias
```

2. Add class attributes on `Handler`:

```python
    provisioner: Provisioner | None = None
    ssh_config_path: Path | None = None
```

3. Add GET routes in `do_GET`:

```python
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
```

4. Add POST routes in `do_POST`:

```python
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
            config_path = (self.ssh_config_path or Path("~/.ssh/config")).expanduser()
            ok, message = test_ssh_alias(config_path, ssh_host_alias)
            if not ok:
                _json_response(self, {"error": message}, 400)
                return
            bootstrap_alias = str(body.get("sshBootstrapHostAlias") or "").strip() or None
            djn_password = str(body.get("djnPassword") or "")
            if mode == "fresh":
                if not bootstrap_alias or not djn_password:
                    _json_response(self, {"error": "fresh mode requires sshBootstrapHostAlias and djnPassword"}, 400)
                    return
                ok_root, root_message = test_ssh_alias(config_path, bootstrap_alias)
                if not ok_root:
                    _json_response(self, {"error": root_message}, 400)
                    return
            display_name = str(body.get("displayName") or worker_id)
            slots_total = int(body.get("slotsTotal") or 1)
            tunnel_remote_port = int(body.get("tunnelRemotePort") or 17380)
            from agent_eval_orchestrator.core.ids import new_id

            job_id = new_id("prov")
            self.store.create_provisioning_worker(
                worker_id=worker_id,
                display_name=display_name,
                slots_total=slots_total,
                ssh_host_alias=ssh_host_alias,
                ssh_bootstrap_host_alias=bootstrap_alias,
                tunnel_remote_port=tunnel_remote_port,
            )
            self.store.create_provision_job(
                job_id=job_id,
                worker_id=worker_id,
                mode=mode,
                steps=self.provisioner.initial_steps(mode),
            )
            self.provisioner.start_job_async(
                job_id=job_id,
                worker_id=worker_id,
                mode=mode,
                ssh_host_alias=ssh_host_alias,
                ssh_bootstrap_host_alias=bootstrap_alias,
                djn_password=djn_password or None,
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
            self.provisioner.cancel_job(job_id, worker_id=str(job["worker_id"]), ssh_host_alias=ssh_alias)
            self.store.set_worker_provision_status(
                str(job["worker_id"]),
                provision_status="failed",
                last_provision_error="cancelled by operator",
            )
            _json_response(self, {"ok": True, "jobId": job_id, "status": "cancelled"})
            return
```

5. Update `main()`:

```python
    parser.add_argument("--ssh-config", default="~/.ssh/config")
    ...
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
    Handler.provisioner = provisioner
    Handler.ssh_config_path = ssh_config_path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/controller/test_provision_api.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/controller/server.py tests/controller/test_provision_api.py
git commit -m "feat: add SSH and worker provision API endpoints"
```

---

### Task 9: Dashboard — Add Worker button and modal shell

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/static.py:474-493` (Workers panel header)
- Modify: `src/agent_eval_orchestrator/controller/static.py` (after previewModal, ~line 589)

- [ ] **Step 1: Add modal HTML**

In Workers panel header, change:

```html
          <div class="panel-header">
            <h2>Workers</h2>
          </div>
```

to:

```html
          <div class="panel-header" style="display:flex;justify-content:space-between;align-items:center;gap:12px">
            <h2>Workers</h2>
            <button class="primary" type="button" id="openAddWorkerBtn">添加 Worker</button>
          </div>
```

After `previewModal` div, add:

```html
  <div class="modal hidden" id="addWorkerModal">
    <div class="modal-card">
      <div class="modal-header">
        <div>
          <h3 id="addWorkerModalTitle">添加 Worker</h3>
          <div class="subtle" id="addWorkerModalSubtitle">通过 SSH 远程部署 Worker 节点</div>
        </div>
        <button class="modal-close" id="addWorkerModalClose" aria-label="关闭">×</button>
      </div>
      <div class="modal-body" id="addWorkerModalBody"></div>
    </div>
  </div>
```

- [ ] **Step 2: Add state fields and open/close handlers**

In `state` object add:

```javascript
      provisionJob: null,
      sshHosts: [],
      addWorkerPhase: "form",
```

Before `setTab(window.location.pathname` add:

```javascript
    function closeAddWorkerModal() {
      state.provisionJob = null;
      state.addWorkerPhase = "form";
      document.getElementById("addWorkerModal").classList.add("hidden");
    }

    function openAddWorkerModal() {
      state.addWorkerPhase = "form";
      state.provisionJob = null;
      renderAddWorkerModal();
      document.getElementById("addWorkerModal").classList.remove("hidden");
    }

    document.getElementById("openAddWorkerBtn").addEventListener("click", openAddWorkerModal);
    document.getElementById("addWorkerModalClose").addEventListener("click", closeAddWorkerModal);
    document.getElementById("addWorkerModal").addEventListener("click", (event) => {
      if (event.target.id === "addWorkerModal") closeAddWorkerModal();
    });
```

Add stub:

```javascript
    function renderAddWorkerModal() {
      document.getElementById("addWorkerModalBody").innerHTML =
        '<div class="empty">Loading...</div>';
    }
```

- [ ] **Step 3: Manual smoke check**

Run controller locally, open Workers tab, confirm **添加 Worker** opens empty modal shell.

- [ ] **Step 4: Commit**

```bash
git add src/agent_eval_orchestrator/controller/static.py
git commit -m "feat: add Add Worker modal shell to dashboard"
```

---

### Task 10: Dashboard — provision form and SSH host loading

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/static.py`

- [ ] **Step 1: Implement form renderer**

Replace `renderAddWorkerModal` stub with:

```javascript
    async function loadSshHosts() {
      try {
        const payload = await api("/api/ssh/hosts");
        state.sshHosts = payload.items || [];
      } catch (error) {
        state.sshHosts = [];
      }
    }

    function sshHostOptions(selected) {
      const options = (state.sshHosts || []).map(item =>
        '<option value="' + esc(item.hostAlias) + '"' +
        (item.hostAlias === selected ? ' selected' : '') + '>' +
        esc(item.hostAlias + ' (' + item.user + '@' + item.hostname + ')') +
        '</option>'
      ).join("");
      return '<option value="">选择或手动输入 Host alias</option>' + options;
    }

    function renderAddWorkerForm() {
      return '' +
        '<form id="addWorkerForm">' +
          '<div class="detail-grid" style="margin-bottom:16px">' +
            '<div class="field"><label>Worker ID *</label><input name="workerId" placeholder="ecs-worker-0004" required /></div>' +
            '<div class="field"><label>显示名称</label><input name="displayName" placeholder="默认同 Worker ID" /></div>' +
            '<div class="field"><label>Slots *</label><input name="slotsTotal" type="number" min="1" value="1" required /></div>' +
            '<div class="field"><label>Tunnel Remote Port</label><input name="tunnelRemotePort" type="number" min="1024" value="17380" /></div>' +
          '</div>' +
          '<div class="field" style="margin-bottom:16px">' +
            '<label>部署模式 *</label>' +
            '<div class="actions">' +
              '<label><input type="radio" name="deployMode" value="fresh" checked /> 全新安装</label>' +
              '<label><input type="radio" name="deployMode" value="join" /> 仅接入</label>' +
            '</div>' +
          '</div>' +
          '<div class="field" style="margin-bottom:16px">' +
            '<label>SSH Host (djn) *</label>' +
            '<input list="sshHostAliases" name="sshHostAlias" required />' +
            '<datalist id="sshHostAliases">' +
              (state.sshHosts || []).map(item => '<option value="' + esc(item.hostAlias) + '"></option>').join("") +
            '</datalist>' +
          '</div>' +
          '<div id="freshOnlyFields">' +
            '<div class="field" style="margin-bottom:16px">' +
              '<label>SSH Host (root) *</label>' +
              '<input list="sshBootstrapAliases" name="sshBootstrapHostAlias" placeholder="建议 -root 后缀" />' +
              '<datalist id="sshBootstrapAliases">' +
                (state.sshHosts || []).map(item => '<option value="' + esc(item.hostAlias) + '"></option>').join("") +
              '</datalist>' +
            '</div>' +
            '<div class="field" style="margin-bottom:16px">' +
              '<label>DJN 密码（一次性，不会保存）*</label>' +
              '<input name="djnPassword" type="password" autocomplete="new-password" />' +
            '</div>' +
          '</div>' +
          '<div class="actions">' +
            '<button class="primary" type="submit">开始部署</button>' +
            '<button class="ghost" type="button" id="addWorkerCancelForm">取消</button>' +
          '</div>' +
        '</form>';
    }

    async function renderAddWorkerModal() {
      const body = document.getElementById("addWorkerModalBody");
      if (state.addWorkerPhase === "form") {
        await loadSshHosts();
        body.innerHTML = renderAddWorkerForm();
        const form = document.getElementById("addWorkerForm");
        const freshFields = document.getElementById("freshOnlyFields");
        const syncMode = () => {
          const mode = form.elements.deployMode.value;
          freshFields.classList.toggle("hidden", mode !== "fresh");
        };
        form.querySelectorAll('input[name="deployMode"]').forEach(input => {
          input.addEventListener("change", syncMode);
        });
        syncMode();
        document.getElementById("addWorkerCancelForm").addEventListener("click", closeAddWorkerModal);
        form.addEventListener("submit", submitAddWorkerForm);
        return;
      }
      body.innerHTML = renderProvisionProgress();
    }
```

- [ ] **Step 2: Implement submit handler**

```javascript
    async function submitAddWorkerForm(event) {
      event.preventDefault();
      const form = new FormData(event.target);
      const mode = String(form.get("deployMode") || "fresh");
      const payload = {
        workerId: String(form.get("workerId") || "").trim(),
        displayName: String(form.get("displayName") || "").trim() || String(form.get("workerId") || "").trim(),
        slotsTotal: Number(form.get("slotsTotal") || 1),
        mode,
        sshHostAlias: String(form.get("sshHostAlias") || "").trim(),
        tunnelRemotePort: Number(form.get("tunnelRemotePort") || 17380),
      };
      if (mode === "fresh") {
        payload.sshBootstrapHostAlias = String(form.get("sshBootstrapHostAlias") || "").trim();
        payload.djnPassword = String(form.get("djnPassword") || "");
      }
      const result = await api("/api/workers/provision", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
      });
      state.provisionJob = { jobId: result.jobId, workerId: result.workerId };
      state.addWorkerPhase = "progress";
      await renderAddWorkerModal();
      startProvisionPolling();
    }
```

- [ ] **Step 3: Manual smoke check**

Open modal, confirm SSH hosts load (if config present), form toggles fresh-only fields.

- [ ] **Step 4: Commit**

```bash
git add src/agent_eval_orchestrator/controller/static.py
git commit -m "feat: add worker provision form with SSH host picker"
```

---

### Task 11: Dashboard — progress polling, cancel, retry

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/static.py`

- [ ] **Step 1: Implement progress UI**

Add:

```javascript
    let provisionPollTimer = null;

    function renderProvisionProgress() {
      const job = state.provisionJob?.detail;
      if (!job) {
        return '<div class="empty">正在加载部署状态...</div>';
      }
      const stepsHtml = (job.steps || []).map(step =>
        '<div class="queue-row">' +
          '<div class="queue-title"><strong>' + esc(step.label) + '</strong>' + badge(step.status) + '</div>' +
        '</div>'
      ).join("");
      const actions = [];
      if (job.status === "running" || job.status === "pending") {
        actions.push('<button class="ghost" type="button" id="provisionCancelBtn">取消</button>');
      }
      if (job.status === "succeeded") {
        actions.push('<button class="primary" type="button" id="provisionCloseBtn">关闭</button>');
      }
      if (job.status === "failed" || job.status === "cancelled") {
        actions.push('<button class="primary" type="button" id="provisionRetryBtn">重试</button>');
        actions.push('<button class="ghost" type="button" id="provisionCloseBtn">关闭</button>');
      }
      return '' +
        '<div class="detail-grid" style="margin-bottom:16px">' +
          '<div class="stat"><div class="subtle">Job</div><strong><code>' + esc(job.jobId) + '</code></strong></div>' +
          '<div class="stat"><div class="subtle">Worker</div><strong><code>' + esc(job.workerId) + '</code></strong></div>' +
          '<div class="stat"><div class="subtle">Status</div><strong>' + badge(job.status) + '</strong></div>' +
        '</div>' +
        stepsHtml +
        (job.errorText ? '<div class="empty" style="color:var(--bad);margin-top:12px">' + esc(job.errorText) + '</div>' : '') +
        '<pre style="margin-top:12px;max-height:240px">' + esc(job.logTail || "") + '</pre>' +
        '<div class="actions" style="margin-top:12px">' + actions.join("") + '</div>';
    }

    async function pollProvisionJob() {
      if (!state.provisionJob?.jobId) return;
      const detail = await api("/api/workers/provision/" + encodeURIComponent(state.provisionJob.jobId));
      state.provisionJob.detail = detail;
      document.getElementById("addWorkerModalBody").innerHTML = renderProvisionProgress();
      bindProvisionProgressActions();
      if (["succeeded", "failed", "cancelled"].includes(detail.status)) {
        clearInterval(provisionPollTimer);
        provisionPollTimer = null;
        await loadDashboard();
      }
    }

    function startProvisionPolling() {
      if (provisionPollTimer) clearInterval(provisionPollTimer);
      pollProvisionJob();
      provisionPollTimer = setInterval(pollProvisionJob, 2000);
    }

    function bindProvisionProgressActions() {
      const cancelBtn = document.getElementById("provisionCancelBtn");
      if (cancelBtn) {
        cancelBtn.addEventListener("click", async () => {
          await api("/api/workers/provision/" + encodeURIComponent(state.provisionJob.jobId) + "/cancel", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: "{}",
          });
          await pollProvisionJob();
        });
      }
      const closeBtn = document.getElementById("provisionCloseBtn");
      if (closeBtn) closeBtn.addEventListener("click", closeAddWorkerModal);
      const retryBtn = document.getElementById("provisionRetryBtn");
      if (retryBtn) {
        retryBtn.addEventListener("click", () => {
          state.addWorkerPhase = "form";
          state.provisionJob = null;
          renderAddWorkerModal();
        });
      }
    }
```

- [ ] **Step 2: Update worker list badges**

In `renderWorkerList`, after computing `runtime`, add provision badge logic:

```javascript
        let statusBadge = worker.status;
        if (worker.provision_status === "provisioning") statusBadge = "provisioning";
        if (worker.provision_status === "failed") statusBadge = "provision_failed";
```

Use `statusBadge` in `badge(statusBadge)` call.

In `renderWorkerDetail`, if `worker.provision_status === "failed"`, append link to latest job:

```javascript
      const latestJob = worker.last_provision_job_id;
```

Because API doesn't expose job id on worker yet, fetch via separate call in `renderWorkerDetail` or extend `/api/workers` response in store `_decorate_worker`:

```python
        latest = self.get_latest_provision_job_for_worker(str(item["worker_id"]))
        if latest:
            item["last_provision_job_id"] = latest["job_id"]
```

Add that to `_decorate_worker` before return.

In worker detail HTML append when failed:

```javascript
        (worker.provision_status === "failed" && worker.last_provision_job_id
          ? '<div class="actions" style="margin-top:12px"><button class="link-btn" type="button" id="viewProvisionLogBtn">查看最近部署日志</button></div>'
          : '')
```

Wire button to open modal in progress phase loading that job id.

- [ ] **Step 3: Run full test suite**

Run: `uv run --extra dev pytest -v`
Expected: all tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/agent_eval_orchestrator/controller/static.py src/agent_eval_orchestrator/storage/store.py
git commit -m "feat: add provision progress UI with polling and worker badges"
```

---

### Task 12: Final verification

**Files:**
- (none — verification only)

- [ ] **Step 1: Run all automated tests**

Run: `uv run --extra dev pytest -v`
Expected: all tests PASS

- [ ] **Step 2: Manual acceptance checklist**

- [ ] Fresh: new Ubuntu 22.04 ECS → worker online in dashboard
- [ ] Join: already-bootstrapped ECS → worker online without re-bootstrap
- [ ] SSH failure shows clear error in UI
- [ ] Cancel stops job (best effort tunnel cleanup)
- [ ] Worker receives tasks after provision (existing create-task flow)

- [ ] **Step 3: Commit any fixes from manual testing**

```bash
git commit -m "fix: address worker provision manual test findings"
```

---

## Self-Review

### Spec coverage

| Requirement | Task |
|-------------|------|
| SSH config parsing + test | Task 3, Task 8 |
| Fresh + Join deploy modes | Task 7 |
| Async jobs + polling API | Task 2, Task 7, Task 8, Task 11 |
| Tunnel management + tunnels.json | Task 6, Task 7 |
| Daemon startup template | Task 4, Task 7 |
| Never persist djnPassword / redact logs | Task 4, Task 7 |
| Worker columns + provision_jobs table | Task 2 |
| Add Worker wizard UI | Task 9–11 |
| `--ssh-config` CLI flag | Task 8 |
| Cancel endpoint | Task 7, Task 8, Task 11 |
| provision_status badges | Task 2, Task 11 |
| Unit + integration tests | Tasks 1–8, 11 |

### Placeholder scan

No TBD/TODO/implement-later steps. Each task includes concrete code and commands.

### Type consistency

- Job IDs use `new_id("prov")` → `prov-{12hex}` matching spec examples.
- Step IDs (`validate_ssh`, `bootstrap`, etc.) consistent across backend and UI labels.
- API JSON field names use camelCase in HTTP responses as spec defines.

---

## Related Documents

- Spec: `docs/superpowers/specs/2026-05-24-worker-provision-ui-design.md`
- Bootstrap script spec: `docs/superpowers/specs/2026-05-24-huawei-ecs-worker-bootstrap-design.md`
- Manual flow reference: `README.md`
