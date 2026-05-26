# Controller Lifecycle Script Design

## Goal

Add a single Bash script on the **controller machine** to start, stop, restart, and inspect the Agent Eval Orchestrator controller process. The script reads controller-only settings from a repo-root `.env` file, launches the server in the background with `uv run`, and writes logs under `runtime/logs/`.

## Non-Goals

This feature will **not**:

- Add worker start/stop scripts (local or remote workers remain managed via Provisioner / manual SSH).
- Introduce systemd units or Docker wrappers.
- Change controller server behavior or CLI flags beyond what the script passes through.
- Support multiple concurrent controller instances on one machine (one PID file per repo checkout).

## Requirements Summary

| Decision | Choice |
|----------|--------|
| Scope | Controller machine only |
| Script | `scripts/aeo-controller.sh` |
| Subcommands | `start`, `stop`, `restart`, `status` |
| Config source | Repo-root `.env` (controller variables only; gitignored) |
| Python launcher | `uv run python -u -m agent_eval_orchestrator.controller.server` |
| Background model | `setsid nohup ... &` |
| PID tracking | `runtime/controller.pid` |
| Logs | `runtime/logs/controller-{port}.log` (append) |
| Health check | `GET /api/health` after start |

## Chosen Approach

**Single-file Bash script** (`scripts/aeo-controller.sh`), consistent with the existing `scripts/bootstrap-huawei-worker.sh` style. No shared shell library or systemd integration.

Rationale: matches the operator's current manual startup (`nohup uv run ...`), minimal surface area, and no extra dependencies.

## Files

| File | Purpose |
|------|---------|
| `scripts/aeo-controller.sh` | CLI entry: `start` / `stop` / `restart` / `status` |
| `.env` | Controller runtime config (not committed) |
| `.env.example` | Documented template (committed, no secrets) |
| `runtime/controller.pid` | Written on start, removed on stop |
| `runtime/logs/controller-{port}.log` | Controller stdout/stderr |

The script resolves the repo root as the parent of `scripts/` and expects to be invoked from anywhere (internally `cd` to repo root).

## Environment Variables

Loaded via `set -a; source "${REPO_ROOT}/.env"; set +a` before any subcommand that needs config.

| Variable | Required | Default | Maps to |
|----------|----------|---------|---------|
| `AEO_HOST` | No | `127.0.0.1` | `--host` |
| `AEO_PORT` | No | `7380` | `--port` |
| `AEO_SHARED_ROOT` | Yes | — | `--shared-root` |
| `AEO_AUTH_TOKEN` | Yes | — | `--auth-token` |
| `AEO_SSH_CONFIG` | No | `~/.ssh/config` | `--ssh-config` |
| `AEO_GITHUB_TOKEN` | No | — | Process env (read by server as `AEO_GITHUB_TOKEN`) |

Validation on `start` / `status`:

- `.env` must exist; otherwise exit with message pointing to `.env.example`.
- `AEO_SHARED_ROOT` and `AEO_AUTH_TOKEN` must be non-empty.
- If `AEO_SHARED_ROOT` is relative, resolve against repo root before passing to the server.

Secrets must not be echoed by the script.

## Subcommand Behavior

### `start`

1. Load and validate `.env`.
2. If `runtime/controller.pid` exists and the PID is alive, exit with error (already running).
3. `mkdir -p runtime/logs` and ensure `AEO_SHARED_ROOT` exists.
4. Start detached:

```bash
setsid nohup env ${AEO_GITHUB_TOKEN:+AEO_GITHUB_TOKEN="$AEO_GITHUB_TOKEN"} \
  uv run python -u -m agent_eval_orchestrator.controller.server \
  --host "$AEO_HOST" \
  --port "$AEO_PORT" \
  --shared-root "$AEO_SHARED_ROOT" \
  --auth-token "$AEO_AUTH_TOKEN" \
  --ssh-config "$AEO_SSH_CONFIG" \
  >> "runtime/logs/controller-${AEO_PORT}.log" 2>&1 \
  < /dev/null &
```

5. Write `$!` to `runtime/controller.pid`.
6. Poll `http://127.0.0.1:${AEO_PORT}/api/health` for up to 5 seconds (1s interval).
7. On success: print PID, listen URL, log path; exit 0.
8. On failure: print last 20 lines of the log file; exit non-zero (leave PID file for debugging).

Note: health check uses loopback regardless of `AEO_HOST`, since the server may bind `0.0.0.0`.

### `stop`

1. Load `.env` (for port/log context in messages only).
2. Read PID from `runtime/controller.pid`.
3. If PID file missing or stale, fall back to `pgrep -f "agent_eval_orchestrator.controller.server"` (single match only; if multiple, error and list PIDs).
4. Send `SIGTERM`, wait up to 15 seconds.
5. If still alive, send `SIGKILL`.
6. Remove `runtime/controller.pid`.
7. Exit 0 if stopped (or was already stopped with no live process).

### `restart`

Run `stop`, `sleep 1`, then `start`. Propagate `start` exit code.

### `status`

1. Load `.env`.
2. Report:
   - State: `running` or `stopped`
   - PID (if known)
   - Listen URL: `http://${AEO_HOST}:${AEO_PORT}`
   - Health: result of `curl -sf http://127.0.0.1:${AEO_PORT}/api/health`
   - Log path: `runtime/logs/controller-${AEO_PORT}.log`
3. Exit 0 if running and health OK; exit 1 otherwise.

## Error Handling

| Condition | Behavior |
|-----------|----------|
| `uv` not in `PATH` | Exit with install hint |
| Missing `.env` | Exit; suggest `cp .env.example .env` |
| Missing required vars | Exit; name the variable |
| Start health check fails | Print log tail; non-zero exit |
| Stale PID file | Treat as not running; remove on `stop` / report stopped on `status` |
| Multiple controller PIDs on fallback | Exit with error; do not kill ambiguously |

## `.env.example` Content

Committed template (placeholders only):

```bash
AEO_HOST=0.0.0.0
AEO_PORT=7380
AEO_SHARED_ROOT=runtime
AEO_AUTH_TOKEN=change-me
AEO_SSH_CONFIG=~/.ssh/config
# AEO_GITHUB_TOKEN=ghp_...
```

Use a relative `AEO_SHARED_ROOT=runtime` in the example so new clones work out of the box after `uv sync`.

## Testing

Manual verification (no pytest):

```bash
cp .env.example .env   # edit secrets
./scripts/aeo-controller.sh start
./scripts/aeo-controller.sh status
curl -sf "http://127.0.0.1:${AEO_PORT:-7380}/api/health"
./scripts/aeo-controller.sh restart
./scripts/aeo-controller.sh stop
./scripts/aeo-controller.sh status   # expect exit 1
```

Also verify: second `start` while running fails cleanly; `stop` when already stopped exits 0.

## Implementation Notes

- Reuse logging/redaction patterns from `scripts/bootstrap-huawei-worker.sh` (`log()`, `die()` helpers).
- Do not log `AEO_AUTH_TOKEN` or `AEO_GITHUB_TOKEN`.
- Script shebang: `#!/usr/bin/env bash`, `set -euo pipefail`.
- `-h` / `--help` prints usage and subcommands.

## Out of Scope Follow-ups

- README section replacing manual `nohup` / `setsid` examples with the new script.
- Worker lifecycle script (deferred per operator decision).
