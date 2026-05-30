import json

from agent_eval_orchestrator.storage.store import Store
from conftest import seed_finished_run_with_cases


def test_case_error_type_from_metrics():
    case = {"metrics": {"errorType": "TimeoutError"}}
    assert Store.case_error_type(case) == "TimeoutError"


def test_case_error_type_top_level_field():
    case = {"errorType": "RewardFileNotFoundError", "metrics": {}}
    assert Store.case_error_type(case) == "RewardFileNotFoundError"


def test_case_error_type_missing_returns_unknown():
    assert Store.case_error_type({"metrics": {}}) == "(unknown)"
    assert Store.case_error_type({"metrics": {"errorType": ""}}) == "(unknown)"
    assert Store.case_error_type({"metrics": {"errorType": "   "}}) == "(unknown)"


def test_summarize_exception_types_for_run(store):
    run, _ = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
            {"case_id": "exc-a", "status": "errored", "error_text": "a", "metrics": {"errorType": "TimeoutError"}},
            {"case_id": "exc-b", "status": "errored", "error_text": "b", "metrics": {"errorType": "TimeoutError"}},
            {"case_id": "exc-c", "status": "errored", "error_text": "c", "metrics": {"errorType": "RewardFileNotFoundError"}},
            {"case_id": "exc-d", "status": "errored", "error_text": "d", "metrics": {}},
        ],
    )
    summary = store.summarize_exception_types_for_run(run["run_id"])
    assert summary["total"] == 4
    assert summary["byType"] == [
        {"errorType": "TimeoutError", "count": 2},
        {"errorType": "(unknown)", "count": 1},
        {"errorType": "RewardFileNotFoundError", "count": 1},
    ]


def test_summarize_exception_types_empty_run(store):
    template = store.create_task_template(
        owner="default",
        name="empty",
        dataset_ref="/tmp/dataset",
        executor_kind="harbor-docker",
        executor_config={},
        model_profile_ref=None,
        note="",
    )
    run = store.create_run(template_id=template["template_id"])
    summary = store.summarize_exception_types_for_run(run["run_id"])
    assert summary == {"total": 0, "byType": []}


def test_filter_exception_cases_by_types(store):
    run, _ = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "a", "metrics": {"errorType": "TimeoutError"}},
            {"case_id": "exc-b", "status": "errored", "error_text": "b", "metrics": {"errorType": "OtherError"}},
        ],
    )
    filtered = store.filter_exception_cases_by_types(
        run["run_id"],
        ["TimeoutError"],
    )
    assert [item["case_id"] for item in filtered] == ["exc-a"]

    all_items = store.filter_exception_cases_by_types(run["run_id"], None)
    assert len(all_items) == 2


def test_eval_task_detail_includes_exception_summary(store):
    run, _ = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "a", "metrics": {"errorType": "TimeoutError"}},
            {"case_id": "exc-b", "status": "errored", "error_text": "b", "metrics": {"errorType": "OtherError"}},
        ],
    )
    detail = store.get_eval_task_detail(run["run_id"])
    assert detail["exceptionSummary"]["total"] == 2
    assert {entry["errorType"] for entry in detail["exceptionSummary"]["byType"]} == {
        "TimeoutError",
        "OtherError",
    }
    assert detail["exceptionCount"] == 2
    assert detail["exceptionDisplay"]["trialRecordCount"] == 2
    assert detail["exceptionDisplay"]["uniqueCaseCount"] == 2


def test_eval_task_detail_prefers_harbor_exception_txt_summary(store, tmp_path):
    from agent_eval_orchestrator.core.ids import sanitize_name

    run, _ = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "db-exc", "status": "errored", "error_text": "db", "metrics": {"errorType": "DbError"}},
            {"case_id": "disk-exc", "status": "succeeded", "score": 1.0},
        ],
    )
    run_row = store.get_run(run["run_id"])
    job_name = sanitize_name(str(run_row["display_name"]))
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / job_name
    trial_dir = job_dir / "disk-exc__old"
    trial_dir.mkdir(parents=True)
    (trial_dir / "result.json").write_text(
        json.dumps({"task_name": "disk-exc", "trial_name": "disk-exc__old"}),
        encoding="utf-8",
    )
    (trial_dir / "exception.txt").write_text(
        "Traceback (most recent call last):\nPermissionError: denied\n",
        encoding="utf-8",
    )
    store.update_task_template_executor_config(
        str(run_row["template_id"]),
        {"combinedJobsDir": str(jobs_dir)},
    )

    detail = store.get_eval_task_detail(run["run_id"])

    assert detail["exceptionCount"] == 1
    assert detail["canRerunExceptions"] is True
    assert detail["exceptionSummary"] == {
        "total": 1,
        "byType": [{"errorType": "PermissionError", "count": 1}],
    }
    assert detail["exceptionDisplay"]["trialRecordCount"] == 1
    assert detail["exceptionDisplay"]["uniqueCaseCount"] == 1


def test_summarize_exception_display_for_run(store):
    run, _ = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "a", "metrics": {"errorType": "TimeoutError"}},
            {"case_id": "exc-a", "status": "errored", "error_text": "a2", "metrics": {"errorType": "TimeoutError"}},
            {"case_id": "exc-b", "status": "errored", "error_text": "b", "metrics": {"errorType": "OtherError"}},
        ],
    )
    display = store.summarize_exception_display_for_run(run["run_id"])
    assert display["trialRecordCount"] == 3
    assert display["uniqueCaseCount"] == 2


def test_eval_task_detail_includes_harbor_merged_stats(store, tmp_path):
    from agent_eval_orchestrator.core.ids import sanitize_name

    run, _ = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "a", "metrics": {"errorType": "TimeoutError"}},
        ],
    )
    run_row = store.get_run(run["run_id"])
    job_name = sanitize_name(str(run_row["display_name"]))
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / job_name
    job_dir.mkdir(parents=True)
    (job_dir / "result.json").write_text(
        json.dumps(
            {
                "n_total_trials": 10,
                "stats": {"n_errored_trials": 4},
            }
        ),
        encoding="utf-8",
    )
    store.update_task_template_executor_config(
        str(run_row["template_id"]),
        {"combinedJobsDir": str(jobs_dir)},
    )
    detail = store.get_eval_task_detail(run["run_id"])
    assert detail["exceptionDisplay"]["harborMergedErroredTrials"] == 4
    assert detail["exceptionDisplay"]["harborMergedJobName"] == job_name
