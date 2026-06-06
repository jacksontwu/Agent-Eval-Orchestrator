#!/usr/bin/env bash
# Restart the controller: stop (if running) then start.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bash "${SCRIPT_DIR}/stop-controller.sh" || true
bash "${SCRIPT_DIR}/start-controller.sh"
