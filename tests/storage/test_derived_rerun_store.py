import json
from pathlib import Path

import pytest

from conftest import seed_finished_run_with_cases


def _create_child_run(store, parent):
    parent_template = store.get_task_template(parent["template_id"])
    child_template = store.create_task_template(
        owner=parent_template["owner"],
        name="child-template",
        dataset_ref=parent_template["dataset_ref"],
        executor_kind=parent_template["executor_kind"],
        executor_config=parent_template["executor_config"],
        model_profile_ref=parent_template["model_profile_ref"],
        note=parent_template["note"],
    )
    return store.create_run(
        template_id=child_template["template_id"],
        display_name="child rerun",
        parent_run_id=parent["run_id"],
    )


def test_create_run_accepts_parent_run_id(store):
    parent, _ = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "ok", "status": "succeeded", "score": 1.0}],
    )
    parent_template = store.get_task_template(parent["template_id"])
    child_template = store.create_task_template(
        owner=parent_template["owner"],
        name="child-template",
        dataset_ref=parent_template["dataset_ref"],
        executor_kind=parent_template["executor_kind"],
        executor_config=parent_template["executor_config"],
        model_profile_ref=parent_template["model_profile_ref"],
        note=parent_template["note"],
    )

    child = store.create_run(
        template_id=child_template["template_id"],
        display_name="child rerun",
        parent_run_id=parent["run_id"],
    )

    assert child["parent_run_id"] == parent["run_id"]
    assert store.get_run(child["run_id"])["parent_run_id"] == parent["run_id"]
    listed = {run["run_id"]: run for run in store.list_runs()}
    assert listed[child["run_id"]]["parent_run_id"] == parent["run_id"]
    assert listed[parent["run_id"]]["parent_run_id"] is None


def test_clone_primary_batches_to_run_requires_existing_target(store):
    parent, _ = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "ok", "status": "succeeded", "score": 1.0}],
    )

    with pytest.raises(RuntimeError, match="target run not found"):
        store.clone_primary_batches_to_run(
            source_run_id=parent["run_id"],
            target_run_id="missing-run",
        )


def test_list_active_derived_reruns_for_parent(store):
    parent, _ = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc", "status": "errored", "error_text": "boom"}],
    )
    parent_template = store.get_task_template(parent["template_id"])
    child_template = store.create_task_template(
        owner=parent_template["owner"],
        name="child-template",
        dataset_ref=parent_template["dataset_ref"],
        executor_kind=parent_template["executor_kind"],
        executor_config=parent_template["executor_config"],
        model_profile_ref=parent_template["model_profile_ref"],
        note=parent_template["note"],
    )
    syncing = store.create_run(
        template_id=child_template["template_id"],
        display_name="syncing child",
        parent_run_id=parent["run_id"],
    )
    running = store.create_run(
        template_id=child_template["template_id"],
        display_name="running child",
        parent_run_id=parent["run_id"],
    )
    done = store.create_run(
        template_id=child_template["template_id"],
        display_name="done child",
        parent_run_id=parent["run_id"],
    )
    store.update_run_rerun_fields(run_id=syncing["run_id"], rerun_status="syncing")
    store.update_run_rerun_fields(run_id=running["run_id"], rerun_status="running")
    store.update_run_rerun_fields(run_id=done["run_id"], rerun_status="succeeded")

    active = store.list_active_derived_reruns(parent["run_id"])

    assert [run["run_id"] for run in active] == [
        syncing["run_id"],
        running["run_id"],
    ]


def test_eval_task_summary_uses_active_derived_rerun_status(store):
    parent, _ = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc", "status": "errored", "error_text": "boom"}],
    )
    child = _create_child_run(store, parent)
    store.clone_primary_batches_to_run(
        source_run_id=parent["run_id"],
        target_run_id=child["run_id"],
    )
    store.update_run_rerun_fields(run_id=child["run_id"], rerun_status="syncing")

    summaries = store.list_eval_task_summaries()
    match = next(item for item in summaries if item["runId"] == child["run_id"])

    assert match["status"] == "syncing"


def test_eval_task_summary_includes_derived_run_lineage(store):
    parent, _ = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc", "status": "errored", "error_text": "boom"}],
    )
    child = _create_child_run(store, parent)

    summaries = {item["runId"]: item for item in store.list_eval_task_summaries()}

    assert summaries[parent["run_id"]]["parentRunId"] is None
    assert summaries[parent["run_id"]]["isDerivedRun"] is False
    assert summaries[child["run_id"]]["parentRunId"] == parent["run_id"]
    assert summaries[child["run_id"]]["isDerivedRun"] is True


def test_eval_task_detail_includes_derived_lineage_and_parent_active_children(store):
    parent, _ = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc", "status": "errored", "error_text": "boom"}],
    )
    active_child = _create_child_run(store, parent)
    finished_child = _create_child_run(store, parent)
    store.update_run_rerun_fields(run_id=active_child["run_id"], rerun_status="syncing")
    store.update_run_rerun_fields(run_id=finished_child["run_id"], rerun_status="succeeded")

    parent_detail = store.get_eval_task_detail(parent["run_id"])
    child_detail = store.get_eval_task_detail(active_child["run_id"])

    assert parent_detail["parentRunId"] is None
    assert parent_detail["isDerivedRun"] is False
    assert parent_detail["parentRun"] is None
    assert parent_detail["activeDerivedReruns"] == [
        {
            "runId": active_child["run_id"],
            "name": "child rerun",
            "rerunStatus": "syncing",
        }
    ]
    assert child_detail["parentRunId"] == parent["run_id"]
    assert child_detail["isDerivedRun"] is True
    assert child_detail["parentRun"] == {
        "runId": parent["run_id"],
        "name": parent["display_name"],
        "status": "finished",
        "rerunStatus": "idle",
    }
    assert child_detail["activeDerivedReruns"] == []


def test_clone_primary_batches_to_run_copies_batches_and_cases(store):
    parent, parent_batch = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
            {
                "case_id": "exc",
                "status": "errored",
                "error_text": "boom",
                "metrics": {"trialName": "exc__old"},
                "artifact_index": {"trialDir": "/tmp/jobs/parent/exc__old"},
            },
        ],
    )
    parent_batch = store.get_batch(parent_batch["batch_id"])
    parent_template = store.get_task_template(parent["template_id"])
    child_template = store.create_task_template(
        owner=parent_template["owner"],
        name="child-template",
        dataset_ref=parent_template["dataset_ref"],
        executor_kind=parent_template["executor_kind"],
        executor_config=parent_template["executor_config"],
        model_profile_ref=parent_template["model_profile_ref"],
        note=parent_template["note"],
    )
    child = store.create_run(
        template_id=child_template["template_id"],
        display_name="child rerun",
        parent_run_id=parent["run_id"],
    )

    mapping = store.clone_primary_batches_to_run(
        source_run_id=parent["run_id"],
        target_run_id=child["run_id"],
    )

    assert set(mapping.keys()) == {parent_batch["batch_id"]}
    cloned_batch = store.get_batch(mapping[parent_batch["batch_id"]])
    assert cloned_batch["run_id"] == child["run_id"]
    assert cloned_batch["batch_kind"] == "primary"
    assert cloned_batch["status"] == parent_batch["status"]
    assert cloned_batch["summary"] == parent_batch["summary"]
    assert cloned_batch["selected_case_ids"] == parent_batch["selected_case_ids"]
    assert cloned_batch["preferred_worker_id"] == parent_batch["preferred_worker_id"]
    assert cloned_batch["assigned_worker_id"] == parent_batch["assigned_worker_id"]

    parent_cases = store.list_case_runs(parent_batch["batch_id"])
    cloned_cases = store.list_case_runs(cloned_batch["batch_id"])
    assert [(case["case_id"], case["status"]) for case in cloned_cases] == [
        (case["case_id"], case["status"]) for case in parent_cases
    ]
    assert {case["case_run_id"] for case in cloned_cases}.isdisjoint(
        {case["case_run_id"] for case in parent_cases}
    )


def test_clone_primary_batches_to_run_reads_source_rows_in_clone_transaction(store, monkeypatch):
    parent, _ = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "ok", "status": "succeeded", "score": 1.0}],
    )
    child = _create_child_run(store, parent)

    def fail_list_primary_batches_for_run(run_id):
        raise AssertionError("clone must read source batches through its transaction")

    def fail_list_case_runs(batch_id):
        raise AssertionError("clone must read source cases through its transaction")

    monkeypatch.setattr(store, "list_primary_batches_for_run", fail_list_primary_batches_for_run)
    monkeypatch.setattr(store, "list_case_runs", fail_list_case_runs)

    mapping = store.clone_primary_batches_to_run(
        source_run_id=parent["run_id"],
        target_run_id=child["run_id"],
    )

    assert len(mapping) == 1


def test_clone_primary_batches_to_run_copies_all_batch_and_case_fields(store, temp_layout):
    parent, first_batch = seed_finished_run_with_cases(
        store,
        cases=[
            {
                "case_id": "alpha__original",
                "status": "errored",
                "score": 0.25,
                "error_text": "alpha boom",
                "metrics": {"trialName": "alpha__retry", "latency": 12},
                "artifact_index": {"trialDir": "/tmp/jobs/alpha__retry", "log": "alpha.log"},
            },
        ],
    )
    second_batch = store.create_batch(
        run_id=parent["run_id"],
        selected_case_ids=["beta"],
        preferred_worker_id="worker-a",
        batch_options={"temperature": 0.2},
        initial_status="running",
    )
    store.update_batch_progress(
        batch_id=second_batch["batch_id"],
        worker_id="worker-a",
        status="failed",
        current_step="collecting-results",
        finished=True,
        error_text="batch failed",
        summary={"failed": 1},
        cases=[
            {
                "caseId": "beta",
                "status": "failed",
                "score": 0.0,
                "errorText": "beta failed",
                "metrics": {"cost": 3.14},
                "artifactIndex": {"trialDir": "/tmp/jobs/beta__retry"},
            }
        ],
        executor_metadata={"image": "runner:v2", "attempt": 4},
        artifact_index={"manifest": "manifest.json"},
    )
    with store.connect() as conn:
        conn.execute(
            """
            UPDATE batches
            SET current_step = ?, assigned_worker_id = ?, batch_options_json = ?,
                started_at = ?, finished_at = ?, error_text = ?
            WHERE batch_id = ?
            """,
            (
                "archiving",
                "worker-a",
                json.dumps({"temperature": 0.9, "retries": 2}),
                "2026-05-28T10:00:00+00:00",
                "2026-05-28T10:05:00+00:00",
                "first batch warning",
                first_batch["batch_id"],
            ),
        )
        conn.execute(
            """
            UPDATE case_runs
            SET created_at = ?, updated_at = ?
            WHERE batch_id = ? AND case_id = ?
            """,
            (
                "2026-05-28T09:00:00+00:00",
                "2026-05-28T09:30:00+00:00",
                first_batch["batch_id"],
                "alpha__original",
            ),
        )
    child = _create_child_run(store, parent)

    mapping = store.clone_primary_batches_to_run(
        source_run_id=parent["run_id"],
        target_run_id=child["run_id"],
    )

    assert list(mapping.keys()) == [first_batch["batch_id"], second_batch["batch_id"]]
    child_after_clone = store.get_run(child["run_id"])
    assert child_after_clone["latest_batch_id"] == mapping[second_batch["batch_id"]]

    source_batches = {
        batch["batch_id"]: batch for batch in store.list_primary_batches_for_run(parent["run_id"])
    }
    cloned_batches = {
        source_id: store.get_batch(cloned_id) for source_id, cloned_id in mapping.items()
    }
    for source_id, cloned in cloned_batches.items():
        source = source_batches[source_id]
        assert cloned["run_id"] == child["run_id"]
        assert cloned["owner"] == child["owner"]
        assert cloned["batch_id"] != source_id
        assert cloned["batch_kind"] == "primary"
        assert cloned["parent_batch_id"] is None
        assert Path(cloned["batch_root"]).is_relative_to(
            temp_layout.run_dir(child["owner"], child["run_id"])
        )
        assert cloned["current_step"] == source["current_step"]
        assert cloned["preferred_worker_id"] == source["preferred_worker_id"]
        assert cloned["assigned_worker_id"] == source["assigned_worker_id"]
        assert cloned["executor_kind"] == source["executor_kind"]
        assert cloned["executor_metadata"] == source["executor_metadata"]
        assert cloned["selected_case_ids"] == source["selected_case_ids"]
        assert cloned["batch_options"] == source["batch_options"]
        assert cloned["summary"] == source["summary"]
        assert cloned["artifact_index"] == source["artifact_index"]
        assert cloned["started_at"] == source["started_at"]
        assert cloned["finished_at"] == source["finished_at"]
        assert cloned["error_text"] == source["error_text"]

    source_cases = store.list_case_runs(first_batch["batch_id"])
    cloned_cases = store.list_case_runs(mapping[first_batch["batch_id"]])
    assert len(source_cases) == len(cloned_cases) == 1
    source_case = source_cases[0]
    cloned_case = cloned_cases[0]
    assert cloned_case["case_run_id"] != source_case["case_run_id"]
    assert cloned_case["case_id"] == source_case["case_id"]
    assert cloned_case["original_case_id"] == source_case["original_case_id"]
    assert cloned_case["status"] == source_case["status"]
    assert cloned_case["score"] == source_case["score"]
    assert cloned_case["metrics"] == source_case["metrics"]
    assert cloned_case["artifact_index"] == source_case["artifact_index"]
    assert cloned_case["error_text"] == source_case["error_text"]
    assert cloned_case["created_at"] == "2026-05-28T09:00:00+00:00"
    assert cloned_case["updated_at"] != source_case["updated_at"]
