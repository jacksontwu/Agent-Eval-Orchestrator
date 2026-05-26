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
  if [[ "${AEO_CONTROLLER_TEST_MODE:-}" != "1" ]]; then
    require_uv
  fi

  local existing_pid
  existing_pid="$(read_pid || true)"
  if pid_alive "${existing_pid}"; then
    die "controller already running (pid ${existing_pid})"
  fi
  [[ -f "${PID_FILE}" ]] && rm -f "${PID_FILE}"

  mkdir -p "${REPO_ROOT}/runtime/logs" "${AEO_SHARED_ROOT}"

  if [[ "${AEO_CONTROLLER_TEST_MODE:-}" == "1" ]]; then
    setsid bash -c 'sleep 300' >> "${LOG_FILE}" 2>&1 < /dev/null &
  else
    local -a cmd=(uv run python -u -m agent_eval_orchestrator.controller.server
      --host "${AEO_HOST}"
      --port "${AEO_PORT}"
      --shared-root "${AEO_SHARED_ROOT}"
      --auth-token "${AEO_AUTH_TOKEN}"
      --ssh-config "${AEO_SSH_CONFIG}")
    if [[ -n "${AEO_GITHUB_TOKEN:-}" ]]; then
      AEO_GITHUB_TOKEN="${AEO_GITHUB_TOKEN}" setsid nohup "${cmd[@]}" >> "${LOG_FILE}" 2>&1 < /dev/null &
    else
      setsid nohup "${cmd[@]}" >> "${LOG_FILE}" 2>&1 < /dev/null &
    fi
  fi

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
    if [[ "${AEO_CONTROLLER_TEST_MODE:-}" == "1" ]]; then
      log "controller not running"
      return 0
    fi
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

cd "${REPO_ROOT}"

main() {
  local cmd="${1:-}"
  case "${cmd}" in
    -h|--help|"") usage; exit 0 ;;
    start) load_env; cmd_start ;;
    stop) load_env; cmd_stop ;;
    restart) load_env; cmd_restart ;;
    status) load_env; cmd_status ;;
    *) die "unknown subcommand: ${cmd}" ;;
  esac
}

main "$@"
