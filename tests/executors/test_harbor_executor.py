from __future__ import annotations

import shlex
from pathlib import Path

from agent_eval_orchestrator.executors.harbor import HarborExecutor


def test_prepare_includes_retry_and_environment_flags(tmp_path: Path) -> None:
    batch_root = tmp_path / "batch-root"
    batch_root.mkdir()
    dataset = tmp_path / "dataset" / "case-a"
    dataset.mkdir(parents=True)
    (dataset / "task.toml").write_text("", encoding="utf-8")

    harbor_repo = tmp_path / "harbor"
    harbor_repo.mkdir()

    prepared = HarborExecutor().prepare(
        batch={
            "batch_id": "batch-test",
            "batch_root": str(batch_root),
            "selected_case_ids": ["case-a"],
        },
        run={},
        template={},
        dataset_ref=str(dataset.parent),
        executor_config={
            "agentName": "bitfun-cli",
            "envType": "docker",
            "nConcurrent": 1,
            "maxRetries": 3,
            "environmentForceBuild": False,
            "environmentDelete": True,
            "timeoutMultiplier": 1.0,
            "agentTimeoutMultiplier": 3.0,
            "verifierTimeoutMultiplier": 2.0,
            "environmentBuildTimeoutMultiplier": 1.5,
            "mounts": [
                {"type": "bind", "source": "/home/djn/.local/bin/uv", "target": "/usr/local/bin/uv", "read_only": True},
                {
                    "type": "bind",
                    "source": "/home/djn/worker/harbor/BitFun/target/release/bitfun-cli",
                    "target": "/usr/local/bin/bitfun-cli",
                    "read_only": True,
                },
                {"type": "bind", "source": "/home/djn/.config/bitfun", "target": "/root/.config/bitfun"},
            ],
            "harborRepoPath": str(harbor_repo),
        },
        local_root=tmp_path / "local",
        shared_root=None,
    )

    shell = prepared.command[2]
    assert "--max-retries 3" in shell
    assert "--no-force-build" in shell
    assert "--delete" in shell
    assert "--ae XDG_CONFIG_HOME" not in shell
    assert "-a bitfun-cli" in shell
    assert "-e docker" in shell
    assert "--n-concurrent 1" in shell
    assert "--timeout-multiplier 1.0" in shell
    assert "--agent-timeout-multiplier 3.0" in shell
    assert "--verifier-timeout-multiplier 2.0" in shell
    assert "--environment-build-timeout-multiplier 1.5" in shell
    assert "/root/.config/bitfun" in shell


def test_prepare_includes_model_agent_env_and_agent_kwargs(tmp_path: Path) -> None:
    batch_root = tmp_path / "batch-root"
    batch_root.mkdir()
    dataset = tmp_path / "dataset" / "case-a"
    dataset.mkdir(parents=True)

    harbor_repo = tmp_path / "harbor"
    harbor_repo.mkdir()

    prepared = HarborExecutor().prepare(
        batch={
            "batch_id": "batch-test",
            "batch_root": str(batch_root),
            "selected_case_ids": ["case-a"],
        },
        run={},
        template={},
        dataset_ref=str(dataset.parent),
        executor_config={
            "agentName": "bitfun-cli",
            "envType": "docker",
            "nConcurrent": 1,
            "modelName": "deepseek-v4-pro",
            "agentEnv": {
                "ANTHROPIC_API_KEY": "<your-api-key>",
                "ANTHROPIC_BASE_URL": "<your-base-url>",
                "ANTHROPIC_MODEL": "deepseek-v4-pro",
                "ANTHROPIC_DEFAULT_SONNET_MODEL": "deepseek-v4-pro",
                "ANTHROPIC_DEFAULT_OPUS_MODEL": "deepseek-v4-pro",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-v4-pro",
                "CLAUDE_CODE_SUBAGENT_MODEL": "deepseek-v4-pro",
            },
            "agentKwargs": {
                "thinking": "enabled",
                "reasoning_effort": "max",
            },
            "harborRepoPath": str(harbor_repo),
        },
        local_root=tmp_path / "local",
        shared_root=None,
    )

    shell = prepared.command[2]
    assert "-m deepseek-v4-pro" in shell
    assert f"--ae {shlex.quote('ANTHROPIC_API_KEY=<your-api-key>')}" in shell
    assert f"--ae {shlex.quote('ANTHROPIC_BASE_URL=<your-base-url>')}" in shell
    assert "--ae ANTHROPIC_MODEL=deepseek-v4-pro" in shell
    assert "--ae ANTHROPIC_DEFAULT_SONNET_MODEL=deepseek-v4-pro" in shell
    assert "--ae ANTHROPIC_DEFAULT_OPUS_MODEL=deepseek-v4-pro" in shell
    assert "--ae ANTHROPIC_DEFAULT_HAIKU_MODEL=deepseek-v4-pro" in shell
    assert "--ae CLAUDE_CODE_SUBAGENT_MODEL=deepseek-v4-pro" in shell
    assert "--ak reasoning_effort=max" in shell
    assert "--ak thinking=enabled" in shell


def test_prepare_applies_default_environment_flags_when_config_omits_them(tmp_path: Path) -> None:
    batch_root = tmp_path / "batch-root"
    batch_root.mkdir()
    dataset = tmp_path / "dataset" / "case-a"
    dataset.mkdir(parents=True)
    (dataset / "task.toml").write_text("", encoding="utf-8")

    harbor_repo = tmp_path / "harbor"
    harbor_repo.mkdir()

    prepared = HarborExecutor().prepare(
        batch={
            "batch_id": "batch-test",
            "batch_root": str(batch_root),
            "selected_case_ids": ["case-a"],
        },
        run={},
        template={},
        dataset_ref=str(dataset.parent),
        executor_config={
            "agentName": "bitfun-cli",
            "envType": "docker",
            "nConcurrent": 1,
            "harborRepoPath": str(harbor_repo),
        },
        local_root=tmp_path / "local",
        shared_root=None,
    )

    shell = prepared.command[2]
    assert "--no-force-build" in shell
    assert "--delete" in shell


def test_prepare_claude_code_normalizes_retries_and_agent_kwargs(tmp_path: Path) -> None:
    batch_root = tmp_path / "batch-root"
    batch_root.mkdir()
    dataset = tmp_path / "dataset" / "case-a"
    dataset.mkdir(parents=True)

    harbor_repo = tmp_path / "harbor"
    harbor_repo.mkdir()

    prepared = HarborExecutor().prepare(
        batch={
            "batch_id": "batch-test",
            "batch_root": str(batch_root),
            "selected_case_ids": ["case-a"],
        },
        run={},
        template={},
        dataset_ref=str(dataset.parent),
        executor_config={
            "agentName": "claude-code",
            "envType": "docker",
            "nConcurrent": 1,
            "maxRetries": 0,
            "agentKwargs": {
                "version": "2.1.152",
                "max_turns": 80,
                "thinking": "disabled",
            },
            "harborRepoPath": str(harbor_repo),
        },
        local_root=tmp_path / "local",
        shared_root=None,
    )

    shell = prepared.command[2]
    assert "--max-retries 3" in shell
    assert "--ak version=2.1.152" in shell
    assert "--ak max_turns=80" not in shell
    assert "--ak thinking=disabled" not in shell
