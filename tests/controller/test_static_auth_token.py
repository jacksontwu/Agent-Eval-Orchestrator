from agent_eval_orchestrator.controller.static import INDEX_HTML


def test_dashboard_api_helper_sends_query_token_header() -> None:
    assert "URLSearchParams(window.location.search)" in INDEX_HTML
    assert "X-AEO-Token" in INDEX_HTML
    assert "fetch(path, requestOptions)" in INDEX_HTML


def test_dashboard_polling_does_not_block_on_task_detail_refresh() -> None:
    assert "async function loadDashboard(options)" in INDEX_HTML
    assert "refreshTaskDetail: false" in INDEX_HTML
    assert "setInterval(() => loadDashboard({ refreshTaskDetail: false }), 5000)" in INDEX_HTML


def test_case_detail_is_lazy_loaded_from_task_detail() -> None:
    assert "async function loadCaseDetail(batchId, caseId)" in INDEX_HTML
    assert '"/api/case-runs?batchId="' in INDEX_HTML
    assert "state.caseDetails[key]" in INDEX_HTML
