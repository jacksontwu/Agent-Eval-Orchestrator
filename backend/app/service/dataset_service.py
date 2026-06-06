from __future__ import annotations

from pathlib import Path

import app.core.defaults as defaults
from app.schema.datasets import DatasetInfo


def list_datasets() -> list[DatasetInfo]:
    result: list[DatasetInfo] = []
    for dataset_ref, raw_path in defaults.DEFAULT_PRESET_DATASETS.items():
        path = Path(raw_path).expanduser()
        result.append(DatasetInfo(
            dataset_ref=dataset_ref,
            available=path.is_dir(),
            path=str(path),
        ))
    return result
