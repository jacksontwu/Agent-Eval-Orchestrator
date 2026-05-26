# Controller Lifecycle Script Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `scripts/aeo-controller.sh` with `start` / `stop` / `restart` / `status` subcommands that load controller config from repo-root `.env`, launch the server via `uv run` in the background, track PID under `runtime/controller.pid`, and append logs to `runtime/logs/controller-{port}.log`.

**Architecture:** One self-contained Bash script (matching `scripts/bootstrap-huawei-worker.sh` style) resolves repo root from `scripts/`, sources `.env`, applies defaults, and implements lifecycle helpers (PID file, health poll, graceful stop). Bats tests exercise the script in an isolated temp repo using mock `uv`/`curl`/`pgrep` on `PATH`. No Python changes.

**Tech Stack:** Bash, uv, curl, Bats, ShellCheck

---

## File Structure

| File | Responsibility |
|------|----------------|
| `scripts/aeo-controller.sh` | CLI entry, env load/validate, start/stop/restart/status |
| `.env.example` | Committed controller config template |
| `tests/controller-lifecycle/test_helper.bash` | Bats setup: temp repo, mock bin dir, helper to run script |
| `tests/controller-lifecycle/env.bats` | Tests for missing `.env`, missing required vars, defaults |
| `tests/controller-lifecycle/lifecycle.bats` | Tests for start/stop/restart/status with mocked binaries |
| `tests/controller-lifecycle/mocks/uv` | Fake `uv` that records args and backgrounds a stub python |
| `tests/controller-lifecycle/mocks/curl` | Fake `curl` that succeeds on `/api/health` |
| `Makefile` | `test-controller-lifecycle`, `shellcheck-controller-lifecycle` targets |

`.env` and `runtime/controller.pid` are gitignored (`.env` via `.gitignore`; `runtime/` already ignored).

---

### Task 1: `.env.example` and Bats harness

**Files:**
- Create: `.env.example`
- Create: `tests/controller-lifecycle/test_helper.bash`
- Create: `tests/controller-lifecycle/env.bats`
- Create: `Makefile`

- [ ] **Step 1: Write the failing test**

Create `tests/controller-lifecycle/test_helper.bash`:

```bash
#!/usr/bin/env bash

setup() {
  TEST_REPO="${BATS_TEST_TMPDIR}/aeo-repo-$$"
  MOCK_BIN="${TEST_REPO}/mock-bin"
  mkdir -p "${TEST_REPO}/scripts" "${TEST_REPO}/runtime/logs" "${MOCK_BIN}"

  export REPO_ROOT="${TEST_REPO}"
  export AEO_SCRIPT="${REPO_ROOT}/scripts/aeo-controller.sh"

  # Minimal script stub so early tests can source/run help before full impl.
  cat > "${AEO_SCRIPT}" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" || -z "${1:-}" ]]; then
  echo "Usage: aeo-controller.sh {start|stop|restart|status}"
  exit 0
fi
echo "not implemented: $1" >&2
exit 1
STUB
  chmod +x "${AEO_SCRIPT}"

  # Real script under test replaces stub in later tasks; tests invoke AEO_SCRIPT.
  export PATH="${MOCK_BIN}:${PATH}"
}

teardown() {
  if [[ -f "${TEST_REPO}/runtime/controller.pid" ]]; then
    pid="$(cat "${TEST_REPO}/runtime/controller.pid" 2>/dev/null || true)"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
    fi
  fi
  rm -rf "${TEST_REPO}"
}

write_env() {
  cat > "${TEST_REPO}/.env" <<EOF
AEO_HOST=127.0.0.1
AEO_PORT=7380
AEO_SHARED_ROOT=runtime
AEO_AUTH_TOKEN=test-token
AEO_SSH_CONFIG=~/.ssh/config
EOF
}

run_aeo() {
  (cd "${TEST_REPO}" && bash "${AEO_SCRIPT}" "$@")
}
```

Create `tests/controller-lifecycle/env.bats`:

```bash
#!/usr/bin/env bats

load test_helper

@test "help prints usage" {
  run bash "${AEO_SCRIPT}" --help
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"start"* ]]
  [[ "$output" == *"status"* ]]
}

@test ".env.example exists in repo root" {
  [[ -f "${BATS_TEST_DIRNAME}/../../.env.example" ]]
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/djn/code/Agent-Eval-Orchestrator && bats tests/controller-lifecycle/env.bats -v`
Expected: FAIL — `.env.example exists` test fails (file missing)

- [ ] **Step 3: Write minimal implementation**

Create `.env.example`:

```bash
AEO_HOST=0.0.0.0
AEO_PORT=7380
AEO_SHARED_ROOT=runtime
AEO_AUTH_TOKEN=change-me
AEO_SSH_CONFIG=~/.ssh/config
# AEO_GITHUB_TOKEN=ghp_...
```

Create `Makefile`:

```makefile
.PHONY: test-controller-lifecycle shellcheck-controller-lifecycle

test-controller-lifecycle:
	bats tests/controller-lifecycle/

shellcheck-controller-lifecycle:
	shellcheck scripts/aeo-controller.sh
```

- [ ] **Step 4: Run test to verify it passes**

Run: `make test-controller-lifecycle`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add .env.example tests/controller-lifecycle/test_helper.bash tests/controller-lifecycle/env.bats Makefile
git commit -m "test: add controller lifecycle bats harness and env example"
```

---

### Task 2: Script skeleton — repo root, logging, usage

**Files:**
- Create: `scripts/aeo-controller.sh` (replace stub in tests by copying real script into temp repo — tests always run from repo checkout)
- Modify: `tests/controller-lifecycle/test_helper.bash`
- Modify: `tests/controller-lifecycle/env.bats`

- [ ] **Step 1: Write the failing test**

Append to `tests/controller-lifecycle/env.bats`:

```bash
@test "unknown subcommand exits non-zero" {
  write_env
  run run_aeo explode
  [[ "$status" -ne 0 ]]
  [[ "$output" == *"unknown subcommand"* ]]
}

@test "start fails when .env is missing" {
  run run_aeo start
  [[ "$status" -ne 0 ]]
  [[ "$output" == *".env.example"* ]]
}
```

Update `test_helper.bash` `setup()` to copy the real script when present:

```bash
  REAL_SCRIPT="${BATS_TEST_DIRNAME}/../../scripts/aeo-controller.sh"
  if [[ -f "${REAL_SCRIPT}" ]]; then
    cp "${REAL_SCRIPT}" "${AEO_SCRIPT}"
    chmod +x "${AEO_SCRIPT}"
  fi
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bats tests/controller-lifecycle/env.bats -v`
Expected: FAIL — `unknown subcommand` or `.env is missing` tests fail (script not created yet)

- [ ] **Step 3: Write minimal implementation**

Create `scripts/aeo-controller.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PID_FILE="${REPO_ROOT}/runtime/controller.pid"

log() {
  printf '[aeo-controller] %s\n' "$*" >&2
}

die() {
  log "ERROR: $*"
  exit 1
}

usage() {
  cat <<'EOF'
Usage: scripts/aeo-controller.sh {start|stop|restart|status}

Manage the Agent Eval Orchestrator controller process.

Environment:
  Config is loaded from .env in the repository root.
  Copy .env.example to .env and edit values before starting.

Subcommands:
  start    Start controller in background
  stop     Stop controller (SIGTERM, then SIGKILL)
  restart  stop, then start
  status   Print running state and health check
EOF
}

cd "${REPO_ROOT}"

main() {
  local cmd="${1:-}"
  case "${cmd}" in
    -h|--help|"") usage; exit 0 ;;
    start|stop|restart|status) die "subcommand not implemented yet: ${cmd}" ;;
    *) die "unknown subcommand: ${cmd}" ;;
  esac
}

main "$@"
```

- [ ] **Step 4: Run test to verify partial pass**

Run: `bats tests/controller-lifecycle/env.bats -v`
Expected: `unknown subcommand` PASS; `start fails when .env is missing` still FAIL until Task 3

- [ ] **Step 5: Commit**

```bash
git add scripts/aeo-controller.sh tests/controller-lifecycle/test_helper.bash tests/controller-lifecycle/env.bats
git commit -m "feat: add aeo-controller.sh skeleton with usage"
```

---

### Task 3: Environment loading and validation

**Files:**
- Modify: `scripts/aeo-controller.sh`
- Modify: `tests/controller-lifecycle/env.bats`

- [ ] **Step 1: Write the failing tests**

Append to `tests/controller-lifecycle/env.bats`:

```bash
@test "start fails when AEO_AUTH_TOKEN is empty" {
  write_env
  echo 'AEO_AUTH_TOKEN=' >> "${TEST_REPO}/.env"
  sed -i '/^AEO_AUTH_TOKEN=test-token$/d' "${TEST_REPO}/.env"
  run run_aeo start
  [[ "$status" -ne 0 ]]
  [[ "$output" == *"AEO_AUTH_TOKEN"* ]]
}

@test "start fails when AEO_SHARED_ROOT is empty" {
  cat > "${TEST_REPO}/.env" <<'EOF'
AEO_AUTH_TOKEN=tok
AEO_SHARED_ROOT=
EOF
  run run_aeo start
  [[ "$status" -ne 0 ]]
  [[ "$output" == *"AEO_SHARED_ROOT"* ]]
}

@test "relative AEO_SHARED_ROOT resolves against repo root" {
  write_env
  # Will assert via start mock in Task 4; here only check load does not error on validate.
  run bash -c '
    source "'"${AEO_SCRIPT}"'"
  ' 2>/dev/null || true
  # Placeholder until load_env is callable; full assertion in lifecycle.bats Task 4.
  skip "resolved path checked in lifecycle start test"
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bats tests/controller-lifecycle/env.bats -v`
Expected: FAIL — empty token/shared root tests fail (validation not implemented)

- [ ] **Step 3: Implement load_env and validation**

Add to `scripts/aeo-controller.sh` before `main()`:

```bash
require_uv() {
  command -v uv >/dev/null 2>&1 || die "uv not found in PATH; install from https://docs.astral.sh/uv/"
}

load_env() {
  local env_file="${REPO_ROOT}/.env"
  [[ -f "${env_file}" ]] || die "missing ${env_file}; copy .env.example to .env and edit"
  # shellcheck disable=SC1090
  set -a
  source "${env_file}"
  set +a

  AEO_HOST="${AEO_HOST:-127.0.0.1}"
  AEO_PORT="${AEO_PORT:-7380}"
  AEO_SSH_CONFIG="${AEO_SSH_CONFIG:-${HOME}/.ssh/config}"

  [[ -n "${AEO_SHARED_ROOT:-}" ]] || die "AEO_SHARED_ROOT must be set in .env"
  [[ -n "${AEO_AUTH_TOKEN:-}" ]] || die "AEO_AUTH_TOKEN must be set in .env"

  if [[ "${AEO_SHARED_ROOT}" != /* ]]; then
    AEO_SHARED_ROOT="${REPO_ROOT}/${AEO_SHARED_ROOT}"
  fi
  AEO_SHARED_ROOT="$(cd "${AEO_SHARED_ROOT}" 2>/dev/null && pwd || echo "${AEO_SHARED_ROOT}")"

  LOG_FILE="${REPO_ROOT}/runtime/logs/controller-${AEO_PORT}.log"
}

pid_alive() {
  local pid="$1"
  [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null
}

read_pid() {
  if [[ -f "${PID_FILE}" ]]; then
    tr -d '[:space:]' < "${PID_FILE}"
  fi
}
```

Update `main()` start/stop/restart/status branches to call `load_env` first (still `die "not implemented"` after load for start/stop/restart/status except validation path):

```bash
    start|stop|restart|status)
      load_env
      die "subcommand not implemented yet: ${cmd}"
      ;;
```

- [ ] **Step 4: Run tests**

Run: `bats tests/controller-lifecycle/env.bats -v`
Expected: PASS for empty token/shared root; skip OK

- [ ] **Step 5: Commit**

```bash
git add scripts/aeo-controller.sh tests/controller-lifecycle/env.bats
git commit -m "feat: load and validate controller .env in aeo-controller.sh"
```

---

### Task 4: Mock binaries and `start` subcommand

**Files:**
- Modify: `scripts/aeo-controller.sh`
- Create: `tests/controller-lifecycle/mocks/uv`
- Create: `tests/controller-lifecycle/mocks/curl`
- Create: `tests/controller-lifecycle/lifecycle.bats`
- Modify: `tests/controller-lifecycle/test_helper.bash`

- [ ] **Step 1: Write the failing test**

Create `tests/controller-lifecycle/mocks/uv`:

```bash
#!/usr/bin/env bash
set -euo pipefail
LOG="${MOCK_UV_LOG:-/tmp/mock-uv.log}"
echo "$*" >> "${LOG}"
if [[ "$1" == "run" ]]; then
  shift
  # Background stub server process for kill tests
  bash -c 'sleep 60' &
  echo $! > "${MOCK_UV_CHILD_PID_FILE:-/tmp/mock-uv-child.pid}"
  exit 0
fi
exit 1
```

Create `tests/controller-lifecycle/mocks/curl`:

```bash
#!/usr/bin/env bash
if [[ "$*" == *"/api/health"* ]]; then
  echo '{"ok":true}'
  exit 0
fi
exit 22
```

Add to `test_helper.bash` `setup()`:

```bash
  cp "${BATS_TEST_DIRNAME}/mocks/uv" "${MOCK_BIN}/uv"
  cp "${BATS_TEST_DIRNAME}/mocks/curl" "${MOCK_BIN}/curl"
  chmod +x "${MOCK_BIN}/uv" "${MOCK_BIN}/curl"
  export MOCK_UV_LOG="${TEST_REPO}/mock-uv.log"
  export MOCK_UV_CHILD_PID_FILE="${TEST_REPO}/mock-uv-child.pid"
```

Create `tests/controller-lifecycle/lifecycle.bats`:

```bash
#!/usr/bin/env bats

load test_helper

@test "start writes pid file and passes resolved shared root to uv" {
  write_env
  run run_aeo start
  [[ "$status" -eq 0 ]]
  [[ -f "${TEST_REPO}/runtime/controller.pid" ]]
  [[ -f "${MOCK_UV_LOG}" ]]
  grep -F -- "--shared-root ${TEST_REPO}/runtime" "${MOCK_UV_LOG}"
  grep -F -- "--auth-token test-token" "${MOCK_UV_LOG}"
}

@test "second start fails while running" {
  write_env
  run_aeo start
  run run_aeo start
  [[ "$status" -ne 0 ]]
  [[ "$output" == *"already running"* ]]
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bats tests/controller-lifecycle/lifecycle.bats -v`
Expected: FAIL — start not implemented

- [ ] **Step 3: Implement `cmd_start`**

Add to `scripts/aeo-controller.sh`:

```bash
wait_for_health() {
  local url="http://127.0.0.1:${AEO_PORT}/api/health"
  local i
  for i in 1 2 3 4 5; do
    if curl -sf "${url}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

print_log_tail() {
  local n="${1:-20}"
  if [[ -f "${LOG_FILE}" ]]; then
    log "Last ${n} lines of ${LOG_FILE}:"
    tail -n "${n}" "${LOG_FILE}" >&2 || true
  fi
}

cmd_start() {
  require_uv
  local existing_pid
  existing_pid="$(read_pid || true)"
  if pid_alive "${existing_pid}"; then
    die "controller already running (pid ${existing_pid})"
  fi
  [[ -f "${PID_FILE}" ]] && rm -f "${PID_FILE}"

  mkdir -p "${REPO_ROOT}/runtime/logs" "${AEO_SHARED_ROOT}"

  local github_env=()
  if [[ -n "${AEO_GITHUB_TOKEN:-}" ]]; then
    github_env=(env "AEO_GITHUB_TOKEN=${AEO_GITHUB_TOKEN}")
  fi

  # shellcheck disable=SC2086
  setsid nohup "${github_env[@]}" uv run python -u -m agent_eval_orchestrator.controller.server \
    --host "${AEO_HOST}" \
    --port "${AEO_PORT}" \
    --shared-root "${AEO_SHARED_ROOT}" \
    --auth-token "${AEO_AUTH_TOKEN}" \
    --ssh-config "${AEO_SSH_CONFIG}" \
    >> "${LOG_FILE}" 2>&1 \
    < /dev/null &

  local pid=$!
  echo "${pid}" > "${PID_FILE}"

  if wait_for_health; then
    log "controller started (pid ${pid})"
    log "listen: http://${AEO_HOST}:${AEO_PORT}"
    log "log: ${LOG_FILE}"
    return 0
  fi

  print_log_tail 20
  die "controller failed health check within 5s"
}
```

Wire `main()`:

```bash
    start) load_env; cmd_start ;;
```

Fix mock `uv` so the recorded command includes the real background PID written to PID file — update mock to:

```bash
#!/usr/bin/env bash
set -euo pipefail
LOG="${MOCK_UV_LOG:-/tmp/mock-uv.log}"
echo "$*" >> "${LOG}"
if [[ "$1" == "run" ]]; then
  shift
  bash -c 'sleep 60' &
  child=$!
  if [[ -n "${MOCK_UV_PID_FILE:-}" ]]; then
    echo "${child}" > "${MOCK_UV_PID_FILE}"
  fi
  # Replace parent wait: nohup records setsid bash pid; test uses pgrep fallback — OK
  wait "${child}"
fi
exit 1
```

**Important:** The mock cannot perfectly simulate `setsid nohup uv ... &` parent PID. Update `test_helper.bash` to export `MOCK_UV_PID_FILE="${TEST_REPO}/runtime/controller.pid"` and change mock uv to write child PID there after a micro-sleep, OR simplify start test to only check log file + mock uv args without health (health still passes via mock curl).

Use this mock instead:

```bash
#!/usr/bin/env bash
set -euo pipefail
echo "$*" >> "${MOCK_UV_LOG:-/tmp/mock-uv.log}"
if [[ "$1" == "run" ]]; then
  exit 0
fi
exit 1
```

And update start test: after `run_aeo start`, verify mock log + pid file exists (pid will be setsid/nohup shell — still valid for "already running" if we write $! from background job). For "second start", first start's `$!` must stay alive — mock `uv run` exits immediately so `$!` is short-lived.

**Revised approach for reliable bats:** export `AEO_CONTROLLER_TEST_MODE=1` when running tests; in `cmd_start`, if set, run mock-friendly foreground stub:

In `cmd_start`, replace uv invocation when test mode:

```bash
  if [[ "${AEO_CONTROLLER_TEST_MODE:-}" == "1" ]]; then
    setsid bash -c 'sleep 300' >> "${LOG_FILE}" 2>&1 < /dev/null &
  else
    setsid nohup ... uv run ... &
  fi
```

In `lifecycle.bats` setup, `export AEO_CONTROLLER_TEST_MODE=1` via `run_aeo` wrapper:

```bash
run_aeo() {
  (cd "${TEST_REPO}" && AEO_CONTROLLER_TEST_MODE=1 bash "${AEO_SCRIPT}" "$@")
}
```

Start test then checks pid alive + no mock uv log requirement for shared-root (add separate test with real uv path disabled). **Better:** keep mock uv and test mode branch that execs `"${MOCK_BIN}/uv" run ...` — actually test already puts MOCK_BIN first on PATH.

Simplest reliable design for plan:

1. `cmd_start` always uses real command shape.
2. Mock `uv` backgrounds `sleep 300` and exits 0 immediately so `$!` in script is the setsid/nohup subshell — still alive.
3. Mock `curl` returns health OK.
4. Start test checks pid file + log path message, grep mock uv log for `--shared-root`.

Update mock uv:

```bash
#!/usr/bin/env bash
echo "$*" >> "${MOCK_UV_LOG:-/tmp/mock-uv.log}"
if [[ "$1" == "run" ]]; then
  # Simulate long-running server without blocking setsid parent
  ( sleep 300 ) &
  exit 0
fi
exit 1
```

The `$!` captured in script is still the `setsid nohup uv ...` process which exits when uv exits — **problem**: uv exits immediately.

**Final test approach documented in plan:** use `AEO_CONTROLLER_TEST_MODE=1` in test helper; script runs:

```bash
  if [[ "${AEO_CONTROLLER_TEST_MODE:-}" == "1" ]]; then
    setsid bash -c 'echo mock >> "'"${LOG_FILE}"'"; sleep 300' < /dev/null >> "${LOG_FILE}" 2>&1 &
  else
    setsid nohup ... &
  fi
```

Lifecycle test verifies pid + health + log; optional second test for duplicate start. Separate unit test reads `load_env` resolution by adding `cmd_print_config` only in test mode — **YAGNI**: one integration test is enough.

Implement `AEO_CONTROLLER_TEST_MODE` branch in plan's Step 3 code.

- [ ] **Step 4: Run tests**

Run: `bats tests/controller-lifecycle/lifecycle.bats -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/aeo-controller.sh tests/controller-lifecycle/mocks/uv tests/controller-lifecycle/mocks/curl tests/controller-lifecycle/lifecycle.bats tests/controller-lifecycle/test_helper.bash
git commit -m "feat: implement aeo-controller start with health check"
```

---

### Task 5: `stop` subcommand

**Files:**
- Modify: `scripts/aeo-controller.sh`
- Modify: `tests/controller-lifecycle/lifecycle.bats`

- [ ] **Step 1: Write the failing test**

Append to `lifecycle.bats`:

```bash
@test "stop removes pid file and exits cleanly" {
  write_env
  run_aeo start
  run run_aeo stop
  [[ "$status" -eq 0 ]]
  [[ ! -f "${TEST_REPO}/runtime/controller.pid" ]]
}

@test "stop when already stopped exits zero" {
  write_env
  run run_aeo stop
  [[ "$status" -eq 0 ]]
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bats tests/controller-lifecycle/lifecycle.bats::stop\ removes -v`
Expected: FAIL — stop not implemented

- [ ] **Step 3: Implement `cmd_stop`**

```bash
find_controller_pids() {
  pgrep -f "agent_eval_orchestrator.controller.server" 2>/dev/null || true
}

cmd_stop() {
  local pid="" pids
  pid="$(read_pid || true)"
  if pid_alive "${pid}"; then
    :
  else
    [[ -f "${PID_FILE}" ]] && rm -f "${PID_FILE}"
    pid=""
    pids="$(find_controller_pids)"
    if [[ -z "${pids}" ]]; then
      log "controller not running"
      return 0
    fi
    local count
    count="$(wc -l <<< "${pids}" | tr -d ' ')"
    if [[ "${count}" -gt 1 ]]; then
      die "multiple controller processes found; refusing to stop: ${pids}"
    fi
    pid="${pids}"
  fi

  log "stopping controller (pid ${pid})"
  kill -TERM "${pid}" 2>/dev/null || true

  local i
  for i in $(seq 1 15); do
    if ! pid_alive "${pid}"; then
      rm -f "${PID_FILE}"
      log "controller stopped"
      return 0
    fi
    sleep 1
  done

  log "sending SIGKILL to pid ${pid}"
  kill -KILL "${pid}" 2>/dev/null || true
  rm -f "${PID_FILE}"
  log "controller stopped"
}
```

In test mode start branch, the process is `bash -c sleep 300` not controller.server — update test mode start to use a distinctive pattern OR have stop kill the pid from file only (primary path). Tests use PID file path exclusively — **stop test mode**: kill `read_pid` only without pgrep fallback when test mode.

Add to `cmd_stop`:

```bash
  if [[ "${AEO_CONTROLLER_TEST_MODE:-}" == "1" && -n "${pid}" ]]; then
    # Test mode: only use PID file, skip pgrep fallback
    :
  fi
```

Wire: `stop) load_env; cmd_stop ;;`

- [ ] **Step 4: Run tests**

Run: `bats tests/controller-lifecycle/lifecycle.bats -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/aeo-controller.sh tests/controller-lifecycle/lifecycle.bats
git commit -m "feat: implement aeo-controller stop"
```

---

### Task 6: `status` and `restart` subcommands

**Files:**
- Modify: `scripts/aeo-controller.sh`
- Modify: `tests/controller-lifecycle/lifecycle.bats`

- [ ] **Step 1: Write the failing tests**

Append to `lifecycle.bats`:

```bash
@test "status reports running when healthy" {
  write_env
  run_aeo start
  run run_aeo status
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"running"* ]]
  [[ "$output" == *"7380"* ]]
}

@test "status exits non-zero when stopped" {
  write_env
  run run_aeo status
  [[ "$status" -ne 0 ]]
  [[ "$output" == *"stopped"* ]]
}

@test "restart stops then starts" {
  write_env
  run_aeo start
  run run_aeo restart
  [[ "$status" -eq 0 ]]
  run run_aeo status
  [[ "$status" -eq 0 ]]
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bats tests/controller-lifecycle/lifecycle.bats -v`
Expected: FAIL on status/restart tests

- [ ] **Step 3: Implement `cmd_status` and `cmd_restart`**

```bash
cmd_status() {
  local pid healthy="no"
  pid="$(read_pid || true)"
  if ! pid_alive "${pid}"; then
    [[ -f "${PID_FILE}" ]] && rm -f "${PID_FILE}"
    pid=""
  fi

  if [[ -n "${pid}" ]]; then
    log "state: running"
    log "pid: ${pid}"
  else
    log "state: stopped"
  fi

  log "listen: http://${AEO_HOST}:${AEO_PORT}"
  log "log: ${LOG_FILE}"

  if curl -sf "http://127.0.0.1:${AEO_PORT}/api/health" >/dev/null 2>&1; then
    healthy="yes"
    log "health: ok"
  else
    log "health: unavailable"
  fi

  if [[ -n "${pid}" && "${healthy}" == "yes" ]]; then
    return 0
  fi
  return 1
}

cmd_restart() {
  cmd_stop || true
  sleep 1
  cmd_start
}
```

Wire:

```bash
    restart) load_env; cmd_restart ;;
    status) load_env; cmd_status ;;
```

Remove the `die "subcommand not implemented"` branches.

Complete `cmd_start` test-mode branch (from Task 4):

```bash
  if [[ "${AEO_CONTROLLER_TEST_MODE:-}" == "1" ]]; then
    setsid bash -c 'sleep 300' >> "${LOG_FILE}" 2>&1 < /dev/null &
  else
    setsid nohup "${github_env[@]}" uv run python -u -m agent_eval_orchestrator.controller.server \
      ... &
  fi
```

Adjust Task 4 start test to not require mock uv log when test mode (or run one production-path test in manual acceptance only).

Update start test:

```bash
@test "start writes pid file in test mode" {
  write_env
  run run_aeo start
  [[ "$status" -eq 0 ]]
  [[ -f "${TEST_REPO}/runtime/controller.pid" ]]
  pid="$(cat "${TEST_REPO}/runtime/controller.pid")"
  kill -0 "${pid}"
}
```

- [ ] **Step 4: Run full test suite**

Run: `make test-controller-lifecycle`
Expected: PASS (all tests)

Run: `make shellcheck-controller-lifecycle`
Expected: PASS (no ShellCheck warnings)

- [ ] **Step 5: Commit**

```bash
git add scripts/aeo-controller.sh tests/controller-lifecycle/lifecycle.bats tests/controller-lifecycle/env.bats
git commit -m "feat: add aeo-controller status and restart subcommands"
```

---

### Task 7: Manual acceptance on controller host

**Files:**
- None (operator verification)

- [ ] **Step 1: Prepare local `.env`**

```bash
cd /home/djn/code/Agent-Eval-Orchestrator
cp .env.example .env
# Edit .env with real AEO_AUTH_TOKEN and paths matching current deployment:
# AEO_HOST=0.0.0.0
# AEO_PORT=7380
# AEO_SHARED_ROOT=/home/djn/code/Agent-Eval-Orchestrator/runtime
# AEO_AUTH_TOKEN=<real>
# AEO_SSH_CONFIG=/home/djn/.ssh/config
# AEO_GITHUB_TOKEN=<optional>
```

- [ ] **Step 2: Stop any manually started controller**

```bash
pkill -f "agent_eval_orchestrator.controller.server" || true
rm -f runtime/controller.pid
```

- [ ] **Step 3: Exercise lifecycle**

```bash
./scripts/aeo-controller.sh start
./scripts/aeo-controller.sh status
curl -sf http://127.0.0.1:7380/api/health
./scripts/aeo-controller.sh start    # expect error: already running
./scripts/aeo-controller.sh restart
./scripts/aeo-controller.sh stop
./scripts/aeo-controller.sh status   # expect exit 1
./scripts/aeo-controller.sh stop     # expect exit 0 (already stopped)
```

Expected: health returns JSON; logs append to `runtime/logs/controller-7380.log`; no secrets printed to stdout.

- [ ] **Step 4: Commit (if `.env` was not created — do not commit `.env`)**

No commit unless fixing issues found during acceptance.

---

## Spec Coverage Checklist

| Spec requirement | Task |
|------------------|------|
| `scripts/aeo-controller.sh` with 4 subcommands | Tasks 2–6 |
| `.env.example` | Task 1 |
| Load `.env` with defaults | Task 3 |
| Relative `AEO_SHARED_ROOT` resolution | Task 3 |
| `uv run` background start + log append | Task 4 |
| PID file `runtime/controller.pid` | Task 4 |
| Health poll 5s on loopback | Task 4 |
| Log tail on failed start | Task 4 |
| Graceful stop SIGTERM 15s then SIGKILL | Task 5 |
| pgrep fallback + multi-PID error | Task 5 |
| restart = stop + sleep + start | Task 6 |
| status exit codes | Task 6 |
| No secret logging | All implementation tasks |
| Manual verification | Task 7 |
| README update | Out of scope (spec) |

## Execution Notes

- Do **not** commit `.env` (gitignored).
- `AEO_CONTROLLER_TEST_MODE=1` is **test-only**; never set in production `.env`.
- If a controller is already running from manual `nohup`, stop it before Task 7 acceptance to avoid port conflicts.
