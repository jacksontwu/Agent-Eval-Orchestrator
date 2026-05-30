from agent_eval_orchestrator.controller.static import INDEX_HTML


def test_dashboard_api_helper_sends_query_token_header() -> None:
    assert "URLSearchParams(window.location.search)" in INDEX_HTML
    assert "X-AEO-Token" in INDEX_HTML
    assert "fetch(path, requestOptions)" in INDEX_HTML
