from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_run_stats_module():
    repo_root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "aeo_run_stats",
        repo_root / "scripts" / "aeo-run-stats.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_build_ssh_target_keeps_host_alias_out_of_prefix(tmp_path: Path) -> None:
    mod = _load_run_stats_module()
    ssh_config = tmp_path / "config"
    ssh_config.write_text("Host ecs-worker-0001\n  HostName 10.0.0.1\n", encoding="utf-8")
    worker = {"ssh_host_alias": "ecs-worker-0001", "worker_id": "ecs-worker-0001"}

    prefix, target = mod.build_ssh_target(
        worker,
        ssh_config=ssh_config,
        ssh_user="djn",
        ssh_key=None,
    )

    assert prefix == ["-F", str(ssh_config)]
    assert target == "ecs-worker-0001"


def test_ssh_analyze_job_builds_single_host_argument(monkeypatch, tmp_path: Path) -> None:
    mod = _load_run_stats_module()
    ssh_config = tmp_path / "config"
    ssh_config.write_text("Host wutao-worker-0001\n  HostName 10.0.0.2\n", encoding="utf-8")
    worker = {"ssh_host_alias": "wutao-worker-0001", "worker_id": "wutao-worker-0001"}
    captured: dict[str, list[str]] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return type(
            "Result",
            (),
            {"returncode": 0, "stdout": '{"total": 0, "completed": 0, "cases": {}}', "stderr": ""},
        )()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    mod.ssh_analyze_job(
        worker=worker,
        job_dir="/tmp/job",
        ssh_config=ssh_config,
        ssh_user="djn",
        ssh_key=None,
        connect_timeout_sec=5,
    )

    cmd = captured["cmd"]
    host_args = [arg for arg in cmd if arg.endswith("-worker-0001")]
    assert host_args == ["wutao-worker-0001"]
    assert cmd.index("wutao-worker-0001") > cmd.index("-F")
