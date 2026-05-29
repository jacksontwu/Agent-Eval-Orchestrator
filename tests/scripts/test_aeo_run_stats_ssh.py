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


def test_case_ids_match_long_selected_and_short_harbor_key() -> None:
    mod = _load_run_stats_module()
    long_selected = (
        "instance_ansible__ansible-12734fa21c08a0ce8c84e533abdc560db2eb1955-"
        "v7eee2454f617569fd6889f2211f75bc02a35f9f8"
    )
    short_harbor = "instance_ansible__ansible-12734f"

    assert mod.case_ids_match(short_harbor, long_selected) is True
    assert mod.lookup_case_entry(long_selected, {short_harbor: {"status": "passed"}}) == {
        "status": "passed",
    }


def test_summarize_case_map_matches_harbor_prefix_keys() -> None:
    mod = _load_run_stats_module()
    long_selected = (
        "instance_ansible__ansible-12734fa21c08a0ce8c84e533abdc560db2eb1955-"
        "v7eee2454f617569fd6889f2211f75bc02a35f9f8"
    )
    case_map = {
        "instance_ansible__ansible-12734f": {
            "status": "passed",
            "scored": True,
            "has_result": True,
        }
    }

    stats = mod.summarize_case_map([long_selected], case_map)

    assert stats["total"] == 1
    assert stats["completed"] == 1
    assert stats["pending"] == 0
    assert stats["score_counts"]["scored"] == 1


def test_is_active_rerun_batch() -> None:
    mod = _load_run_stats_module()

    assert mod.is_active_rerun_batch({"status": "running"}) is True
    assert mod.is_active_rerun_batch({"status": "queued"}) is True
    assert mod.is_active_rerun_batch({"status": "pending_sync"}) is True
    assert mod.is_active_rerun_batch({"status": "succeeded"}) is False
    assert mod.is_active_rerun_batch(None) is False


def test_collect_run_stats_active_exception_only_skips_primary_ssh(monkeypatch, tmp_path: Path) -> None:
    mod = _load_run_stats_module()
    ssh_calls: list[str] = []

    def fake_load_run_plan(_db_path, _run_id):
        return {
            "run": {"rerun_status": "running", "rerun_job_id": "job-1"},
            "rerun_job": None,
            "primary_batches": [
                {
                    "batch_id": "batch-primary",
                    "owner": "alice",
                    "worker_id": "worker-1",
                    "worker": {"worker_id": "worker-1", "capabilities": {}},
                    "expected_case_ids": ["case-a", "case-b"],
                    "expected_cases": 2,
                    "rerun_batch": {
                        "batch_id": "batch-rerun",
                        "status": "running",
                        "selected_case_ids": ["case-a"],
                    },
                },
                {
                    "batch_id": "batch-primary-2",
                    "owner": "alice",
                    "worker_id": "worker-2",
                    "worker": {"worker_id": "worker-2", "capabilities": {}},
                    "expected_case_ids": ["case-c"],
                    "expected_cases": 1,
                    "rerun_batch": {
                        "batch_id": "batch-rerun-done",
                        "status": "succeeded",
                        "selected_case_ids": ["case-c"],
                    },
                },
            ],
        }

    def fake_ssh_analyze_job(*, worker, job_dir, **kwargs):
        ssh_calls.append(job_dir)
        return {
            "completed": 1,
            "running": 0,
            "pending": 0,
            "started_count": 1,
            "cases": {
                "case-a": {"status": "passed", "scored": True, "has_result": True},
            },
        }

    monkeypatch.setattr(mod, "load_run_plan", fake_load_run_plan)
    monkeypatch.setattr(mod, "ssh_analyze_job", fake_ssh_analyze_job)

    payload = mod.collect_run_stats(
        run_id="run-test",
        db_path=tmp_path / "state.sqlite3",
        ssh_config=None,
        ssh_user="djn",
        ssh_key=None,
        connect_timeout_sec=5,
        controller_url=None,
        auth_token="",
        active_exception_only=True,
    )

    assert len(payload["workers"]) == 1
    assert payload["workers"][0]["batch_id"] == "batch-rerun"
    assert payload["active_exception_only"] is True
    assert payload["overall"]["total"] == 1
    assert payload["overall"]["completed"] == 1
    assert len(ssh_calls) == 1
    assert ssh_calls[0].endswith("/batch-rerun/harbor/jobs/batch-rerun")


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
