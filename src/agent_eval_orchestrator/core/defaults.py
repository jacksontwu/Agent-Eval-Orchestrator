from __future__ import annotations

from pathlib import Path


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8790
DEFAULT_POLL_INTERVAL_SEC = 5
DEFAULT_HEARTBEAT_TIMEOUT_SEC = 45
DEFAULT_SLOTS = 1
DEFAULT_SHARED_ROOT = Path("/root/projects/agent-eval-orchestrator/runtime").resolve()
DEFAULT_HARBOR_REPO = Path("/root/projects/harbor").resolve()
DEFAULT_PRESET_DATASETS = {
    "terminal-bench/terminal-bench-2": Path(
        "/root/projects/agent-eval-orchestrator/datasets/terminal-bench-2"
    ).resolve(),
    "swe-bench/swe-bench-verified": Path(
        "/root/projects/agent-eval-orchestrator/datasets/swe-bench-verified"
    ).resolve(),
}
