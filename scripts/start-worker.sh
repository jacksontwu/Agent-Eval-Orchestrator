#!/usr/bin/env bash
# Convenience wrapper to run the worker daemon against a controller.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKEND_DIR="${REPO_ROOT}/backend"

if [ -f "${REPO_ROOT}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "${REPO_ROOT}/.env"
  set +a
fi

: "${AEO_CONTROLLER_URL:?set AEO_CONTROLLER_URL (e.g. http://controller:8790)}"
: "${AEO_WORKER_ID:?set AEO_WORKER_ID}"
: "${AEO_BOT_USERNAME:?set AEO_BOT_USERNAME}"
: "${AEO_BOT_PASSWORD:?set AEO_BOT_PASSWORD}"

cd "${BACKEND_DIR}"
exec uv run python -m app.worker.daemon \
  --controller-url "${AEO_CONTROLLER_URL}" \
  --worker-id "${AEO_WORKER_ID}" \
  --bot-username "${AEO_BOT_USERNAME}" \
  --bot-password "${AEO_BOT_PASSWORD}" \
  --slots "${AEO_WORKER_SLOTS:-1}"
