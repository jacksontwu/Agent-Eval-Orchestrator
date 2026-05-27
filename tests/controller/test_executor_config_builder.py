from agent_eval_orchestrator.controller.executor_config import build_asset_sync_executor_config


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
