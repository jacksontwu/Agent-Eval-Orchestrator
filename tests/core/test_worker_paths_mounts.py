from agent_eval_orchestrator.core.worker_paths import (
    build_harbor_bind_mounts,
    build_sync_bind_mounts,
    default_bitfun_config_dir,
)


def test_default_bitfun_config_dir_for_worker_layout():
    assert default_bitfun_config_dir(
        worker_id="ecs-worker-0001",
        shared_root="/home/djn/worker/agent-eval-orchestrator/runtime",
    ) == "/home/djn/.config/bitfun"


def test_build_sync_bind_mounts():
    mounts = build_sync_bind_mounts(
        uv_binary="/home/djn/.local/bin/uv",
        sync_root="/home/djn/worker/agent-eval-orchestrator/runtime/sync/run-abc",
    )
    assert mounts == [
        {"type": "bind", "source": "/home/djn/.local/bin/uv", "target": "/usr/local/bin/uv", "read_only": True},
        {
            "type": "bind",
            "source": "/home/djn/worker/agent-eval-orchestrator/runtime/sync/run-abc/bitfun/bitfun-cli",
            "target": "/usr/local/bin/bitfun-cli",
            "read_only": True,
        },
        {
            "type": "bind",
            "source": "/home/djn/worker/agent-eval-orchestrator/runtime/sync/run-abc/bitfun/config",
            "target": "/root/.config/bitfun/config",
            "read_only": True,
        },
    ]


def test_build_harbor_bind_mounts():
    mounts = build_harbor_bind_mounts(
        uv_binary="/home/djn/.local/bin/uv",
        harbor_repo="/home/djn/worker/harbor",
        bitfun_config_dir="/home/djn/.config/bitfun",
    )
    assert mounts == [
        {"type": "bind", "source": "/home/djn/.local/bin/uv", "target": "/usr/local/bin/uv", "read_only": True},
        {
            "type": "bind",
            "source": "/home/djn/worker/harbor/BitFun/target/release/bitfun-cli",
            "target": "/usr/local/bin/bitfun-cli",
            "read_only": True,
        },
        {
            "type": "bind",
            "source": "/home/djn/.config/bitfun/config",
            "target": "/root/.config/bitfun/config",
            "read_only": True,
        },
    ]
