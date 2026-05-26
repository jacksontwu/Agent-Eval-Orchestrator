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


def seed_finished_run_with_cases(store, *, cases):
    template = store.create_task_template(
        owner="default",
        name="exc-test",
        dataset_ref="/tmp/dataset",
        executor_kind="harbor-docker",
        executor_config={},
        model_profile_ref=None,
        note="",
    )
    run = store.create_run(template_id=template["template_id"])
    store.register_worker(
        worker_id="worker-a",
        display_name="worker-a",
        host="localhost",
        slots_total=1,
        slots_used=0,
        capabilities={},
    )
    batch = store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=[item["case_id"] for item in cases],
        preferred_worker_id="worker-a",
        batch_options={},
    )
    store.update_batch_progress(
        batch_id=batch["batch_id"],
        worker_id="worker-a",
        status="succeeded",
        current_step=None,
        finished=True,
        cases=[
            {
                "caseId": item["case_id"],
                "status": item["status"],
                "score": item.get("score"),
                "errorText": item.get("error_text"),
                "metrics": item.get("metrics") or {},
                "artifactIndex": item.get("artifact_index") or {},
            }
            for item in cases
        ],
    )
    return run, batch
