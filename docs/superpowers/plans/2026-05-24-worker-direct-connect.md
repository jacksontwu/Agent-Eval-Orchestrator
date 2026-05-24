# Worker Direct Connect Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Default Worker provisioning to internal-IP direct connection; keep SSH reverse tunnel as an advanced legacy option.

**Architecture:** Add `connectionMode` (`direct`|`tunnel`) end-to-end. Direct mode skips `establish_tunnel`, starts daemon with `http://<controllerInternalIp>:<port>`, stores `connection_mode` on worker row. UI replaces Tunnel Remote Port with Controller 内网 IP; tunnel fields live in collapsed advanced section.

**Tech Stack:** Python 3.10+, stdlib HTTP/SQLite, embedded dashboard JS, pytest

**Spec:** `docs/superpowers/specs/2026-05-24-worker-direct-connect-design.md`

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/agent_eval_orchestrator/controller/provisioner.py` | `build_daemon_start_command(controller_url)`, step lists by mode, `run_job` branching, decommission by connection_mode |
| `src/agent_eval_orchestrator/storage/store.py` | Schema migration, `create_provisioning_worker` new fields, set worker host from SSH |
| `src/agent_eval_orchestrator/controller/server.py` | Provision API validation for `connectionMode` / IPs |
| `src/agent_eval_orchestrator/controller/static.py` | Form UI: Controller IP + advanced tunnel toggle |
| `tests/controller/test_provisioner_templates.py` | Daemon command URL tests |
| `tests/controller/test_provisioner_connection_mode.py` | Step lists + run_job branching (new) |
| `tests/controller/test_provision_api.py` | API validation integration tests |
| `tests/storage/test_provision_store.py` | Store fields for connection_mode |

---

### Task 1: `build_daemon_start_command` accepts controller URL

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/provisioner.py:46-70`
- Modify: `tests/controller/test_provisioner_templates.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/controller/test_provisioner_templates.py`:

```python
def test_build_daemon_start_command_direct_url():
    cmd = build_daemon_start_command(
        worker_id="ecs-worker-0004",
        display_name="ecs-worker-0004",
        slots=2,
        controller_url="http://192.168.0.211:7380",
        auth_token="secret-token-value",
    )
    assert '--controller-url "http://192.168.0.211:7380"' in cmd
    assert "127.0.0.1" not in cmd


def test_build_daemon_start_command_tunnel_loopback():
    cmd = build_daemon_start_command(
        worker_id="ecs-worker-0004",
        display_name="ecs-worker-0004",
        slots=2,
        controller_url="http://127.0.0.1:17380",
        auth_token="secret-token-value",
    )
    assert '--controller-url "http://127.0.0.1:17380"' in cmd
```

Update existing `test_build_daemon_start_command` to use `controller_url=` instead of `tunnel_remote_port=`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/djn/code/Agent-Eval-Orchestrator && uv run --extra dev pytest tests/controller/test_provisioner_templates.py -v`
Expected: FAIL — unexpected keyword `controller_url`

- [ ] **Step 3: Write minimal implementation**

In `provisioner.py`, change signature:

```python
def build_daemon_start_command(
    *,
    worker_id: str,
    display_name: str,
    slots: int,
    controller_url: str,
    auth_token: str,
) -> str:
    ...
        f'--controller-url "{controller_url}" '
```

Update `_start_daemon` call sites in same file (Task 3 will pass URL through).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/controller/test_provisioner_templates.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/controller/provisioner.py tests/controller/test_provisioner_templates.py
git commit -m "refactor: build_daemon_start_command takes controller_url"
```

---

### Task 2: Step lists exclude tunnel for direct mode

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/provisioner.py:73-102`
- Create: `tests/controller/test_provisioner_connection_mode.py`

- [ ] **Step 1: Write the failing test**

Create `tests/controller/test_provisioner_connection_mode.py`:

```python
from agent_eval_orchestrator.controller.provisioner import initial_steps_for_mode


def test_initial_steps_direct_join_excludes_tunnel():
    steps = initial_steps_for_mode("join", connection_mode="direct")
    assert [s["id"] for s in steps] == [
        "validate_ssh",
        "verify_layout",
        "start_daemon",
        "wait_register",
    ]


def test_initial_steps_tunnel_join_includes_tunnel():
    steps = initial_steps_for_mode("join", connection_mode="tunnel")
    assert [s["id"] for s in steps] == [
        "validate_ssh",
        "verify_layout",
        "establish_tunnel",
        "start_daemon",
        "wait_register",
    ]


def test_initial_steps_direct_fresh_excludes_tunnel():
    steps = initial_steps_for_mode("fresh", connection_mode="direct")
    assert "establish_tunnel" not in [s["id"] for s in steps]
    assert steps[0]["id"] == "validate_ssh"
    assert "bootstrap" in [s["id"] for s in steps]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/controller/test_provisioner_connection_mode.py -v`
Expected: FAIL — `initial_steps_for_mode()` got unexpected keyword `connection_mode`

- [ ] **Step 3: Write minimal implementation**

```python
TUNNEL_STEP_ID = "establish_tunnel"

def initial_steps_for_mode(mode: str, *, connection_mode: str = "direct") -> list[dict[str, str]]:
    ids = FRESH_STEP_IDS if mode == "fresh" else JOIN_STEP_IDS
    if connection_mode == "direct":
        ids = [step_id for step_id in ids if step_id != TUNNEL_STEP_ID]
    return [{"id": step_id, "label": STEP_LABELS[step_id], "status": "pending"} for step_id in ids]
```

Update `Provisioner.initial_steps` to accept and forward `connection_mode`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/controller/test_provisioner_connection_mode.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/controller/provisioner.py tests/controller/test_provisioner_connection_mode.py
git commit -m "feat: omit establish_tunnel step for direct connection mode"
```

---

### Task 3: Store schema + `create_provisioning_worker` connection fields

**Files:**
- Modify: `src/agent_eval_orchestrator/storage/store.py` (~130-150, 537-570)
- Modify: `tests/storage/test_provision_store.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/storage/test_provision_store.py`:

```python
def test_create_provisioning_worker_direct_mode(store):
    worker = store.create_provisioning_worker(
        worker_id="ecs-worker-direct",
        display_name="ecs-worker-direct",
        slots_total=1,
        ssh_host_alias="aeo-ecs-0004",
        ssh_bootstrap_host_alias=None,
        connection_mode="direct",
        controller_internal_ip="192.168.0.211",
        tunnel_remote_port=None,
    )
    assert worker["connection_mode"] == "direct"
    assert worker["controller_internal_ip"] == "192.168.0.211"
    assert worker["tunnel_remote_port"] is None


def test_create_provisioning_worker_tunnel_mode(store):
    worker = store.create_provisioning_worker(
        worker_id="ecs-worker-tunnel",
        display_name="ecs-worker-tunnel",
        slots_total=1,
        ssh_host_alias="aeo-ecs-0004",
        ssh_bootstrap_host_alias=None,
        connection_mode="tunnel",
        controller_internal_ip=None,
        tunnel_remote_port=17380,
    )
    assert worker["connection_mode"] == "tunnel"
    assert worker["tunnel_remote_port"] == 17380
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/storage/test_provision_store.py::test_create_provisioning_worker_direct_mode -v`
Expected: FAIL — unexpected keyword `connection_mode`

- [ ] **Step 3: Write minimal implementation**

In `_ensure_schema` migrations dict, add:

```python
"connection_mode": "TEXT NOT NULL DEFAULT 'direct'",
"controller_internal_ip": "TEXT",
```

Alter `tunnel_remote_port` handling: new installs use nullable; migration:

```python
# after column add loop, once:
conn.execute("UPDATE workers SET connection_mode = 'tunnel' WHERE tunnel_remote_port IS NOT NULL AND ssh_host_alias != '' AND connection_mode = 'direct'")
```

Update `create_provisioning_worker`:

```python
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
```

Include new columns in INSERT. Extend `_worker_item` / `_decorate_worker` to expose fields.

Add `update_worker_host(worker_id, host)` for post-register host assignment.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/storage/test_provision_store.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/storage/store.py tests/storage/test_provision_store.py
git commit -m "feat: store connection_mode and controller_internal_ip on workers"
```

---

### Task 4: Provision API validation

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/server.py:936-999`
- Modify: `tests/controller/test_provision_api.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/controller/test_provision_api.py`:

```python
def test_provision_direct_requires_controller_ip(store, sample_ssh_config):
    server = start_test_server(store, sample_ssh_config, 9876)
    conn = HTTPConnection("127.0.0.1", 9876)
    body = json.dumps(
        {
            "workerId": "ecs-worker-direct-api",
            "mode": "join",
            "sshHostAlias": "aeo-ecs-0004",
            "connectionMode": "direct",
        }
    )
    conn.request("POST", "/api/workers/provision", body=body, headers={"Content-Type": "application/json", "X-AEO-Token": "secret"})
    resp = conn.getresponse()
    assert resp.status == 400
    payload = json.loads(resp.read())
    assert "controllerInternalIp" in payload["error"]
    server.shutdown()


def test_provision_direct_accepts_valid_ip(store, sample_ssh_config):
    server = start_test_server(store, sample_ssh_config, 9875)
    conn = HTTPConnection("127.0.0.1", 9875)
    body = json.dumps(
        {
            "workerId": "ecs-worker-direct-api2",
            "mode": "join",
            "sshHostAlias": "aeo-ecs-0004",
            "connectionMode": "direct",
            "controllerInternalIp": "192.168.0.211",
        }
    )
    conn.request("POST", "/api/workers/provision", body=body, headers={"Content-Type": "application/json", "X-AEO-Token": "secret"})
    resp = conn.getresponse()
    assert resp.status == 201
    server.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/controller/test_provision_api.py::test_provision_direct_requires_controller_ip -v`
Expected: FAIL — 201 or wrong error

- [ ] **Step 3: Write minimal implementation**

Add helper in `server.py`:

```python
def _validate_controller_internal_ip(value: str) -> bool:
    import ipaddress
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return bool(value and " " not in value and len(value) <= 253)
```

In provision handler:

```python
connection_mode = str(body.get("connectionMode") or "direct").strip()
if connection_mode not in {"direct", "tunnel"}:
    _json_response(self, {"error": "connectionMode must be direct or tunnel"}, 400)
    return
controller_internal_ip = str(body.get("controllerInternalIp") or "").strip() or None
tunnel_remote_port = int(body.get("tunnelRemotePort") or 17380) if body.get("tunnelRemotePort") is not None else 17380
if connection_mode == "direct":
    if not controller_internal_ip:
        _json_response(self, {"error": "direct mode requires controllerInternalIp"}, 400)
        return
    if not _validate_controller_internal_ip(controller_internal_ip):
        _json_response(self, {"error": "invalid controllerInternalIp"}, 400)
        return
    tunnel_remote_port = None
else:
    controller_internal_ip = None
    if tunnel_remote_port < 1024 or tunnel_remote_port > 65535:
        _json_response(self, {"error": "tunnelRemotePort out of range"}, 400)
        return
```

Pass new fields to `create_provisioning_worker`, `initial_steps_for_mode(mode, connection_mode=...)`, and `start_job_async`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/controller/test_provision_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/controller/server.py tests/controller/test_provision_api.py
git commit -m "feat: validate connectionMode on worker provision API"
```

---

### Task 5: Provisioner `run_job` direct vs tunnel branching

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/provisioner.py` (run_job, _start_daemon, decommission_worker)
- Modify: `tests/controller/test_provisioner_connection_mode.py`
- Modify: `tests/controller/test_provisioner_runner.py` (update kwargs)

- [ ] **Step 1: Write the failing test**

Append to `tests/controller/test_provisioner_connection_mode.py`:

```python
from unittest.mock import MagicMock, patch
from pathlib import Path
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


def test_run_job_direct_skips_tunnel(store, sample_ssh_config):
    provisioner = _provisioner(store, sample_ssh_config)
    store.create_provisioning_worker(
        worker_id="w-direct",
        display_name="w-direct",
        slots_total=1,
        ssh_host_alias="aeo-ecs-0004",
        ssh_bootstrap_host_alias=None,
        connection_mode="direct",
        controller_internal_ip="192.168.0.211",
        tunnel_remote_port=None,
    )
    job_id = "prov-direct"
    store.create_provision_job(job_id=job_id, worker_id="w-direct", mode="join", steps=provisioner.initial_steps("join", connection_mode="direct"))
    with patch.object(provisioner, "_establish_tunnel") as tunnel, \
         patch.object(provisioner, "_validate_ssh"), \
         patch.object(provisioner, "_verify_layout"), \
         patch.object(provisioner, "_start_daemon") as start_daemon, \
         patch.object(provisioner, "_wait_for_register"):
        provisioner.run_job(
            job_id=job_id,
            worker_id="w-direct",
            mode="join",
            ssh_host_alias="aeo-ecs-0004",
            ssh_bootstrap_host_alias=None,
            djn_password=None,
            connection_mode="direct",
            controller_internal_ip="192.168.0.211",
            tunnel_remote_port=None,
            display_name="w-direct",
            slots_total=1,
        )
    tunnel.assert_not_called()
    start_daemon.assert_called_once()
    _, kwargs = start_daemon.call_args
    assert kwargs["controller_url"] == "http://192.168.0.211:7380"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/controller/test_provisioner_connection_mode.py::test_run_job_direct_skips_tunnel -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

Update `run_job` signature with `connection_mode`, `controller_internal_ip`, `tunnel_remote_port`.

Only run `establish_tunnel` when `connection_mode == "tunnel"`.

Compute `controller_url` and pass to `_start_daemon(..., controller_url=controller_url)`.

Refactor `_start_daemon` to accept `controller_url: str`.

After successful register in direct mode, resolve SSH HostName and call `store.update_worker_host`.

Update `decommission_worker` to accept optional `connection_mode`; skip `kill_tunnel` when `direct`.

Update `cancel_job` / delete worker handler to pass worker's `connection_mode`.

- [ ] **Step 4: Run tests**

Run: `uv run --extra dev pytest tests/controller/test_provisioner_connection_mode.py tests/controller/test_provisioner_runner.py tests/controller/test_provisioner_decommission.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/controller/provisioner.py tests/controller/test_provisioner_connection_mode.py tests/controller/test_provisioner_runner.py
git commit -m "feat: provision direct mode without reverse tunnel"
```

---

### Task 6: Dashboard form UI

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/static.py:1383-1500`

- [ ] **Step 1: Update form markup**

Replace tunnel port field in `renderAddWorkerForm()`:

```javascript
'<div class="field"><label>Controller 内网 IP *</label>' +
  '<input name="controllerInternalIp" placeholder="192.168.0.211" required />' +
  '<div class="subtle">在 Controller 上运行 ifconfig 或 ip addr 查看内网地址</div></div>' +
```

Add after detail-grid:

```javascript
'<details class="field" style="margin-bottom:16px">' +
  '<summary>高级选项</summary>' +
  '<label style="display:block;margin-top:12px">' +
    '<input type="checkbox" name="useTunnel" id="useTunnelCheckbox" /> 使用 SSH 反向隧道（旧方案，不推荐）' +
  '</label>' +
  '<div class="field hidden" id="tunnelPortField" style="margin-top:12px">' +
    '<label>Tunnel Remote Port</label>' +
    '<input name="tunnelRemotePort" type="number" min="1024" value="17380" />' +
  '</div>' +
'</details>' +
```

Wire toggle in `renderAddWorkerModal` after form render:

```javascript
const useTunnel = form.querySelector("#useTunnelCheckbox");
const tunnelField = form.querySelector("#tunnelPortField");
const controllerIpField = form.querySelector('[name="controllerInternalIp"]');
function syncConnectionMode() {
  const tunnel = useTunnel.checked;
  tunnelField.classList.toggle("hidden", !tunnel);
  controllerIpField.required = !tunnel;
  controllerIpField.closest(".field").classList.toggle("hidden", tunnel);
}
useTunnel.addEventListener("change", syncConnectionMode);
syncConnectionMode();
```

- [ ] **Step 2: Update submit payload in `submitAddWorkerForm`**

```javascript
const useTunnel = Boolean(form.get("useTunnel"));
const payload = {
  ...
  connectionMode: useTunnel ? "tunnel" : "direct",
};
if (useTunnel) {
  payload.tunnelRemotePort = Number(form.get("tunnelRemotePort") || 17380);
} else {
  payload.controllerInternalIp = String(form.get("controllerInternalIp") || "").trim();
}
```

- [ ] **Step 3: Manual smoke test**

Restart controller, open Add Worker → verify Controller IP default visible, tunnel hidden; check advanced → tunnel appears, IP hides.

- [ ] **Step 4: Run full test suite**

Run: `uv run --extra dev pytest tests/ -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/controller/static.py
git commit -m "feat: add worker form defaults to direct internal IP connect"
```

---

## Self-Review

**Spec coverage:**

| Requirement | Task |
|-------------|------|
| Default direct, tunnel advanced | Task 6 |
| Controller IP per-form, not global | Task 4, 6 |
| No Worker IP field; SSH HostName backend | Task 5 (host update) |
| Skip establish_tunnel for direct | Task 2, 5 |
| Daemon controller URL | Task 1, 5 |
| Store connection_mode | Task 3 |
| Decommission direct skips tunnel | Task 5 |
| API validation | Task 4 |

**Placeholder scan:** None.

**Type consistency:** `connection_mode`, `controller_internal_ip`, `controller_url` used consistently across store, API, provisioner, UI.

---

Plan complete and saved to `docs/superpowers/plans/2026-05-24-worker-direct-connect.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks
2. **Inline Execution** — execute in this session with checkpoints

Which approach?
