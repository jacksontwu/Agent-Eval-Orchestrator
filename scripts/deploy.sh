#!/usr/bin/env bash
# One-shot deploy: rebuild the frontend bundle and restart the controller so the
# latest frontend (and backend) code takes effect. Run this after changing the
# frontend under frontend/app/.
#
#   AEO_SKIP_FRONTEND=1   skip the pnpm install/build step (backend-only restart)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
FRONTEND_DIR="${REPO_ROOT}/frontend"

if [ "${AEO_SKIP_FRONTEND:-}" != "1" ]; then
  echo "[deploy] building frontend ..."
  cd "${FRONTEND_DIR}"
  pnpm install
  pnpm build
else
  echo "[deploy] skipping frontend build (AEO_SKIP_FRONTEND=1)"
fi

echo "[deploy] restarting controller ..."
bash "${SCRIPT_DIR}/restart-controller.sh"

echo "[deploy] done."
