from html.parser import HTMLParser

from agent_eval_orchestrator.controller.static import INDEX_HTML


class CreateFormInputParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_create_form = False
        self.inputs: dict[str, dict[str, str | None]] = {}
        self.textareas: dict[str, dict[str, str | None]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        if tag == "form" and attr_map.get("id") == "createTaskForm":
            self.in_create_form = True
        if self.in_create_form and tag == "input" and attr_map.get("name"):
            self.inputs[str(attr_map["name"])] = attr_map
        if self.in_create_form and tag == "textarea" and attr_map.get("name"):
            self.textareas[str(attr_map["name"])] = attr_map

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self.in_create_form:
            self.in_create_form = False


def create_form_inputs() -> dict[str, dict[str, str | None]]:
    parser = CreateFormInputParser()
    parser.feed(INDEX_HTML)
    return parser.inputs


def create_form_textareas() -> dict[str, dict[str, str | None]]:
    parser = CreateFormInputParser()
    parser.feed(INDEX_HTML)
    return parser.textareas


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


def test_rerun_disabled_reason_is_visible_and_prefers_no_exception() -> None:
    no_exception_check = 'if ((detail.exceptionCount || 0) <= 0) return "没有需要重跑的 exception case";'
    unfinished_check = 'return "Run 尚未全部完成";'
    assert no_exception_check in INDEX_HTML
    assert INDEX_HTML.index(no_exception_check) < INDEX_HTML.index(unfinished_check, INDEX_HTML.index(no_exception_check))
    assert "const rerunReason = rerunDisabledReason(detail);" in INDEX_HTML
    assert '<span class="subtle">' in INDEX_HTML


def test_create_form_uses_harbor_yaml_textarea() -> None:
    inputs = create_form_inputs()
    textareas = create_form_textareas()

    assert "harborYaml" in textareas
    assert "name" not in inputs
    assert "agentName" not in inputs
    assert "modelName" not in inputs
    assert "bitfunCliPath" not in inputs
    assert "bitfunConfigDir" not in inputs
    assert "timeoutMultiplier" not in inputs
    assert "selectedCaseIds" not in textareas
    assert "agentEnv" not in textareas
    assert "agentKwargs" not in textareas


def test_create_payload_sends_harbor_yaml_and_worker_ids_only() -> None:
    assert 'harborYaml: String(data.get("harborYaml") || "").trim()' in INDEX_HTML
    assert "任务已创建，正在分发到 worker" in INDEX_HTML
    assert "任务已创建，正在同步资产到 worker" not in INDEX_HTML
