from app.service.status import (
    case_is_errored,
    case_is_failed,
    case_error_type,
    overall_status_from_batch_counts,
    exception_type_from_text,
)


def test_case_status_helpers():
    assert case_is_errored({"status": "errored"}) is True
    assert case_is_errored({"status": "failed", "error_text": "boom"}) is True
    assert case_is_errored({"status": "failed"}) is False
    assert case_is_failed({"status": "failed"}) is True
    assert case_is_failed({"status": "failed", "error_text": "boom"}) is False


def test_case_error_type():
    assert case_error_type({"status": "errored"}) == "(unknown)"
    assert case_error_type({"errorType": "RewardFileNotFoundError"}) == "RewardFileNotFoundError"
    assert case_error_type({"metrics": {"errorType": "Boom"}}) == "Boom"


def test_overall_status_from_batch_counts():
    base = {"running": 0, "pending_sync": 0, "failed": 0, "sync_failed": 0,
            "queued": 0, "succeeded": 0}
    assert overall_status_from_batch_counts({**base, "running": 1}, True) == "running"
    assert overall_status_from_batch_counts({**base, "failed": 1}, True) == "failed"
    assert overall_status_from_batch_counts({**base, "succeeded": 2}, True) == "finished"
    assert overall_status_from_batch_counts(base, False) == "idle"


def test_exception_type_from_text():
    assert exception_type_from_text("foo\nbar.RewardFileNotFoundError: boom") == "RewardFileNotFoundError"
    assert exception_type_from_text("") == "UnknownException"
