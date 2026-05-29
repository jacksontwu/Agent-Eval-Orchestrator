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
