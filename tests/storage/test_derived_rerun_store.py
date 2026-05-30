from conftest import seed_finished_run_with_cases


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
