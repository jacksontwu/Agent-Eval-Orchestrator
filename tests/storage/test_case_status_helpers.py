import pytest

from agent_eval_orchestrator.storage.store import Store


@pytest.mark.unit
def test_case_status_helpers() -> None:
    assert Store._case_is_errored({"status": "errored"}) is True
    assert Store._case_is_errored({"status": "failed", "error_text": "boom"}) is True
    assert Store._case_is_errored({"status": "failed"}) is False
    assert Store._case_is_failed({"status": "failed"}) is True
    assert Store._case_is_failed({"status": "failed", "error_text": "boom"}) is False
