import os
from pathlib import Path

import pytest

from agent_eval_orchestrator.controller.server import _default_harbor_for_worker, _default_uv_for_worker
from agent_eval_orchestrator.core.worker_paths import (
    default_harbor_repo_from_shared_root,
    default_uv_binary_from_shared_root,
    resolve_harbor_repo,
    resolve_uv_binary,
    user_home_from_shared_root,
)


def test_user_home_from_shared_root_bootstrap_layout():
    shared = "/home/djn/worker/agent-eval-orchestrator/runtime"
    assert user_home_from_shared_root(shared) == Path("/home/djn")
    assert default_uv_binary_from_shared_root(shared) == Path("/home/djn/.local/bin/uv")
    assert default_harbor_repo_from_shared_root(shared) == Path("/home/djn/worker/harbor")


def test_default_uv_for_worker_uses_user_home():
    worker = {
        "capabilities": {
            "sharedRoot": "/home/djn/worker/agent-eval-orchestrator/runtime",
        }
    }
    assert _default_uv_for_worker("ecs-worker-0003", worker) == "/home/djn/.local/bin/uv"
    assert _default_harbor_for_worker("ecs-worker-0003", worker) == "/home/djn/worker/harbor"


def test_resolve_uv_binary_prefers_configured_before_which(tmp_path, monkeypatch):
    configured = tmp_path / "configured-uv"
    configured.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(configured, 0o755)
    wrong = tmp_path / "wrong-uv"
    wrong.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(wrong, 0o755)
    monkeypatch.setenv("PATH", str(tmp_path))

    resolved = resolve_uv_binary(
        configured=str(wrong),
        shared_root="/home/djn/worker/agent-eval-orchestrator/runtime",
    )
    assert resolved == str(wrong)


def test_resolve_uv_binary_falls_back_to_which(tmp_path, monkeypatch):
    uv = tmp_path / "uv"
    uv.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(uv, 0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    shared_root = tmp_path / "home" / "djn" / "worker" / "agent-eval-orchestrator" / "runtime"
    shared_root.mkdir(parents=True)

    resolved = resolve_uv_binary(
        configured="/nonexistent/.local/bin/uv",
        shared_root=shared_root,
    )
    assert resolved == str(uv)


def test_resolve_harbor_repo_prefers_local_shared_root(tmp_path):
    local_harbor = tmp_path / "worker" / "harbor"
    local_harbor.mkdir(parents=True)
    controller_harbor = tmp_path / "controller-harbor"
    controller_harbor.mkdir()
    shared_root = tmp_path / "worker" / "agent-eval-orchestrator" / "runtime"
    shared_root.mkdir(parents=True)

    resolved = resolve_harbor_repo(
        shared_root=shared_root,
        configured=str(controller_harbor),
        default="/missing/default",
    )
    assert resolved == local_harbor.resolve()


def test_resolve_harbor_repo_uses_configured_when_local_missing(tmp_path):
    controller_harbor = tmp_path / "controller-harbor"
    controller_harbor.mkdir()
    shared_root = tmp_path / "other" / "runtime"
    shared_root.mkdir(parents=True)

    resolved = resolve_harbor_repo(
        shared_root=shared_root,
        configured=str(controller_harbor),
        default="/missing/default",
    )
    assert resolved == controller_harbor.resolve()
