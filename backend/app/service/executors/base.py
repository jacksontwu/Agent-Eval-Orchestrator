from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class PreparedBatch:
    command: list[str]
    env: dict[str, str]
    cwd: Path
    batch_root: Path
    local_root: Path
    job_name: str
    jobs_dir: Path
    job_dir: Path
    dataset_path: Path
    worker_log_path: Path
    metadata: dict[str, Any]


@dataclass
class CollectedArtifacts:
    job_dir: Path
    job_result_path: Path | None
    metadata: dict[str, Any]


class Executor(ABC):
    kind: str

    @abstractmethod
    def prepare(
        self,
        *,
        batch: dict[str, Any],
        run: dict[str, Any],
        template: dict[str, Any],
        dataset_ref: str,
        executor_config: dict[str, Any],
        local_root: Path,
        shared_root: Path | None = None,
    ) -> PreparedBatch:
        raise NotImplementedError

    @abstractmethod
    def collect(self, prepared: PreparedBatch) -> CollectedArtifacts:
        raise NotImplementedError
