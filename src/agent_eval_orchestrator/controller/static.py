INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Agent Eval Orchestrator</title>
  <style>
    :root {
      --bg: #f3f6f9;
      --fg: #17212b;
      --muted: #617180;
      --border: #d8dfe6;
      --card: #ffffff;
      --accent: #0f6cbd;
      --ok: #147a50;
      --warn: #8a5a00;
      --bad: #b42318;
      --idle: #6c7a86;
      --shadow: 0 12px 40px rgba(15, 23, 42, 0.06);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      color: var(--fg);
      background:
        radial-gradient(circle at top left, #e7f3ff 0, transparent 22%),
        linear-gradient(180deg, #f9fbfc 0%, var(--bg) 100%);
    }
    main {
      max-width: 1440px;
      margin: 0 auto;
      padding: 24px;
    }
    h1, h2, h3, h4 { margin: 0; }
    p { margin: 0; color: var(--muted); }
    code {
      font-family: "IBM Plex Mono", monospace;
      font-size: 12px;
      background: #eef2f5;
      padding: 2px 5px;
      border-radius: 4px;
    }
    button, input, textarea, select {
      font: inherit;
    }
    .header {
      display: flex;
      justify-content: space-between;
      align-items: end;
      gap: 16px;
      margin-bottom: 24px;
    }
    .header-actions {
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }
    .subtle {
      color: var(--muted);
      font-size: 13px;
    }
    .cards {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 16px;
      margin-bottom: 20px;
    }
    .card, .panel, .worker-card, .case-card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 10px;
      box-shadow: var(--shadow);
    }
    .card {
      padding: 16px;
    }
    .metric {
      font-size: 30px;
      font-weight: 600;
      margin-top: 8px;
    }
    .tabs {
      display: inline-flex;
      padding: 4px;
      border-radius: 999px;
      background: #eaf0f5;
      gap: 4px;
      margin-bottom: 20px;
    }
    .tab {
      border: none;
      background: transparent;
      padding: 10px 16px;
      border-radius: 999px;
      color: var(--muted);
      cursor: pointer;
    }
    .tab.active {
      background: var(--card);
      color: var(--fg);
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.08);
    }
    .hidden { display: none !important; }
    .layout {
      display: grid;
      grid-template-columns: 320px minmax(0, 1fr);
      gap: 20px;
      align-items: start;
    }
    .panel {
      min-height: 240px;
      overflow: hidden;
    }
    .panel-header {
      padding: 16px 18px;
      border-bottom: 1px solid var(--border);
    }
    .panel-body {
      padding: 0;
    }
    .filters {
      padding: 14px 18px;
      border-bottom: 1px solid var(--border);
      display: grid;
      grid-template-columns: 1.3fr 0.8fr 0.8fr;
      gap: 12px;
    }
    .field {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .field label {
      font-size: 12px;
      color: var(--muted);
    }
    .field input, .field textarea, .field select {
      width: 100%;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px 12px;
      background: white;
      color: var(--fg);
    }
    .field textarea {
      min-height: 80px;
      resize: vertical;
    }
    .list {
      display: flex;
      flex-direction: column;
    }
    .item {
      width: 100%;
      border: none;
      border-bottom: 1px solid var(--border);
      background: transparent;
      padding: 14px 18px;
      text-align: left;
      cursor: pointer;
    }
    .item:last-child { border-bottom: none; }
    .item:hover, .item.active {
      background: #f7fbff;
    }
    .item-title {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 6px;
    }
    .item-meta {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      color: var(--muted);
      font-size: 12px;
    }
    .detail {
      padding: 18px;
    }
    .detail-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }
    .stat {
      background: #f8fafc;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px;
    }
    .stat strong {
      display: block;
      font-size: 22px;
      margin-top: 6px;
    }
    .worker-tabs {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-bottom: 16px;
    }
    .worker-tab {
      border: 1px solid var(--border);
      background: #f7f9fb;
      color: var(--muted);
      border-radius: 999px;
      padding: 8px 12px;
      cursor: pointer;
    }
    .worker-tab.active {
      background: var(--accent);
      color: white;
      border-color: var(--accent);
    }
    .worker-pane {
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 16px;
      background: #fbfdff;
    }
    .worker-meta {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      color: var(--muted);
      font-size: 12px;
      margin-top: 8px;
      margin-bottom: 14px;
    }
    .case-list {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 12px;
      margin-top: 14px;
    }
    .case-card {
      padding: 12px;
      text-align: left;
    }
    .case-title {
      display: flex;
      justify-content: space-between;
      gap: 8px;
      align-items: start;
      margin-bottom: 8px;
    }
    .case-meta {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 10px;
    }
    .case-actions {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .queue-summary {
      padding: 14px 18px;
      border-bottom: 1px solid var(--border);
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
      gap: 12px;
    }
    .queue-row {
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px;
      background: #fbfdff;
      margin-bottom: 8px;
    }
    .queue-row:last-child {
      margin-bottom: 0;
    }
    .queue-title {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 6px;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 12px;
      line-height: 20px;
      color: white;
      white-space: nowrap;
    }
    .ok { background: var(--ok); }
    .warn { background: var(--warn); }
    .bad { background: var(--bad); }
    .idle { background: var(--idle); }
    .link-btn {
      border: none;
      background: none;
      color: var(--accent);
      cursor: pointer;
      padding: 0;
      text-decoration: underline;
    }
    .primary, .ghost {
      border-radius: 8px;
      padding: 8px 12px;
      cursor: pointer;
    }
    a.primary, a.ghost {
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
    }
    .primary {
      border: none;
      background: var(--accent);
      color: white;
    }
    .ghost {
      border: 1px solid var(--border);
      background: white;
      color: var(--fg);
    }
    .actions {
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }
    pre {
      margin: 0;
      background: #0d1822;
      color: #d9e7f2;
      padding: 14px;
      border-radius: 10px;
      overflow: auto;
      font-family: "IBM Plex Mono", monospace;
      font-size: 12px;
      line-height: 1.45;
    }
    .empty {
      padding: 24px 18px;
      color: var(--muted);
    }
    .modal.hidden {
      display: none;
    }
    .modal {
      position: fixed;
      inset: 0;
      z-index: 1000;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
      background: rgba(9, 18, 28, 0.58);
      backdrop-filter: blur(4px);
    }
    .modal-card {
      width: min(1100px, 100%);
      max-height: min(88vh, 920px);
      display: flex;
      flex-direction: column;
      border-radius: 12px;
      overflow: hidden;
      background: white;
      border: 1px solid var(--border);
      box-shadow: 0 24px 80px rgba(15, 23, 42, 0.28);
    }
    .modal-header {
      display: flex;
      justify-content: space-between;
      align-items: start;
      gap: 16px;
      padding: 16px 18px;
      border-bottom: 1px solid var(--border);
      background: #f8fbfd;
    }
    .modal-body {
      padding: 0;
      overflow: auto;
    }
    .modal-close {
      border: 1px solid var(--border);
      background: white;
      color: var(--fg);
      border-radius: 999px;
      width: 34px;
      height: 34px;
      cursor: pointer;
      flex: 0 0 auto;
    }
    iframe {
      width: 100%;
      height: 720px;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: white;
    }
    @media (max-width: 1180px) {
      .layout { grid-template-columns: 1fr; }
      .filters { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main>
    <div class="header">
      <div>
        <h1>Agent Eval Orchestrator</h1>
        <p>页面版本 2026-05-21-1 · timeout 配置、磁盘保护与失败占位状态已更新</p>
      </div>
      <div class="header-actions">
        <a class="primary" id="openCreateBtn" href="/create">创建分布式任务</a>
        <div class="subtle" id="updatedAt">加载中</div>
      </div>
    </div>

    <div class="cards">
      <div class="card"><div class="subtle">Tasks</div><div class="metric" id="taskCount">0</div></div>
      <div class="card"><div class="subtle">Online Workers</div><div class="metric" id="workerCount">0</div></div>
      <div class="card"><div class="subtle">Running Tasks</div><div class="metric" id="runningCount">0</div></div>
      <div class="card"><div class="subtle">Finished Tasks</div><div class="metric" id="finishedCount">0</div></div>
    </div>

    <div class="tabs">
      <button class="tab active" data-tab="tasks">Tasks</button>
      <button class="tab" data-tab="workers">Workers</button>
    </div>

    <section id="tasksView">
      <div class="layout">
        <div class="panel">
          <div class="panel-header">
            <h2>Task List</h2>
          </div>
          <div class="filters">
            <div class="field">
              <label>搜索</label>
              <input id="taskSearch" placeholder="按 task / worker / case 搜索" />
            </div>
            <div class="field">
              <label>状态筛选</label>
              <input id="taskStatusFilter" placeholder="running / finished / failed" />
            </div>
            <div class="field">
              <label>Worker 筛选</label>
              <input id="taskWorkerFilter" placeholder="local-a / remote-case" />
            </div>
          </div>
          <div class="panel-body list" id="taskList"></div>
        </div>

        <div class="panel">
          <div class="panel-header">
            <div>
              <h2 id="taskDetailTitle">Task Detail</h2>
              <div class="subtle" id="taskDetailHint">点击左侧 task 查看分 worker 的 case 运行结果</div>
            </div>
          </div>
          <div class="panel-body detail" id="taskDetail"></div>
        </div>
      </div>
    </section>

    <section id="workersView" class="hidden">
      <div class="layout">
        <div class="panel">
          <div class="panel-header">
            <h2>Workers</h2>
          </div>
          <div id="workerRuntimeSummary"></div>
          <div class="panel-body list" id="workerList"></div>
        </div>
        <div class="panel">
          <div class="panel-header">
            <div>
              <h2 id="workerDetailTitle">Worker Detail</h2>
              <div class="subtle">手动配置 worker 的基础参数</div>
            </div>
          </div>
          <div class="panel-body detail" id="workerDetail"></div>
        </div>
      </div>
    </section>

    <section id="createView" class="hidden">
      <div class="panel">
        <div class="panel-header">
          <div>
            <h2>Create Distributed Eval Task</h2>
            <div class="subtle">创建一次 bitfun-cli Harbor 评测任务，controller 会自动切分 case 并合并 jobs</div>
          </div>
        </div>
        <div class="detail">
          <form id="createTaskForm">
            <div class="detail-grid" style="margin-bottom:16px">
              <div class="field">
                <label>Task Name</label>
                <input name="name" placeholder="terminal-bench-2-bitfun" required />
              </div>
              <div class="field">
                <label>Executor</label>
                <input name="executorKind" value="harbor-docker" readonly />
              </div>
              <div class="field">
                <label>Agent Name</label>
                <input name="agentName" value="bitfun-cli" readonly />
              </div>
              <div class="field">
                <label>Per Worker Concurrency</label>
                <input name="nConcurrent" type="number" min="1" value="1" required />
              </div>
            </div>

            <div class="detail-grid" style="margin-bottom:16px">
              <div class="field">
                <label>Timeout Multiplier</label>
                <input name="timeoutMultiplier" type="number" min="0.1" step="0.1" value="1.0" />
              </div>
              <div class="field">
                <label>Agent Timeout Multiplier</label>
                <input name="agentTimeoutMultiplier" type="number" min="0.1" step="0.1" value="3.0" />
              </div>
              <div class="field">
                <label>Verifier Timeout Multiplier</label>
                <input name="verifierTimeoutMultiplier" type="number" min="0.1" step="0.1" value="2.0" />
              </div>
              <div class="field">
                <label>Environment Build Multiplier</label>
                <input name="environmentBuildTimeoutMultiplier" type="number" min="0.1" step="0.1" value="1.5" />
              </div>
            </div>

            <div class="detail-grid" style="margin-bottom:16px">
              <div class="field">
                <label>Dataset Ref</label>
                <select name="datasetRef" id="datasetRefSelect" required></select>
              </div>
              <div class="field">
                <label>Jobs Dir</label>
                <input name="jobsDir" value="/root/projects/harbor/jobs" required />
              </div>
            </div>

            <div class="field" style="margin-bottom:16px">
              <label>Selected Case IDs（每行一个，可空）</label>
              <textarea name="selectedCaseIds" placeholder="留空则执行全量 dataset，并平均分配给选中的 worker&#10;astropy__astropy-12907&#10;astropy__astropy-13033"></textarea>
            </div>

            <div style="margin-bottom:16px">
              <h3 style="margin-bottom:10px">Workers</h3>
              <div class="subtle" style="margin-bottom:10px">勾选参与执行的 worker；Harbor、dataset、uv 路径由 controller 基于 worker 注册信息自动推断</div>
              <div id="createWorkerConfigs"></div>
            </div>

            <div class="actions">
              <button class="primary" type="submit">创建并分发任务</button>
            </div>
          </form>

          <div id="createResult" class="hidden" style="margin-top:16px"></div>
        </div>
      </div>
    </section>
  </main>

  <div class="modal hidden" id="previewModal">
    <div class="modal-card">
      <div class="modal-header">
        <div>
          <h3 id="previewModalTitle">文件预览</h3>
          <div class="subtle" id="previewModalPath">-</div>
        </div>
        <button class="modal-close" id="previewModalClose" aria-label="关闭">×</button>
      </div>
      <div class="modal-body">
        <pre id="previewModalContent"></pre>
      </div>
    </div>
  </div>

  <script>
    const state = {
      tasks: [],
      workers: [],
      workerRuntime: null,
      selectedTaskId: null,
      selectedWorkerId: null,
      taskDetail: null,
      filePreview: null,
      viewerInfo: null,
      selectedCase: null,
      selectedTaskWorkerId: null,
      createResult: null,
      datasets: [],
      filters: {
        search: "",
        status: "",
        worker: "",
      },
    };

    function badge(value) {
      const lower = String(value || "").toLowerCase();
      let cls = "idle";
      if (["online", "finished", "succeeded", "completed", "enabled"].includes(lower)) cls = "ok";
      else if (["running", "queued"].includes(lower)) cls = "warn";
      else if (["failed", "forbidden", "unavailable", "stopped", "disabled", "mixed", "interrupted"].includes(lower)) cls = "bad";
      else if (["missing-result"].includes(lower)) cls = "warn";
      return '<span class="badge ' + cls + '">' + value + "</span>";
    }

    function fmtNumber(value) {
      return value == null ? "-" : Number(value).toLocaleString();
    }

    function fmtMetric(value) {
      return value == null ? "n/a" : Number(value).toLocaleString();
    }

    function parsePositiveNumber(value, fallback = null) {
      const parsed = Number(value);
      return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
    }

    function fmtTrace(item) {
      return item.hasTrajectory ? "yes" : "n/a";
    }

    function fmtTraceMetric(item, key) {
      return item.hasTrajectory ? fmtMetric(item[key]) : "n/a";
    }

    function fmtDuration(ms) {
      if (ms == null) return "-";
      if (ms < 1000) return ms + "ms";
      const sec = Math.floor(ms / 1000);
      if (sec < 60) return sec + "s";
      const min = Math.floor(sec / 60);
      const rem = sec % 60;
      return min + "m " + rem + "s";
    }

    function esc(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
    }

    async function api(path, options) {
      const res = await fetch(path, options);
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || ("HTTP " + res.status));
      }
      return res.json();
    }

    function setTab(tab) {
      document.querySelectorAll(".tab").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.tab === tab);
      });
      document.getElementById("tasksView").classList.toggle("hidden", tab !== "tasks");
      document.getElementById("createView").classList.toggle("hidden", tab !== "create");
      document.getElementById("workersView").classList.toggle("hidden", tab !== "workers");
    }

    async function loadDashboard() {
      const [tasksPayload, workersPayload, workerRuntimePayload] = await Promise.all([
        api("/api/dashboard/tasks"),
        api("/api/workers"),
        api("/api/workers/runtime"),
      ]);
      state.tasks = tasksPayload.items;
      state.workers = workersPayload;
      state.workerRuntime = workerRuntimePayload;
      try {
        const datasetsPayload = await api("/api/datasets");
        state.datasets = datasetsPayload.items || [];
      } catch (_error) {
        state.datasets = [];
      }
      document.getElementById("updatedAt").textContent = "最后刷新: " + tasksPayload.time;
      document.getElementById("taskCount").textContent = state.tasks.length;
      document.getElementById("workerCount").textContent = state.workers.filter(w => w.status === "online").length;
      document.getElementById("runningCount").textContent = state.tasks.filter(t => t.status === "running").length;
      document.getElementById("finishedCount").textContent = state.tasks.filter(t => t.status === "finished").length;
      renderTaskList();
      renderWorkerRuntimeSummary();
      renderWorkerList();
      renderCreateWorkerConfigs();
      renderDatasetOptions();
      renderCreateResult();
      if (state.selectedTaskId) {
        await loadTaskDetail(state.selectedTaskId);
      }
      if (state.selectedWorkerId) {
        renderWorkerDetail();
      }
    }

    function runtimeForWorker(workerId) {
      const items = state.workerRuntime?.workers || [];
      return items.find(item => item.workerId === workerId) || null;
    }

    function renderWorkerRuntimeSummary() {
      const el = document.getElementById("workerRuntimeSummary");
      if (!el) return;
      const summary = state.workerRuntime?.summary || {};
      el.innerHTML = '' +
        '<div class="queue-summary">' +
          '<div class="stat"><div class="subtle">Running batches</div><strong>' + esc(summary.runningBatches ?? 0) + '</strong></div>' +
          '<div class="stat"><div class="subtle">Queued batches</div><strong>' + esc(summary.queuedBatches ?? 0) + '</strong></div>' +
          '<div class="stat"><div class="subtle">Shared queue</div><strong>' + esc(summary.sharedQueuedBatches ?? 0) + '</strong></div>' +
        '</div>';
    }

    function runtimeBatchRow(item) {
      return '' +
        '<div class="queue-row">' +
          '<div class="queue-title">' +
            '<strong>' + esc(item.runName || item.taskName || item.runId || "-") + '</strong>' +
            badge(item.status || "-") +
          '</div>' +
          '<div class="item-meta">' +
            '<span>batch: <code>' + esc(item.batchId || "-") + '</code></span>' +
            '<span>cases: ' + esc(item.caseCount ?? 0) + '</span>' +
            (item.queuePosition ? '<span>queue: #' + esc(item.queuePosition) + '</span>' : '') +
            (item.currentStep ? '<span>step: ' + esc(item.currentStep) + '</span>' : '') +
          '</div>' +
        '</div>';
    }

    function preferredDatasetRef() {
      if (!state.datasets.length) return "";
      const terminalBench = state.datasets.find(item => String(item.path || "").endsWith("/terminal-bench-2"));
      return terminalBench ? terminalBench.path : (state.datasets[0] ? state.datasets[0].path : "");
    }

    function renderDatasetOptions() {
      const list = document.getElementById("datasetRefSelect");
      const form = document.getElementById("createTaskForm");
      if (!list || !form) return;
      list.innerHTML = (state.datasets || []).map(item =>
        '<option value="' + esc(item.path) + '">' + esc(item.label) + '</option>'
      ).join("");
      const current = String(form.elements.datasetRef.value || "").trim();
      if (!current) {
        const preferred = preferredDatasetRef();
        if (preferred) {
          form.elements.datasetRef.value = preferred;
        }
      }
    }

    function filteredTasks() {
      return state.tasks.filter(task => {
        const haystack = [
          task.name,
          task.executorKind,
          task.datasetRef,
          ...(task.workers || []),
        ].join(" ").toLowerCase();
        const search = state.filters.search.trim().toLowerCase();
        const status = state.filters.status.trim().toLowerCase();
        const worker = state.filters.worker.trim().toLowerCase();
        if (search && !haystack.includes(search)) return false;
        if (status && String(task.status || "").toLowerCase() !== status) return false;
        if (worker) {
          const workerText = (task.workers || []).join(" ").toLowerCase();
          if (!workerText.includes(worker)) return false;
        }
        return true;
      });
    }

    function renderTaskList() {
      const el = document.getElementById("taskList");
      const tasks = filteredTasks();
      if (!tasks.length) {
        el.innerHTML = '<div class="empty">暂无匹配的 task</div>';
        return;
      }
      el.innerHTML = tasks.map(task => {
        const active = task.evalTaskId === state.selectedTaskId ? " active" : "";
        return '<button class="item' + active + '" data-task-id="' + esc(task.evalTaskId) + '">' +
          '<div class="item-title"><strong>' + esc(task.name) + '</strong>' + badge(task.status) + '</div>' +
          '<div class="item-meta">' +
            '<span>executor: <code>' + esc(task.executorKind) + '</code></span>' +
            '<span>dataset: <code>' + esc(task.datasetRef) + '</code></span>' +
          '</div>' +
          '<div class="item-meta">' +
            '<span>workers: ' + esc((task.workers || []).join(", ") || "-") + '</span>' +
            '<span>run: <code>' + esc(task.runId) + '</code></span>' +
            '<span>batches: ' + task.batchCount + '</span>' +
            '<span>cases: ' + task.caseSucceeded + '/' + task.caseTotal + '</span>' +
          '</div>' +
        '</button>';
      }).join("");
      el.querySelectorAll("[data-task-id]").forEach(btn => {
        btn.addEventListener("click", async () => {
          state.selectedTaskId = btn.dataset.taskId;
          state.selectedCase = null;
          state.filePreview = null;
          state.viewerInfo = null;
          await loadTaskDetail(state.selectedTaskId);
          renderTaskList();
        });
      });
    }

    async function loadTaskDetail(runId) {
      state.taskDetail = await api("/api/eval-tasks/" + encodeURIComponent(runId));
      const groups = state.taskDetail.workerGroups || [];
      if (!groups.find(group => group.workerId === state.selectedTaskWorkerId)) {
        state.selectedTaskWorkerId = groups[0] ? groups[0].workerId : null;
      }
      renderTaskDetail();
    }

    async function previewFile(path) {
      state.filePreview = await api("/api/files/read?path=" + encodeURIComponent(path));
      renderPreviewModal();
    }

    async function openHarborViewer(batchId) {
      state.viewerInfo = await api("/api/batches/" + encodeURIComponent(batchId) + "/viewer", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: "{}",
      });
      renderTaskDetail();
    }

    async function openGlobalHarborViewer() {
      const info = await api("/api/harbor-viewer/global", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: "{}",
      });
      if (!info.available) {
        alert(info.reason || "Harbor Viewer 启动失败");
        return;
      }
      window.open(info.url, "_blank", "noopener,noreferrer");
    }

    function renderTaskDetail() {
      const root = document.getElementById("taskDetail");
      const detail = state.taskDetail;
      if (!detail) {
        root.innerHTML = '<div class="empty">点击左侧 task 查看详情</div>';
        return;
      }
      const template = detail.template;
      const run = detail.run;
      const groups = detail.workerGroups || [];
      document.getElementById("taskDetailTitle").textContent = run.display_name;
      document.getElementById("taskDetailHint").textContent = (template ? template.name + " · " : "") + (template ? template.dataset_ref : "");

      root.innerHTML =
        '<div class="detail-grid">' +
          '<div class="stat"><div class="subtle">Workers</div><strong>' + groups.length + '</strong></div>' +
          '<div class="stat"><div class="subtle">Run ID</div><strong><code>' + esc(run.run_id) + '</code></strong></div>' +
          '<div class="stat"><div class="subtle">Executor</div><strong><code>' + esc(template.executor_kind) + '</code></strong></div>' +
        '</div>' +
        '<div class="actions" style="margin-bottom:16px">' +
          '<button class="primary" type="button" id="openGlobalViewerBtn">打开 Harbor Viewer</button>' +
        '</div>' +
        (groups.length ? renderWorkerTabsHtml(groups) : '<div class="empty">这个 task 暂无 worker 执行记录</div>') +
        renderViewerMountHtml();

      root.querySelectorAll("[data-preview-path]").forEach(btn => {
        btn.addEventListener("click", async () => {
          await previewFile(btn.dataset.previewPath);
        });
      });
      root.querySelectorAll("[data-open-viewer]").forEach(btn => {
        btn.addEventListener("click", async () => {
          await openHarborViewer(btn.dataset.openViewer);
        });
      });
      const globalViewerBtn = root.querySelector("#openGlobalViewerBtn");
      if (globalViewerBtn) {
        globalViewerBtn.addEventListener("click", openGlobalHarborViewer);
      }
      root.querySelectorAll("[data-case-key]").forEach(btn => {
        btn.addEventListener("click", () => {
          const workerId = btn.dataset.workerId;
          const batchId = btn.dataset.batchId;
          const caseId = btn.dataset.caseId;
          state.selectedCase = {workerId, batchId, caseId};
          renderTaskDetail();
        });
      });
      root.querySelectorAll("[data-worker-tab]").forEach(btn => {
        btn.addEventListener("click", () => {
          state.selectedTaskWorkerId = btn.dataset.workerTab;
          state.selectedCase = null;
          state.filePreview = null;
          state.viewerInfo = null;
          renderTaskDetail();
        });
      });
    }

    function renderWorkerTabsHtml(groups) {
      const selectedGroup = groups.find(group => group.workerId === state.selectedTaskWorkerId) || groups[0];
      const tabs = '<div class="worker-tabs">' + groups.map(group =>
        '<button class="worker-tab' + (group.workerId === selectedGroup.workerId ? ' active' : '') + '" data-worker-tab="' + esc(group.workerId) + '">' +
          esc(group.workerName) + ' ' + badge(group.workerStatus) +
        '</button>'
      ).join('') + '</div>';
      return tabs + renderWorkerPaneHtml(selectedGroup);
    }

    function renderWorkerPaneHtml(group) {
      const cases = group.cases || [];
      const selected = state.selectedCase;
      const selectedCase = selected
        ? cases.find(item => item.batchId === selected.batchId && item.case_id === selected.caseId)
        : null;
      return '' +
        '<div class="worker-pane">' +
          '<h3>' + esc(group.workerName) + '</h3>' +
          '<div class="worker-meta">' +
            '<span>workerId: <code>' + esc(group.workerId) + '</code></span>' +
            '<span>status: ' + badge(group.workerStatus) + '</span>' +
            '<span>batches: ' + (group.batches || []).length + '</span>' +
            '<span>cases: ' + cases.length + '</span>' +
          '</div>' +
          '<div class="case-list">' +
            (cases.map(item =>
              '<div class="case-card">' +
                '<div class="case-title"><strong>' + esc(item.case_id) + '</strong>' + badge(item.status) + '</div>' +
                '<div class="case-meta">' +
                  '<span>batch: <code>' + esc(item.batchId) + '</code></span>' +
                '</div>' +
                (selectedCase && selectedCase.case_id === item.case_id && selectedCase.batchId === item.batchId ? renderSelectedCaseHtml(selectedCase) : '') +
              '</div>'
            ).join("") || '<div class="empty">暂无 case 结果</div>') +
          '</div>' +
        '</div>';
    }

    function renderSelectedCaseHtml(item) {
      const artifact = item.artifact_index || {};
      return '' +
        '<div style="margin-top:10px">' +
          '<div class="subtle" style="margin-bottom:6px">Case Detail</div>' +
          '<div class="detail-grid" style="margin-bottom:10px">' +
            '<div class="stat"><div class="subtle">trial</div><strong><code>' + esc(item.trialName || "-") + '</code></strong></div>' +
            '<div class="stat"><div class="subtle">reward</div><strong>' + esc(item.score == null ? "-" : item.score) + '</strong></div>' +
            '<div class="stat"><div class="subtle">duration</div><strong>' + esc(fmtDuration(item.durationMs)) + '</strong></div>' +
            '<div class="stat"><div class="subtle">cost</div><strong>' + esc(item.costUsd == null ? "-" : item.costUsd) + '</strong></div>' +
          '</div>' +
          '<div class="case-meta" style="margin-bottom:10px">' +
            '<span>agent: ' + esc(item.agentName || "-") + ' ' + esc(item.agentVersion || "") + '</span>' +
            '<span>model: ' + esc(item.modelName || "-") + '</span>' +
            '<span>provider: ' + esc(item.provider || "-") + '</span>' +
            '<span>trace: ' + esc(fmtTrace(item)) + '</span>' +
          '</div>' +
          ((!item.hasTrajectory || item.inputTokens == null) ? '<div class="empty" style="padding:10px 12px;margin-bottom:10px">当前 agent 未产出 token 或 trajectory，相关字段按 n/a 展示。Harbor 原始结果仍可通过 result/log 查看。</div>' : '') +
          '<table><tbody>' +
            '<tr><th>startedAt</th><td><code>' + esc(item.startedAt || "-") + '</code></td></tr>' +
            '<tr><th>finishedAt</th><td><code>' + esc(item.finishedAt || "-") + '</code></td></tr>' +
            '<tr><th>environmentSetup</th><td>' + esc(fmtDuration(item.environmentSetupMs)) + '</td></tr>' +
            '<tr><th>agentSetup</th><td>' + esc(fmtDuration(item.agentSetupMs)) + '</td></tr>' +
            '<tr><th>agentExecution</th><td>' + esc(fmtDuration(item.agentExecutionMs)) + '</td></tr>' +
            '<tr><th>verifier</th><td>' + esc(fmtDuration(item.verifierMs)) + '</td></tr>' +
            '<tr><th>inputTokens</th><td>' + esc(fmtMetric(item.inputTokens)) + '</td></tr>' +
            '<tr><th>cachedInputTokens</th><td>' + esc(fmtMetric(item.cachedInputTokens)) + '</td></tr>' +
            '<tr><th>outputTokens</th><td>' + esc(fmtMetric(item.outputTokens)) + '</td></tr>' +
            '<tr><th>toolSummary</th><td><code>' + esc(JSON.stringify(item.toolSummary || {})) + '</code></td></tr>' +
            '<tr><th>errorType</th><td>' + esc(item.errorType || "-") + '</td></tr>' +
            '<tr><th>errorText</th><td>' + esc(item.errorText || "-") + '</td></tr>' +
            '<tr><th>trialDir</th><td><code>' + esc(artifact.trialDir || "-") + '</code></td></tr>' +
            '<tr><th>resultPath</th><td><code>' + esc(artifact.resultPath || "-") + '</code></td></tr>' +
            '<tr><th>logPath</th><td><code>' + esc(artifact.logPath || "-") + '</code></td></tr>' +
            '<tr><th>agentDir</th><td><code>' + esc(artifact.agentDir || "-") + '</code></td></tr>' +
            '<tr><th>verifierDir</th><td><code>' + esc(artifact.verifierDir || "-") + '</code></td></tr>' +
          '</tbody></table>' +
        '</div>';
    }

    function renderViewerMountHtml() {
      if (!state.viewerInfo) return "";
      if (!state.viewerInfo.available) {
        return '<div class="empty">' + esc(state.viewerInfo.reason || "Harbor viewer 当前不可用") + '</div>';
      }
      return '' +
        '<div style="margin-top:18px">' +
          '<h3 style="margin-bottom:8px">Harbor Viewer</h3>' +
          '<iframe src="' + esc(state.viewerInfo.embeddedUrl) + '"></iframe>' +
        '</div>';
    }

    function renderPreviewModal() {
      const modal = document.getElementById("previewModal");
      if (!state.filePreview) {
        modal.classList.add("hidden");
        return;
      }
      document.getElementById("previewModalTitle").textContent = "文件预览";
      document.getElementById("previewModalPath").textContent = state.filePreview.path || "-";
      document.getElementById("previewModalContent").textContent = state.filePreview.content || "";
      modal.classList.remove("hidden");
    }

    function renderCreateWorkerConfigs() {
      const root = document.getElementById("createWorkerConfigs");
      if (!root) return;
      const form = document.getElementById("createTaskForm");
      const previousValues = {};
      if (form) {
        state.workers.forEach(worker => {
          previousValues[worker.worker_id] = {
            selected: form.querySelector('input[name="workerIds"][value="' + worker.worker_id + '"]')?.checked,
          };
        });
      }
      if (!state.workers.length) {
        root.innerHTML = '<div class="empty">当前还没有可用 worker，先等待 worker 注册进来。</div>';
        return;
      }
      root.innerHTML = '<div class="case-list">' + state.workers.map(worker => {
        const previous = previousValues[worker.worker_id] || {};
        const checked = (previous.selected != null ? previous.selected : worker.status === "online") ? " checked" : "";
        return '' +
          '<div class="worker-card" style="padding:14px">' +
            '<div class="case-title">' +
              '<label style="display:flex;gap:10px;align-items:center;cursor:pointer">' +
                '<input type="checkbox" name="workerIds" value="' + esc(worker.worker_id) + '"' + checked + ' />' +
                '<strong>' + esc(worker.display_name) + '</strong>' +
              '</label>' +
              badge(worker.status) +
            '</div>' +
            '<div class="case-meta">' +
              '<span>workerId: <code>' + esc(worker.worker_id) + '</code></span>' +
              '<span>host: <code>' + esc(worker.host || "-") + '</code></span>' +
              '<span>slots: ' + esc(worker.slots_used + "/" + worker.slots_total) + '</span>' +
            '</div>' +
          '</div>';
      }).join('') + '</div>';
    }

    function renderCreateResult() {
      const root = document.getElementById("createResult");
      if (!root) return;
      if (!state.createResult) {
        root.classList.add("hidden");
        root.innerHTML = "";
        return;
      }
      const item = state.createResult;
      root.classList.remove("hidden");
      root.className = "panel";
      root.innerHTML = '' +
        '<div class="detail">' +
          '<div class="item-title"><strong>任务已创建并开始分发</strong>' + badge("created") + '</div>' +
          '<div class="item-meta">' +
            '<span>run: <code>' + esc(item.run.run_id) + '</code></span>' +
            '<span>template: <code>' + esc(item.template.template_id) + '</code></span>' +
            '<span>batches: ' + (item.batches || []).length + '</span>' +
          '</div>' +
          '<div class="actions" style="margin-top:10px">' +
            '<button class="primary" type="button" id="openCreatedTaskBtn">查看这个 task</button>' +
          '</div>' +
        '</div>';
      const btn = document.getElementById("openCreatedTaskBtn");
      if (btn) {
        btn.addEventListener("click", async () => {
          state.selectedTaskId = item.run.run_id;
          state.createResult = null;
          setTab("tasks");
          await loadDashboard();
        });
      }
    }

    function closePreviewModal() {
      state.filePreview = null;
      document.getElementById("previewModal").classList.add("hidden");
    }

    function renderWorkerList() {
      const el = document.getElementById("workerList");
      if (!state.workers.length) {
        el.innerHTML = '<div class="empty">暂无 worker</div>';
        return;
      }
      el.innerHTML = state.workers.map(worker => {
        const active = worker.worker_id === state.selectedWorkerId ? " active" : "";
        const runtime = runtimeForWorker(worker.worker_id) || {};
        const current = runtime.currentBatch;
        return '<button class="item' + active + '" data-worker-id="' + esc(worker.worker_id) + '">' +
          '<div class="item-title"><strong>' + esc(worker.display_name) + '</strong>' + badge(worker.status) + '</div>' +
          '<div class="item-meta">' +
            '<span>host: <code>' + esc(worker.host) + '</code></span>' +
            '<span>manual: ' + badge(worker.manualStatus) + '</span>' +
            '<span>slots: ' + worker.slots_used + '/' + worker.slots_total + '</span>' +
            '<span>running: ' + esc(runtime.runningCount ?? 0) + '</span>' +
            '<span>queued: ' + esc(runtime.queuedCount ?? 0) + '</span>' +
          '</div>' +
          (current ? '<div class="item-meta" style="margin-top:6px"><span>current: <code>' + esc(current.runName || current.runId || "-") + '</code></span></div>' : '') +
        '</button>';
      }).join("");
      el.querySelectorAll("[data-worker-id]").forEach(btn => {
        btn.addEventListener("click", () => {
          state.selectedWorkerId = btn.dataset.workerId;
          renderWorkerList();
          renderWorkerDetail();
        });
      });
    }

    function renderWorkerDetail() {
      const root = document.getElementById("workerDetail");
      const worker = state.workers.find(item => item.worker_id === state.selectedWorkerId);
      if (!worker) {
        root.innerHTML = '<div class="empty">选择左侧 worker 查看详情和配置项</div>';
        return;
      }
      document.getElementById("workerDetailTitle").textContent = worker.display_name;
      const runtime = runtimeForWorker(worker.worker_id) || {};
      const runningRows = (runtime.runningBatches || []).map(runtimeBatchRow).join("") || '<div class="empty">当前没有运行中的 batch</div>';
      const queuedRows = (runtime.queuedBatches || []).map(runtimeBatchRow).join("") || '<div class="empty">当前没有排队 batch</div>';
      root.innerHTML =
        '<div class="detail-grid">' +
          '<div class="stat"><div class="subtle">Worker ID</div><strong><code>' + esc(worker.worker_id) + '</code></strong></div>' +
          '<div class="stat"><div class="subtle">Host</div><strong class="subtle">' + esc(worker.host) + '</strong></div>' +
          '<div class="stat"><div class="subtle">Status</div><strong>' + badge(worker.status) + '</strong></div>' +
          '<div class="stat"><div class="subtle">Heartbeat</div><strong class="subtle">' + esc(worker.last_heartbeat_at || "-") + '</strong></div>' +
          '<div class="stat"><div class="subtle">Running</div><strong>' + esc(runtime.runningCount ?? 0) + '</strong></div>' +
          '<div class="stat"><div class="subtle">Queued</div><strong>' + esc(runtime.queuedCount ?? 0) + '</strong></div>' +
          '<div class="stat"><div class="subtle">Available Slots</div><strong>' + esc(runtime.availableSlots ?? Math.max(0, worker.slots_total - worker.slots_used)) + '</strong></div>' +
        '</div>' +
        '<div style="margin-bottom:18px">' +
          '<h3 style="margin-bottom:8px">运行状态</h3>' +
          '<div class="detail-grid" style="grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));align-items:start">' +
            '<div><div class="subtle" style="margin-bottom:8px">Running</div>' + runningRows + '</div>' +
            '<div><div class="subtle" style="margin-bottom:8px">Queued</div>' + queuedRows + '</div>' +
          '</div>' +
        '</div>' +
        '<form id="workerForm">' +
          '<div class="field"><label>显示名称</label><input name="displayName" value="' + esc(worker.display_name) + '" /></div>' +
          '<div class="field"><label>Slots</label><input name="slotsTotal" type="number" min="1" value="' + esc(worker.slots_total) + '" /></div>' +
          '<div class="field"><label>标签（逗号分隔）</label><input name="tags" value="' + esc((worker.tags || []).join(", ")) + '" /></div>' +
          '<div class="field"><label>备注</label><textarea name="note">' + esc(worker.note || "") + '</textarea></div>' +
          '<div class="actions">' +
            '<button class="primary" type="submit">保存配置</button>' +
            '<button class="ghost" type="button" id="toggleEnabledBtn">' + (worker.enabled ? "设为禁用" : "设为启用") + '</button>' +
          '</div>' +
        '</form>' +
        '<div style="margin-top:16px"><h3 style="margin-bottom:8px">Capabilities</h3><pre>' + esc(JSON.stringify(worker.capabilities, null, 2)) + '</pre></div>';

      root.querySelector("#workerForm").addEventListener("submit", async (event) => {
        event.preventDefault();
        const form = new FormData(event.target);
        await api("/api/workers/" + encodeURIComponent(worker.worker_id) + "/settings", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            displayName: form.get("displayName"),
            slotsTotal: Number(form.get("slotsTotal")),
            tags: String(form.get("tags") || "").split(",").map(s => s.trim()).filter(Boolean),
            note: form.get("note"),
          }),
        });
        await loadDashboard();
      });

      root.querySelector("#toggleEnabledBtn").addEventListener("click", async () => {
        await api("/api/workers/" + encodeURIComponent(worker.worker_id) + "/settings", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({ enabled: !worker.enabled }),
        });
        await loadDashboard();
      });
    }

    function collectCreateFormPayload(form) {
      const data = new FormData(form);
      const workerIds = data.getAll("workerIds").map(value => String(value));
      const selectedCaseIds = String(data.get("selectedCaseIds") || "")
        .split(/[\\n,]/)
        .map(item => item.trim())
        .filter(Boolean);
      const concurrency = Number(data.get("nConcurrent") || 1);
      return {
        name: String(data.get("name") || "").trim(),
        datasetRef: String(data.get("datasetRef") || "").trim(),
        jobsDir: String(data.get("jobsDir") || "/root/projects/harbor/jobs").trim(),
        workerIds,
        selectedCaseIds,
        batchOptions: {
          concurrency,
        },
        executorConfig: {
          agentName: "bitfun-cli",
          nConcurrent: concurrency,
          timeoutMultiplier: parsePositiveNumber(data.get("timeoutMultiplier"), 1.0),
          agentTimeoutMultiplier: parsePositiveNumber(data.get("agentTimeoutMultiplier"), 3.0),
          verifierTimeoutMultiplier: parsePositiveNumber(data.get("verifierTimeoutMultiplier"), 2.0),
          environmentBuildTimeoutMultiplier: parsePositiveNumber(
            data.get("environmentBuildTimeoutMultiplier"),
            1.5,
          ),
        },
      };
    }

    async function submitCreateTaskForm(event) {
      event.preventDefault();
      const payload = collectCreateFormPayload(event.target);
      if (!payload.workerIds.length) {
        alert("至少选择一个 worker。");
        return;
      }
      const result = await api("/api/eval-tasks/create-and-distribute", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
      });
      state.createResult = result;
      renderCreateResult();
      await loadDashboard();
    }

    document.querySelectorAll(".tab").forEach(btn => {
      btn.addEventListener("click", () => setTab(btn.dataset.tab));
    });
    document.getElementById("taskSearch").addEventListener("input", (event) => {
      state.filters.search = event.target.value;
      renderTaskList();
    });
    document.getElementById("taskStatusFilter").addEventListener("input", (event) => {
      state.filters.status = event.target.value;
      renderTaskList();
    });
    document.getElementById("taskWorkerFilter").addEventListener("input", (event) => {
      state.filters.worker = event.target.value;
      renderTaskList();
    });
    document.getElementById("createTaskForm").addEventListener("submit", submitCreateTaskForm);
    document.getElementById("previewModalClose").addEventListener("click", closePreviewModal);
    document.getElementById("previewModal").addEventListener("click", (event) => {
      if (event.target.id === "previewModal") {
        closePreviewModal();
      }
    });

    setTab(window.location.pathname === "/create" ? "create" : "tasks");
    loadDashboard();
    setInterval(loadDashboard, 5000);
  </script>
</body>
</html>
"""
