from __future__ import annotations

from pathlib import Path

import pytest

from agent_eval_orchestrator.storage.layout import Layout
from agent_eval_orchestrator.storage.store import Store


@pytest.fixture()
def temp_layout(tmp_path: Path) -> Layout:
    layout = Layout(tmp_path / "runtime")
    layout.ensure_dirs()
    return layout


@pytest.fixture()
def store(temp_layout: Layout) -> Store:
    return Store(temp_layout)


@pytest.fixture()
def sample_ssh_config(tmp_path: Path) -> Path:
    content = """
Host aeo-ecs-0004-root
    HostName 192.168.0.244
    User root
    IdentityFile ~/.ssh/aeo_admin

Host aeo-ecs-0004
    HostName 192.168.0.244
    User djn
    IdentityFile ~/.ssh/aeo_workers
"""
    path = tmp_path / "ssh_config"
    path.write_text(content.strip() + "\n", encoding="utf-8")
    return path
