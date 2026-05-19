from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_eval_orchestrator.core.defaults import DEFAULT_SHARED_ROOT
from agent_eval_orchestrator.core.ids import sanitize_name


@dataclass(frozen=True)
class Layout:
    root: Path

    @property
    def controller_dir(self) -> Path:
        return self.root / "controller"

    @property
    def db_path(self) -> Path:
        return self.controller_dir / "state.sqlite3"

    @property
    def runtime_path(self) -> Path:
        return self.controller_dir / "controller-runtime.json"

    @property
    def archives_dir(self) -> Path:
        return self.root / "archives"

    @property
    def workers_dir(self) -> Path:
        return self.root / "workers"

    def run_dir(self, owner: str, run_id: str) -> Path:
        return self.archives_dir / sanitize_name(owner) / "runs" / sanitize_name(run_id)

    def batch_dir(self, owner: str, run_id: str, batch_id: str) -> Path:
        return self.run_dir(owner, run_id) / "batches" / sanitize_name(batch_id)

    def ensure_dirs(self) -> None:
        for path in (self.controller_dir, self.archives_dir, self.workers_dir):
            path.mkdir(parents=True, exist_ok=True)


def default_layout(root: str | Path | None = None) -> Layout:
    return Layout(Path(root or DEFAULT_SHARED_ROOT).expanduser().resolve())
