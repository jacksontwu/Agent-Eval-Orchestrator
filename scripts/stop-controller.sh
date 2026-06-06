#!/usr/bin/env bash
# Stop the controller started by start-controller.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PID_FILE="${REPO_ROOT}/runtime/controller.pid"

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
