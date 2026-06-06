#!/usr/bin/env bash
# Show controller status: process (from pidfile) + HTTP health probe.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PID_FILE="${REPO_ROOT}/runtime/controller.pid"

# Load .env so we know which host/port to probe.
if [ -f "${REPO_ROOT}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "${REPO_ROOT}/.env"
  set +a
fi

HOST="${AEO_HOST:-0.0.0.0}"
PORT="${AEO_PORT:-8790}"
PROBE_HOST="127.0.0.1"

# 1. Process state.
if [ -f "${PID_FILE}" ]; then
  PID="$(cat "${PID_FILE}")"
  if kill -0 "${PID}" 2>/dev/null; then
    echo "[controller] process: RUNNING (pid ${PID})"
  else
    echo "[controller] process: NOT RUNNING (stale pidfile ${PID_FILE})"
  fi
else
  echo "[controller] process: NOT RUNNING (no pidfile)"
fi

# 2. HTTP health probe.
HEALTH_URL="http://${PROBE_HOST}:${PORT}/api/health"
if BODY="$(curl -fsS --max-time 3 "${HEALTH_URL}" 2>/dev/null)"; then
  echo "[controller] health: OK  ${HEALTH_URL} -> ${BODY}"
else
  echo "[controller] health: UNREACHABLE  ${HEALTH_URL}"
fi

echo "[controller] listen: ${HOST}:${PORT}"
