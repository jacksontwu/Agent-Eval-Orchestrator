import json

from conftest import seed_finished_run_with_cases


def test_eval_task_detail_dedupes_long_selected_case_ids(store):
    long_selected = (
        "instance_tutao__tutanota-fb32e5f9d9fc152a00144d56dd0af01760a2d4dc-"
        "vc4e41fd0029957297843cb9dec4a25c7c756f029"
    )
    short_case_id = "instance_tutao__tutanota-fb32e5f"
    run, batch = seed_finished_run_with_cases(
        store,
        cases=[
            {
                "case_id": short_case_id,
                "status": "errored",
                "error_text": "boom",
                "artifact_index": {
                    "trialDir": (
                        "/tmp/jobs/batch/instance_tutao__tutanota-fb32e5f__XsXcKQq"
                    ),
                },
            }
        ],
    )
    with store.connect() as conn:
        conn.execute(
            "UPDATE batches SET selected_case_ids_json = ? WHERE batch_id = ?",
            (json.dumps([long_selected], ensure_ascii=False), batch["batch_id"]),
        )
        conn.commit()

    detail = store.get_eval_task_detail(run["run_id"])
    worker_cases = detail["workerGroups"][0]["cases"]

    assert len(worker_cases) == 1
    assert worker_cases[0]["case_id"] == short_case_id
    assert worker_cases[0]["status"] == "errored"


def test_case_covers_selected_matches_prefix_and_exact_ids(store):
    actual_case = {
        "case_id": "instance_tutao__tutanota-fb32e5f",
        "original_case_id": "instance_tutao__tutanota-fb32e5f",
        "artifact_index": {},
    }
    long_selected = (
        "instance_tutao__tutanota-fb32e5f9d9fc152a00144d56dd0af01760a2d4dc-"
        "vc4e41fd0029957297843cb9dec4a25c7c756f029"
    )

    assert store._case_covers_selected(actual_case, long_selected) is True
    assert store._case_covers_selected(actual_case, "other-case") is False
