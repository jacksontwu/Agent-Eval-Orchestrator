#!/usr/bin/env bash
# Start the Agent Eval Orchestrator controller (FastAPI + orchestration threads).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKEND_DIR="${REPO_ROOT}/backend"

# Load project-root .env so the shell guard and uvicorn see the same config
# regardless of how the script is invoked.
if [ -f "${REPO_ROOT}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "${REPO_ROOT}/.env"
  set +a
fi

# Logs and pidfile live under AEO_SHARED_ROOT/controller so everything follows
# the same data root (relative roots resolve against the repo root).
SHARED_ROOT="${AEO_SHARED_ROOT:-runtime}"
case "${SHARED_ROOT}" in /*) ;; *) SHARED_ROOT="${REPO_ROOT}/${SHARED_ROOT}" ;; esac
LOG_DIR="${SHARED_ROOT}/controller/logs"
PID_FILE="${SHARED_ROOT}/controller/controller.pid"

# Refuse to start wide-open on a network-reachable host.
if [ -z "${AEO_TOKEN:-}" ] && [ "${AEO_ALLOW_NO_AUTH:-}" != "1" ]; then
  echo "refusing to start: set AEO_TOKEN (or AEO_ALLOW_NO_AUTH=1 for local dev)" >&2
  exit 1
fi

mkdir -p "${LOG_DIR}"
cd "${BACKEND_DIR}"

echo "[controller] applying migrations ..."
uv run alembic upgrade head

echo "[controller] starting uvicorn ..."
setsid uv run uvicorn app.main:app \
  --host "${AEO_HOST:-0.0.0.0}" \
  --port "${AEO_PORT:-8790}" \
  >> "${LOG_DIR}/controller.log" 2>&1 &

echo $! > "${PID_FILE}"
echo "[controller] started (pid $(cat "${PID_FILE}")); logs: ${LOG_DIR}/controller.log"
