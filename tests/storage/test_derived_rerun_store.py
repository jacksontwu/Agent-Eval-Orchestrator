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
