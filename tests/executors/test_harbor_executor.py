from __future__ import annotations

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
            "environmentDelete": False,
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
    assert "--no-delete" in shell
    assert "--ae XDG_CONFIG_HOME" not in shell
    assert "-a bitfun-cli" in shell
    assert "-e docker" in shell
    assert "--n-concurrent 1" in shell
    assert "--timeout-multiplier 1.0" in shell
    assert "--agent-timeout-multiplier 3.0" in shell
    assert "--verifier-timeout-multiplier 2.0" in shell
    assert "--environment-build-timeout-multiplier 1.5" in shell
    assert "/root/.config/bitfun" in shell
