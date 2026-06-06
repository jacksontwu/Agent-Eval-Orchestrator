#!/usr/bin/env bash
# Stop the controller started by start-controller.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [ -f "${REPO_ROOT}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "${REPO_ROOT}/.env"
  set +a
fi

SHARED_ROOT="${AEO_SHARED_ROOT:-runtime}"
case "${SHARED_ROOT}" in /*) ;; *) SHARED_ROOT="${REPO_ROOT}/${SHARED_ROOT}" ;; esac
PID_FILE="${SHARED_ROOT}/controller/controller.pid"

if [ ! -f "${PID_FILE}" ]; then
  echo "[controller] no pidfile at ${PID_FILE}; nothing to stop." >&2
  exit 0
fi

PID="$(cat "${PID_FILE}")"
if kill -0 "${PID}" 2>/dev/null; then
  kill "${PID}"
  echo "[controller] stopped (pid ${PID})."
else
  echo "[controller] process ${PID} not running."
fi
rm -f "${PID_FILE}"
