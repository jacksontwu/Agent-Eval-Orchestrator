from agent_eval_orchestrator.controller.executor_config import (
    build_asset_sync_executor_config,
    build_executor_config,
)


def test_build_executor_config_maps_dataset_defaults_and_pass_through_keys():
    config = build_executor_config(
        dataset_ref="/root/projects/agent-eval-orchestrator/datasets/demo",
        worker_ids=["worker-a"],
        workers=[
            {
                "worker_id": "worker-a",
                "capabilities": {
                    "sharedRoot": "/home/wt/worker/runtime",
                },
            }
        ],
        body_config={
            "modelName": "deepseek-v4-pro",
            "agentKwargs": {"version": "2.1.152"},
            "processEnv": {"ANTHROPIC_BASE_URL": "https://example.test/v1"},
            "extraArgs": ["--verbose"],
            "mounts": [{"type": "bind", "source": "/tmp/a", "target": "/tmp/b"}],
        },
        jobs_dir="/tmp/harbor/jobs",
    )

    assert config["agentName"] == "bitfun-cli"
    assert config["envType"] == "docker"
    assert config["nConcurrent"] == 1
    assert config["collectJobs"] is True
    assert config["combinedJobsDir"] == "/tmp/harbor/jobs"
    assert config["datasetPathByWorker"]["worker-a"] == "/home/wt/worker/datasets/demo"
    assert config["harborRepoPathByWorker"]["worker-a"] == "/home/wt/harbor"
    assert config["uvBinaryByWorker"]["worker-a"] == "/home/wt/.local/bin/uv"
    assert config["mountsByWorker"]["worker-a"][0]["target"] == "/usr/local/bin/uv"
    assert config["modelName"] == "deepseek-v4-pro"
    assert config["agentKwargs"] == {"version": "2.1.152"}
    assert config["processEnv"] == {"ANTHROPIC_BASE_URL": "https://example.test/v1"}
    assert config["extraArgs"] == ["--verbose"]
    assert config["mounts"] == [{"type": "bind", "source": "/tmp/a", "target": "/tmp/b"}]


def test_build_asset_sync_executor_config_uses_worker_defaults():
    config = build_asset_sync_executor_config(
        worker_ids=["local-a"],
        workers=[
            {
                "worker_id": "local-a",
                "capabilities": {
                    "sharedRoot": "/tmp/controller-runtime",
                    "localToController": True,
                },
            }
        ],
        body_config={
            "agentName": "bitfun-cli",
            "nConcurrent": 4,
            "timeoutMultiplier": 1.2,
            "agentTimeoutMultiplier": 3.5,
            "verifierTimeoutMultiplier": 2.5,
            "environmentBuildTimeoutMultiplier": 1.7,
            "maxRetries": 0,
        },
        jobs_dir="/tmp/harbor/jobs",
    )

    assert config["useAssetSync"] is True
    assert config["agentName"] == "bitfun-cli"
    assert config["nConcurrent"] == 4
    assert config["timeoutMultiplier"] == 1.2
    assert config["agentTimeoutMultiplier"] == 3.5
    assert config["verifierTimeoutMultiplier"] == 2.5
    assert config["environmentBuildTimeoutMultiplier"] == 1.7
    assert config["maxRetries"] == 0
    assert config["combinedJobsDir"] == "/tmp/harbor/jobs"
    assert config["datasetPathByWorker"] == {}
    assert config["mountsByWorker"] == {}
    assert config["harborRepoPathByWorker"]["local-a"].endswith("/harbor")
    assert config["uvBinaryByWorker"]["local-a"].endswith("/.local/bin/uv")
