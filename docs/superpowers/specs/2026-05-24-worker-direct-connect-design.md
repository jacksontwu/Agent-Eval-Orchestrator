# Worker Direct Connect Design

## Goal

Change the default Worker provisioning path from **SSH reverse tunnel** to **direct internal-network connectivity** between Controller and Worker. SSH reverse tunnel remains available as an **advanced / legacy** option for environments that cannot route Controller ↔ Worker over private IP.

## Background

Current flow (`2026-05-24-worker-provision-ui-design.md`):

1. Controller SSHs to Worker
2. Establishes reverse tunnel: `-R <tunnelRemotePort>:127.0.0.1:<controllerPort>`
3. Starts daemon with `--controller-url http://127.0.0.1:<tunnelRemotePort>`

Problems with tunnel-first default:

- Extra moving part (tunnel process, port allocation, decommission cleanup)
- Assumes Worker reaches Controller via loopback on the remote side
- Operators already have internal IPs in `~/.ssh/config` (`HostName`)

## Decisions (confirmed with operator)

| Topic | Decision |
|-------|----------|
| Connection default | **Direct internal IP** |
| Legacy tunnel | **Advanced option**, collapsed by default |
| Controller internal IP | **Per provision form**, not persisted globally |
| Worker internal IP UI | **No separate field** — backend resolves from SSH `HostName` |
| Implementation shape | **`connectionMode` enum** (`direct` \| `tunnel`), full stack change |

## Non-Goals

- No global Controller settings page for internal IP
- No automatic network reachability probing
- No change to worker registration / claim / heartbeat protocol
- No firewall / security-group automation
- No migration of existing tunnel workers to direct (they keep working as tunnel)

## Architecture

```text
Add Worker form (default: direct)
    │
    ├─ connectionMode=direct
    │     controllerInternalIp (required, form only — also stored on worker row)
    │     sshHostAlias → backend resolves HostName for worker host metadata
    │     provisioner skips establish_tunnel
    │     daemon: --controller-url http://<controllerInternalIp>:<controllerPort>
    │
    └─ connectionMode=tunnel (advanced, collapsed)
          tunnelRemotePort (default 17380)
          existing establish_tunnel + loopback controller URL
```

### Provision steps by mode

| Step | fresh + direct | join + direct | fresh + tunnel | join + tunnel |
|------|----------------|---------------|----------------|---------------|
| validate_ssh | ✓ | ✓ | ✓ | ✓ |
| bootstrap | ✓ | — | ✓ | — |
| verify_layout | ✓ | ✓ | ✓ | ✓ |
| establish_tunnel | **skip** | **skip** | ✓ | ✓ |
| start_daemon | ✓ | ✓ | ✓ | ✓ |
| wait_register | ✓ | ✓ | ✓ | ✓ |

### Worker host metadata

On successful provision (`direct` mode):

- Resolve `HostName` from SSH config for `ssh_host_alias`
- Set `workers.host` to that internal IP (replacing empty string at create time)
- Daemon still passes `--host "$(hostname -f || hostname)"` at runtime; DB `host` reflects operator-facing internal IP from SSH config

## UI Design

### Default section (direct)

Replace visible **Tunnel Remote Port** with:

| Field | Required | Notes |
|-------|----------|-------|
| Controller 内网 IP | yes | placeholder `192.168.0.211`; hint: 在 Controller 上运行 `ifconfig` 或 `ip addr` 查看内网地址 |

Layout (detail-grid row):

```
Worker ID *     | 显示名称
Slots *         | Controller 内网 IP *
部署模式 *      | SSH Host (djn) *
```

### Advanced section (collapsed)

```
▸ 高级选项
  ☐ 使用 SSH 反向隧道（旧方案，不推荐）
    Tunnel Remote Port  [17380]   (visible only when checked)
```

Behavior:

- Unchecked (default): `connectionMode=direct`, Controller IP visible, tunnel port hidden
- Checked: `connectionMode=tunnel`, Controller IP hidden, tunnel port visible

### Progress UI

When `direct`, step list must not include **建立反向隧道**. Labels unchanged for other steps.

## API

### `POST /api/workers/provision`

Add / change request fields:

```json
{
  "connectionMode": "direct",
  "controllerInternalIp": "192.168.0.211"
}
```

Legacy path:

```json
{
  "connectionMode": "tunnel",
  "tunnelRemotePort": 17380
}
```

Validation:

| Field | direct | tunnel |
|-------|--------|--------|
| `connectionMode` | `"direct"` (default if omitted) | `"tunnel"` |
| `controllerInternalIp` | required; IPv4 or hostname | ignored |
| `tunnelRemotePort` | ignored | optional, default 17380, range 1024–65535 |

Error examples:

- `400` — `direct mode requires controllerInternalIp`
- `400` — `invalid controllerInternalIp`
- `400` — `tunnel mode requires tunnelRemotePort` (if explicitly invalid)

Response unchanged: `{ jobId, workerId, status }`.

### Internal job kwargs

`Provisioner.run_job` receives:

- `connection_mode: str`
- `controller_internal_ip: str | None`
- `tunnel_remote_port: int | None`

## Storage

### Schema migration (additive)

Add to `workers`:

| Column | Type | Default | Purpose |
|--------|------|---------|---------|
| `connection_mode` | TEXT NOT NULL | `'direct'` | `direct` or `tunnel` |
| `controller_internal_ip` | TEXT | NULL | Set for direct workers |

Change:

- `tunnel_remote_port` → nullable (NULL when `connection_mode='direct'`)

Existing rows: backfill `connection_mode='tunnel'` where `tunnel_remote_port IS NOT NULL AND ssh_host_alias != ''` (provisioning workers), else `'direct'` for manually registered workers without tunnel metadata.

### `create_provisioning_worker`

New parameters:

```python
connection_mode: str
controller_internal_ip: str | None
tunnel_remote_port: int | None
```

## Provisioner

### `build_daemon_start_command`

Replace `tunnel_remote_port: int` with `controller_url: str`:

```python
f'--controller-url "{controller_url}" '
```

Examples:

- direct: `http://192.168.0.211:7380`
- tunnel: `http://127.0.0.1:17380`

### `initial_steps_for_mode(mode, connection_mode)`

Return step list excluding `establish_tunnel` when `connection_mode == "direct"`.

### `run_job`

Branch before tunnel step:

```python
if connection_mode == "tunnel":
    steps = self._run_step(..., "establish_tunnel", ...)
controller_url = (
    f"http://{controller_internal_ip}:{self.controller_port}"
    if connection_mode == "direct"
    else f"http://127.0.0.1:{tunnel_remote_port}"
)
steps = self._run_step(..., "start_daemon", lambda: self._start_daemon(..., controller_url=controller_url))
```

After `wait_register` succeeds (direct mode), update worker `host` from SSH `HostName`.

### Decommission / delete

`decommission_worker`:

- `tunnel` → kill tunnel + pkill daemon (current behavior)
- `direct` → pkill daemon only (`remoteCleanup: skipped` for tunnel part)

Read `connection_mode` from worker row when deleting.

## Testing

| Area | Cases |
|------|-------|
| API validation | direct missing IP, invalid IP, tunnel port range, default mode |
| Step templates | direct excludes establish_tunnel; tunnel includes it |
| Daemon command | direct URL vs tunnel loopback URL |
| Provisioner run | direct skips tunnel mock; tunnel calls tunnel manager |
| Store | nullable tunnel port, connection_mode persisted |
| Decommission | direct skips kill_tunnel |

## Compatibility

- Existing tunnel-provisioned workers: `connection_mode='tunnel'`, decommission unchanged
- Default for new provisions: `direct`
- Omitting `connectionMode` in API → treat as `direct` (document in spec; acceptable breaking change for scripts still sending only `tunnelRemotePort` — they must opt into advanced tunnel explicitly)

## Related Docs

- `docs/superpowers/specs/2026-05-24-worker-provision-ui-design.md` — original tunnel-first design (superseded for default path)
- `docs/superpowers/specs/2026-05-24-worker-delete-design.md` — delete uses `decommission_worker`
