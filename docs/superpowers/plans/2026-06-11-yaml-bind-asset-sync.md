# YAML Bind Asset Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generalize YAML-first distributed task creation so every controller-local bind mount is validated, synced to each worker, and rewritten anywhere it is referenced in generated worker YAML.

**Architecture:** Add generic bind asset discovery and path rewriting to `controller/harbor_yaml.py`, store bind asset metadata in the existing sync manifest, and extend `AssetSyncer` to copy file/directory assets into `<worker sharedRoot>/sync/<run>/assets/`. Dataset sharding remains unchanged; `harborYamlByBatchId` remains the authoritative YAML-first execution config.

**Tech Stack:** Python 3.14, stdlib `pathlib`/`shutil`/`os`, PyYAML, existing `SshRunner`, pytest.

---

## File Structure

- Modify `src/agent_eval_orchestrator/controller/harbor_yaml.py`
  - Owns Harbor YAML parsing, bind asset discovery, stable asset names, and recursive YAML string rewriting.
  - Add dataclasses/functions here so YAML-specific behavior remains close to existing YAML code.

- Modify `src/agent_eval_orchestrator/controller/asset_syncer.py`
  - Owns sync manifest shape and copying assets locally/remotely.
  - Replace bitfun-specific sync functions with generic bind asset helpers while keeping existing dataset sync behavior.

- Modify `src/agent_eval_orchestrator/controller/server.py`
  - Wires YAML asset planning into `_create_yaml_eval_task`.
  - Removes YAML-first bitfun-specific validation path.

- Modify `src/agent_eval_orchestrator/executors/harbor.py`
  - Generalizes mount validation so any local bind source used by executor fallback config must exist.

- Modify `tests/controller/test_create_task_sync_api.py`
  - Covers API-level YAML planning and worker YAML rewrite behavior.

- Modify `tests/controller/test_asset_syncer.py`
  - Covers manifest shape and local/remote generic asset copying.

- Modify `tests/executors/test_harbor_executor.py`
  - Covers executor fallback mount validation.

---

### Task 1: Add Bind Asset Discovery and Recursive Rewrite Tests

**Files:**
- Modify: `tests/controller/test_create_task_sync_api.py`
- Modify: `src/agent_eval_orchestrator/controller/harbor_yaml.py`

- [ ] **Step 1: Write failing API tests for file bind asset rewrite**

Add this test after `test_create_task_yaml_first_syncs_dataset_and_rewrites_batch_yaml` in `tests/controller/test_create_task_sync_api.py`:

```python
def test_create_task_yaml_first_syncs_bind_file_and_rewrites_all_references(store, tmp_path, monkeypatch):
    dataset = tmp_path / "tasks"
    harbor_repo = tmp_path / "harbor"
    monkeypatch.setattr(controller_server, "resolve_controller_viewer_harbor_repo", lambda: harbor_repo)
    case_dir = dataset / "alpha"
    case_dir.mkdir(parents=True)
    (case_dir / "task.toml").write_text("", encoding="utf-8")
    codeagent = tmp_path / "codeagentcli"
    codeagent.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(codeagent, 0o755)
    store.register_worker(
        worker_id="local-a",
        display_name="local-a",
        host="localhost",
        slots_total=1,
        slots_used=0,
        capabilities={"sharedRoot": str(tmp_path / "runtime-a"), "localToController": True},
    )
    server = start_test_server(store, tmp_path, 9891)
    conn = HTTPConnection("127.0.0.1", 9891)
    body = json.dumps(
        {
            "harborYaml": f"""
agents:
  - name: codeagent
    kwargs:
      install_mode: binary
      binary_path: {codeagent}
datasets:
  - path: {dataset}
    task_names:
      - alpha
environment:
  type: docker
  mounts:
    - type: bind
      source: {codeagent}
      target: /usr/local/bin/codeagentcli
      read_only: true
""",
            "workerIds": ["local-a"],
        }
    )
    conn.request(
        "POST",
        "/api/eval-tasks/create-and-distribute",
        body=body,
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    payload = json.loads(resp.read().decode("utf-8"))
    server.shutdown()

    assert resp.status == 201
    manifest = payload["run"]["sync_manifest"]
    assert manifest["bindAssets"] == [
        {
            "source": str(codeagent.resolve()),
            "kind": "file",
            "targetName": "codeagentcli",
        }
    ]
    batch = payload["batches"][0]
    batch_yaml = yaml.safe_load(payload["template"]["executor_config"]["harborYamlByBatchId"][batch["batch_id"]])
    target = str(tmp_path / "runtime-a" / "sync" / payload["run"]["run_id"] / "assets" / "codeagentcli")
    assert batch_yaml["environment"]["mounts"][0]["source"] == target
    assert batch_yaml["agents"][0]["kwargs"]["binary_path"] == target
```

- [ ] **Step 2: Write failing API tests for directory child-path rewrite and longest-prefix matching**

Add this test after the previous one:

```python
def test_create_task_yaml_first_rewrites_bind_directory_child_paths_with_longest_prefix(store, tmp_path, monkeypatch):
    dataset = tmp_path / "tasks"
    harbor_repo = tmp_path / "harbor"
    monkeypatch.setattr(controller_server, "resolve_controller_viewer_harbor_repo", lambda: harbor_repo)
    case_dir = dataset / "alpha"
    case_dir.mkdir(parents=True)
    (case_dir / "task.toml").write_text("", encoding="utf-8")
    tools_dir = tmp_path / "tools"
    nested_dir = tools_dir / "codeagent"
    nested_dir.mkdir(parents=True)
    (nested_dir / "codeagentcli").write_text("#!/bin/sh\n", encoding="utf-8")
    store.register_worker(
        worker_id="local-a",
        display_name="local-a",
        host="localhost",
        slots_total=1,
        slots_used=0,
        capabilities={"sharedRoot": str(tmp_path / "runtime-a"), "localToController": True},
    )
    server = start_test_server(store, tmp_path, 9892)
    conn = HTTPConnection("127.0.0.1", 9892)
    body = json.dumps(
        {
            "harborYaml": f"""
agents:
  - name: codeagent
    kwargs:
      install_mode: binary
      binary_path: {nested_dir / "codeagentcli"}
      untouched_text: prefix:{nested_dir / "codeagentcli"}
datasets:
  - path: {dataset}
    task_names:
      - alpha
environment:
  type: docker
  mounts:
    - type: bind
      source: {tools_dir}
      target: /opt/tools
      read_only: true
    - type: bind
      source: {nested_dir}
      target: /opt/codeagent
      read_only: true
""",
            "workerIds": ["local-a"],
        }
    )
    conn.request(
        "POST",
        "/api/eval-tasks/create-and-distribute",
        body=body,
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    payload = json.loads(resp.read().decode("utf-8"))
    server.shutdown()

    assert resp.status == 201
    batch = payload["batches"][0]
    batch_yaml = yaml.safe_load(payload["template"]["executor_config"]["harborYamlByBatchId"][batch["batch_id"]])
    root = tmp_path / "runtime-a" / "sync" / payload["run"]["run_id"] / "assets"
    assert batch_yaml["environment"]["mounts"][0]["source"] == str(root / "tools")
    assert batch_yaml["environment"]["mounts"][1]["source"] == str(root / "codeagent")
    assert batch_yaml["agents"][0]["kwargs"]["binary_path"] == str(root / "codeagent" / "codeagentcli")
    assert batch_yaml["agents"][0]["kwargs"]["untouched_text"].startswith("prefix:")
```

- [ ] **Step 3: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/controller/test_create_task_sync_api.py::test_create_task_yaml_first_syncs_bind_file_and_rewrites_all_references tests/controller/test_create_task_sync_api.py::test_create_task_yaml_first_rewrites_bind_directory_child_paths_with_longest_prefix -q
```

Expected: both tests fail because `bindAssets` and generic YAML rewrite do not exist yet.

- [ ] **Step 4: Implement bind asset dataclass and discovery helpers**

In `src/agent_eval_orchestrator/controller/harbor_yaml.py`, add `BindAsset` after `HarborYamlPlan`:

```python
@dataclass(frozen=True)
class BindAsset:
    source: str
    kind: str
    target_name: str
```

Then add these helpers before `extract_bitfun_mount_paths`:

```python
def discover_bind_assets(config: dict[str, Any]) -> list[BindAsset]:
    assets: list[BindAsset] = []
    seen: set[str] = set()
    used_names: set[str] = set()
    for index, mount in enumerate(_environment_mounts(config)):
        mount_type = str(mount.get("type") or "").strip()
        if mount_type != "bind":
            continue
        raw_source = str(mount.get("source") or "").strip()
        if not raw_source:
            raise HarborYamlError(f"environment.mounts[{index}].source is required for bind mounts")
        source = Path(raw_source).expanduser()
        if not source.is_absolute():
            raise HarborYamlError(f"environment.mounts[{index}].source must be an absolute path: {raw_source}")
        resolved = source.resolve()
        if not resolved.exists():
            raise HarborYamlError(f"environment.mounts[{index}].source not found on controller: {resolved}")
        if resolved.is_file():
            kind = "file"
        elif resolved.is_dir():
            kind = "directory"
        else:
            raise HarborYamlError(f"environment.mounts[{index}].source must be a file or directory: {resolved}")
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        assets.append(
            BindAsset(
                source=key,
                kind=kind,
                target_name=_stable_asset_name(resolved, used_names),
            )
        )
    return assets


def _stable_asset_name(source: Path, used_names: set[str]) -> str:
    base = sanitize_name(source.name or "asset")
    if not base:
        base = "asset"
    name = base
    suffix = 2
    while name in used_names:
        name = f"{base}-{suffix}"
        suffix += 1
    used_names.add(name)
    return name
```

- [ ] **Step 5: Implement recursive rewrite helpers**

In `src/agent_eval_orchestrator/controller/harbor_yaml.py`, add:

```python
def build_worker_rewrite_map(
    *,
    dataset_path: str,
    worker_dataset_path: str,
    bind_assets: list[BindAsset],
    worker_sync_root: str,
) -> dict[str, str]:
    mapping = {str(Path(dataset_path).expanduser().resolve()): str(Path(worker_dataset_path))}
    asset_root = Path(worker_sync_root) / "assets"
    for asset in bind_assets:
        mapping[asset.source] = str(asset_root / asset.target_name)
    return mapping


def rewrite_yaml_paths(value: Any, rewrite_map: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: rewrite_yaml_paths(item, rewrite_map) for key, item in value.items()}
    if isinstance(value, list):
        return [rewrite_yaml_paths(item, rewrite_map) for item in value]
    if isinstance(value, str):
        return _rewrite_path_string(value, rewrite_map)
    return value


def _rewrite_path_string(value: str, rewrite_map: dict[str, str]) -> str:
    if not value.startswith("/"):
        return value
    ordered = sorted(rewrite_map.items(), key=lambda item: len(item[0]), reverse=True)
    for source, target in ordered:
        if value == source:
            return target
        if value.startswith(f"{source}/"):
            return f"{target}/{value[len(source) + 1:]}"
    return value
```

- [ ] **Step 6: Thread bind assets through batch YAML generation**

Change `build_batch_harbor_yaml` signature in `src/agent_eval_orchestrator/controller/harbor_yaml.py`:

```python
def build_batch_harbor_yaml(
    plan: HarborYamlPlan,
    *,
    batch_id: str,
    selected_task_ids: list[str],
    jobs_dir: str,
    worker_dataset_path: str | None = None,
    worker_sync_root: str | None = None,
    bind_assets: list[BindAsset] | None = None,
) -> str:
```

Replace the final bitfun rewrite block:

```python
    if worker_sync_root:
        _rewrite_bitfun_mounts(payload, worker_sync_root=worker_sync_root)
```

with:

```python
    if worker_sync_root and worker_dataset_path:
        rewrite_map = build_worker_rewrite_map(
            dataset_path=plan.dataset_ref,
            worker_dataset_path=worker_dataset_path,
            bind_assets=bind_assets or [],
            worker_sync_root=worker_sync_root,
        )
        payload = rewrite_yaml_paths(payload, rewrite_map)
```

Leave `extract_bitfun_mount_paths` and `_rewrite_bitfun_mounts` in place for now; Task 4 removes the YAML-first use.

- [ ] **Step 7: Run tests and verify they still fail at API wiring**

Run:

```bash
uv run pytest tests/controller/test_create_task_sync_api.py::test_create_task_yaml_first_syncs_bind_file_and_rewrites_all_references tests/controller/test_create_task_sync_api.py::test_create_task_yaml_first_rewrites_bind_directory_child_paths_with_longest_prefix -q
```

Expected: failures remain because `server.py` does not call `discover_bind_assets` or pass `bind_assets` yet.

---

### Task 2: Wire Bind Assets Into YAML-First API and Manifest

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/server.py`
- Modify: `src/agent_eval_orchestrator/controller/asset_syncer.py`
- Modify: `tests/controller/test_create_task_sync_api.py`

- [ ] **Step 1: Replace bitfun-specific API test with generic bind behavior**

In `tests/controller/test_create_task_sync_api.py`, rename `test_create_task_yaml_first_syncs_bitfun_mounts_and_rewrites_worker_yaml` to:

```python
def test_create_task_yaml_first_syncs_bind_mounts_and_rewrites_worker_yaml(store, tmp_path, monkeypatch):
```

In that test, change the manifest assertions from:

```python
    assert manifest["bitfunCliPath"] == str(bitfun_cli.resolve())
    assert manifest["bitfunConfigDir"] == str(bitfun_config.resolve())
```

to:

```python
    assert manifest["bindAssets"] == [
        {"source": str(bitfun_cli.resolve()), "kind": "file", "targetName": "bitfun-cli"},
        {"source": str(bitfun_config.resolve()), "kind": "directory", "targetName": "bitfun-config"},
    ]
```

Change expected mount paths from the old `/bitfun/` sync directory to the new `/assets/` sync directory:

```python
    assert mounts[0]["source"] == str(tmp_path / "runtime-a" / "sync" / payload["run"]["run_id"] / "assets" / "bitfun-cli")
    assert mounts[1]["source"] == str(tmp_path / "runtime-a" / "sync" / payload["run"]["run_id"] / "assets" / "bitfun-config")
    assert mounts[1]["target"] == "/root/.config/bitfun"
```

- [ ] **Step 2: Add validation tests for missing and relative bind source**

Add these tests near the other YAML-first rejection tests:

```python
def test_create_task_yaml_first_rejects_missing_bind_source(store, tmp_path, monkeypatch):
    dataset = tmp_path / "tasks"
    harbor_repo = tmp_path / "harbor"
    monkeypatch.setattr(controller_server, "resolve_controller_viewer_harbor_repo", lambda: harbor_repo)
    case_dir = dataset / "alpha"
    case_dir.mkdir(parents=True)
    (case_dir / "task.toml").write_text("", encoding="utf-8")
    missing = tmp_path / "missing-binary"
    store.register_worker(
        worker_id="local-a",
        display_name="local-a",
        host="localhost",
        slots_total=1,
        slots_used=0,
        capabilities={"sharedRoot": str(tmp_path / "runtime-a"), "localToController": True},
    )
    server = start_test_server(store, tmp_path, 9893)
    conn = HTTPConnection("127.0.0.1", 9893)
    body = json.dumps(
        {
            "harborYaml": f"""
agents:
  - name: codeagent
datasets:
  - path: {dataset}
    task_names:
      - alpha
environment:
  mounts:
    - type: bind
      source: {missing}
      target: /usr/local/bin/missing
""",
            "workerIds": ["local-a"],
        }
    )
    conn.request(
        "POST",
        "/api/eval-tasks/create-and-distribute",
        body=body,
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    payload = json.loads(resp.read().decode("utf-8"))
    server.shutdown()

    assert resp.status == 400
    assert "environment.mounts[0].source not found on controller" in payload["error"]
    assert store.list_runs() == []


def test_create_task_yaml_first_rejects_relative_bind_source(store, tmp_path, monkeypatch):
    dataset = tmp_path / "tasks"
    harbor_repo = tmp_path / "harbor"
    monkeypatch.setattr(controller_server, "resolve_controller_viewer_harbor_repo", lambda: harbor_repo)
    case_dir = dataset / "alpha"
    case_dir.mkdir(parents=True)
    (case_dir / "task.toml").write_text("", encoding="utf-8")
    store.register_worker(
        worker_id="local-a",
        display_name="local-a",
        host="localhost",
        slots_total=1,
        slots_used=0,
        capabilities={"sharedRoot": str(tmp_path / "runtime-a"), "localToController": True},
    )
    server = start_test_server(store, tmp_path, 9894)
    conn = HTTPConnection("127.0.0.1", 9894)
    body = json.dumps(
        {
            "harborYaml": f"""
agents:
  - name: codeagent
datasets:
  - path: {dataset}
    task_names:
      - alpha
environment:
  mounts:
    - type: bind
      source: relative/codeagentcli
      target: /usr/local/bin/codeagentcli
""",
            "workerIds": ["local-a"],
        }
    )
    conn.request(
        "POST",
        "/api/eval-tasks/create-and-distribute",
        body=body,
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    payload = json.loads(resp.read().decode("utf-8"))
    server.shutdown()

    assert resp.status == 400
    assert "environment.mounts[0].source must be an absolute path" in payload["error"]
    assert store.list_runs() == []
```

- [ ] **Step 3: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/controller/test_create_task_sync_api.py::test_create_task_yaml_first_syncs_bind_mounts_and_rewrites_worker_yaml tests/controller/test_create_task_sync_api.py::test_create_task_yaml_first_rejects_missing_bind_source tests/controller/test_create_task_sync_api.py::test_create_task_yaml_first_rejects_relative_bind_source -q
```

Expected: failures because the API still emits bitfun manifest keys and does not validate generic bind sources.

- [ ] **Step 4: Add bind assets to manifest builder**

In `src/agent_eval_orchestrator/controller/asset_syncer.py`, import `BindAsset` only for typing:

```python
from agent_eval_orchestrator.controller.harbor_yaml import BindAsset
```

Change `build_sync_manifest` signature:

```python
def build_sync_manifest(
    *,
    run_id: str,
    dataset_path: Path,
    worker_shards: dict[str, list[str]],
    workers_by_id: dict[str, dict[str, Any]],
    controller_shared_root: Path,
    bitfun_cli_path: Path | None = None,
    bitfun_config_dir: Path | None = None,
    bind_assets: list[BindAsset] | None = None,
    task_sources: dict[str, str] | None = None,
) -> dict[str, Any]:
```

Before `return manifest`, add:

```python
    if bind_assets:
        manifest["bindAssets"] = [
            {"source": asset.source, "kind": asset.kind, "targetName": asset.target_name}
            for asset in bind_assets
        ]
```

Keep `bitfun_*` parameters temporarily for legacy non-YAML tests; they are removed or left unused after generic sync tests pass.

- [ ] **Step 5: Wire bind assets in server.py**

In `src/agent_eval_orchestrator/controller/server.py`, change the Harbor YAML import block:

```python
from agent_eval_orchestrator.controller.harbor_yaml import (
    HarborYamlError,
    build_batch_harbor_yaml,
    discover_bind_assets,
    parse_harbor_yaml,
)
```

Remove `extract_bitfun_mount_paths` from the import.

In `_create_yaml_eval_task`, delete the block that calls `extract_bitfun_mount_paths` and validates `bitfun_cli_path`/`bitfun_config_dir`.

Immediately after the existing `validate_dataset_assets` call in `_create_yaml_eval_task`, add:

```python
            bind_assets = discover_bind_assets(plan.original_config)
```

Change the existing `build_sync_manifest` call to pass this additional keyword argument:

```python
                bind_assets=bind_assets,
```

and remove the `bitfun_cli_path=bitfun_cli_path` and `bitfun_config_dir=bitfun_config_dir` keyword arguments from that call.

Change the existing `build_batch_harbor_yaml` call to pass this additional keyword argument:

```python
                    bind_assets=bind_assets,
```

Change asset sync steps creation from:

```python
                steps=initial_worker_steps(
                    worker_ids,
                    include_bitfun=bitfun_cli_path is not None and bitfun_config_dir is not None,
                ),
```

to:

```python
                steps=initial_worker_steps(
                    worker_ids,
                    include_assets=bool(bind_assets),
                ),
```

Task 3 updates `initial_worker_steps` to accept `include_assets`.

- [ ] **Step 6: Run tests and observe expected signature failure**

Run:

```bash
uv run pytest tests/controller/test_create_task_sync_api.py::test_create_task_yaml_first_syncs_bind_file_and_rewrites_all_references -q
```

Expected: failure about `initial_worker_steps()` not accepting `include_assets`.

---

### Task 3: Implement Generic Asset Sync Steps and Copy Helpers

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/asset_syncer.py`
- Modify: `tests/controller/test_asset_syncer.py`
- Modify: `tests/controller/test_create_task_sync_api.py`

- [ ] **Step 1: Add local sync tests for generic file and directory assets**

In `tests/controller/test_asset_syncer.py`, add imports:

```python
    sync_bind_asset_local,
```

Add tests after `test_sync_bitfun_local_preserves_executable`:

```python
def test_sync_bind_asset_local_copies_file_and_preserves_executable(tmp_path):
    source = tmp_path / "codeagentcli"
    source.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(source, 0o755)
    target = tmp_path / "target" / "assets" / "codeagentcli"

    sync_bind_asset_local(source_path=source, kind="file", target_path=target)

    assert target.read_text(encoding="utf-8") == "#!/bin/sh\n"
    assert os.access(target, os.X_OK)


def test_sync_bind_asset_local_copies_directory(tmp_path):
    source = tmp_path / "config"
    source.mkdir()
    (source / "settings.json").write_text("{}", encoding="utf-8")
    target = tmp_path / "target" / "assets" / "config"

    sync_bind_asset_local(source_path=source, kind="directory", target_path=target)

    assert (target / "settings.json").read_text(encoding="utf-8") == "{}"
```

- [ ] **Step 2: Add remote sync tests for generic file and directory assets**

In `tests/controller/test_asset_syncer.py`, add imports:

```python
    sync_bind_asset_remote,
```

Add tests after `test_sync_cases_remote_uses_rsync`:

```python
def test_sync_bind_asset_remote_uses_scp_for_file(tmp_path):
    source = tmp_path / "codeagentcli"
    source.write_text("#!/bin/sh\n", encoding="utf-8")
    runner = MagicMock()

    sync_bind_asset_remote(
        ssh=runner,
        host_alias="aeo-ecs-0004",
        source_path=source,
        kind="file",
        target_path="/tmp/sync/run-1/assets/codeagentcli",
    )

    runner.remote_mkdir_p.assert_called_once_with("aeo-ecs-0004", "/tmp/sync/run-1/assets")
    runner.scp_file.assert_called_once_with(source, "aeo-ecs-0004:/tmp/sync/run-1/assets/codeagentcli")
    runner.rsync_dir.assert_not_called()


def test_sync_bind_asset_remote_uses_rsync_for_directory(tmp_path):
    source = tmp_path / "config"
    source.mkdir()
    runner = MagicMock()

    sync_bind_asset_remote(
        ssh=runner,
        host_alias="aeo-ecs-0004",
        source_path=source,
        kind="directory",
        target_path="/tmp/sync/run-1/assets/config",
    )

    runner.remote_mkdir_p.assert_called_once_with("aeo-ecs-0004", "/tmp/sync/run-1/assets")
    runner.rsync_dir.assert_called_once_with(source, "aeo-ecs-0004:/tmp/sync/run-1/assets/config/", remote=True)
    runner.scp_file.assert_not_called()
```

- [ ] **Step 3: Update step tests to generic assets**

In `tests/controller/test_asset_syncer.py`, update `test_initial_worker_steps_and_status`:

```python
def test_initial_worker_steps_and_status():
    steps = initial_worker_steps(["worker-a", "worker-b"], include_assets=True)
    assert len(steps) == 2
    assert [step["id"] for step in steps[0]["steps"]] == ["sync_cases", "sync_assets"]
    updated = set_worker_step_status(steps, "worker-a", "sync_cases", "running")
    worker_a = next(item for item in updated if item["workerId"] == "worker-a")
    assert worker_a["steps"][0]["status"] == "running"
```

- [ ] **Step 4: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/controller/test_asset_syncer.py::test_sync_bind_asset_local_copies_file_and_preserves_executable tests/controller/test_asset_syncer.py::test_sync_bind_asset_local_copies_directory tests/controller/test_asset_syncer.py::test_sync_bind_asset_remote_uses_scp_for_file tests/controller/test_asset_syncer.py::test_sync_bind_asset_remote_uses_rsync_for_directory tests/controller/test_asset_syncer.py::test_initial_worker_steps_and_status -q
```

Expected: import/signature failures because helpers and `include_assets` do not exist.

- [ ] **Step 5: Implement generic step labels**

In `src/agent_eval_orchestrator/controller/asset_syncer.py`, change:

```python
SYNC_STEP_LABELS = {
    "sync_cases": "同步 dataset case",
    "sync_bitfun": "同步 bitfun-cli",
}
```

to:

```python
SYNC_STEP_LABELS = {
    "sync_cases": "同步 dataset case",
    "sync_assets": "同步 bind assets",
}
```

Replace `initial_worker_steps` with:

```python
def initial_worker_steps(
    worker_ids: list[str],
    *,
    include_assets: bool = False,
    include_bitfun: bool | None = None,
) -> list[dict[str, Any]]:
    if include_bitfun is not None:
        include_assets = include_bitfun
    step_defs = [{"id": "sync_cases", "label": SYNC_STEP_LABELS["sync_cases"], "status": "pending"}]
    if include_assets:
        step_defs.append({"id": "sync_assets", "label": SYNC_STEP_LABELS["sync_assets"], "status": "pending"})
    return [
        {
            "workerId": worker_id,
            "steps": [dict(step) for step in step_defs],
        }
        for worker_id in worker_ids
    ]
```

The `include_bitfun` compatibility argument avoids breaking older tests during this task; remove it only if all call sites are updated.

- [ ] **Step 6: Implement generic local and remote asset helpers**

In `src/agent_eval_orchestrator/controller/asset_syncer.py`, add after `sync_bitfun_remote`:

```python
def sync_bind_asset_local(*, source_path: Path, kind: str, target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        if target_path.is_dir():
            shutil.rmtree(target_path)
        else:
            target_path.unlink()
    if kind == "file":
        shutil.copy2(source_path, target_path)
        os.chmod(target_path, os.stat(source_path).st_mode)
        return
    if kind == "directory":
        shutil.copytree(source_path, target_path)
        return
    raise RuntimeError(f"unsupported bind asset kind: {kind}")


def sync_bind_asset_remote(
    *,
    ssh: SshRunner,
    host_alias: str,
    source_path: Path,
    kind: str,
    target_path: str,
) -> None:
    target = Path(target_path)
    ssh.remote_mkdir_p(host_alias, str(target.parent))
    if kind == "file":
        ssh.scp_file(source_path, f"{host_alias}:{target_path}")
        return
    if kind == "directory":
        ssh.rsync_dir(source_path, f"{host_alias}:{target_path}/", remote=True)
        return
    raise RuntimeError(f"unsupported bind asset kind: {kind}")
```

- [ ] **Step 7: Implement manifest asset target lookup**

In `src/agent_eval_orchestrator/controller/asset_syncer.py`, add:

```python
def worker_asset_paths(*, target_root: str, bind_assets: list[dict[str, Any]]) -> dict[str, str]:
    root = Path(target_root) / "assets"
    return {
        str(asset["source"]): str(root / str(asset["targetName"]))
        for asset in bind_assets
    }
```

- [ ] **Step 8: Update AssetSyncer.run_job to sync generic assets**

In `AssetSyncer.run_job`, replace:

```python
        include_bitfun = bool(str(manifest.get("bitfunCliPath") or "").strip()) and bool(
            str(manifest.get("bitfunConfigDir") or "").strip()
        )
        steps = initial_worker_steps(worker_ids, include_bitfun=include_bitfun)
```

with:

```python
        bind_assets = list(manifest.get("bindAssets") or [])
        include_assets = bool(bind_assets)
        steps = initial_worker_steps(worker_ids, include_assets=include_assets)
```

In `worker_thread`, replace the `include_bitfun` block:

```python
                    if include_bitfun:
                        steps = set_worker_step_status(steps, worker_id, "sync_bitfun", "running")
                        self.store.update_asset_sync_job(job_id, current_step=f"{worker_id}:sync_bitfun", steps=steps)
                if include_bitfun:
                    self._sync_bitfun(entry, manifest)
                    with lock:
                        steps = set_worker_step_status(steps, worker_id, "sync_bitfun", "succeeded")
                        self.store.update_asset_sync_job(job_id, steps=steps)
```

with:

```python
                    if include_assets:
                        steps = set_worker_step_status(steps, worker_id, "sync_assets", "running")
                        self.store.update_asset_sync_job(job_id, current_step=f"{worker_id}:sync_assets", steps=steps)
                if include_assets:
                    self._sync_bind_assets(entry, bind_assets)
                    with lock:
                        steps = set_worker_step_status(steps, worker_id, "sync_assets", "succeeded")
                        self.store.update_asset_sync_job(job_id, steps=steps)
```

In the exception block, replace `sync_bitfun` with `sync_assets`:

```python
                    if include_assets:
                        steps = set_worker_step_status(steps, worker_id, "sync_assets", "failed")
```

Immediately after the existing assignment that stores the return value of `worker_executor_paths` in `paths`, add:

```python
                asset_paths = worker_asset_paths(
                    target_root=str(entry["targetRoot"]),
                    bind_assets=bind_assets,
                )
```

Update the store config update payload:

```python
                    {
                        "datasetPathByWorker": {worker_id: paths["datasetPath"]},
                        "assetPathsByWorker": {worker_id: asset_paths},
                        "mountsByWorker": {worker_id: paths["mounts"]},
                    },
```

- [ ] **Step 9: Add AssetSyncer._sync_bind_assets**

In `AssetSyncer`, add this method after `_sync_bitfun`:

```python
    def _sync_bind_assets(self, entry: dict[str, Any], bind_assets: list[dict[str, Any]]) -> None:
        target_root = str(entry["targetRoot"])
        for asset in bind_assets:
            source_path = Path(str(asset["source"])).expanduser()
            kind = str(asset["kind"])
            target_path = str(Path(target_root) / "assets" / str(asset["targetName"]))
            if entry["transport"] == "local":
                sync_bind_asset_local(
                    source_path=source_path,
                    kind=kind,
                    target_path=Path(target_path),
                )
            else:
                sync_bind_asset_remote(
                    ssh=self.ssh,
                    host_alias=str(entry["sshHostAlias"]),
                    source_path=source_path,
                    kind=kind,
                    target_path=target_path,
                )
```

- [ ] **Step 10: Run asset sync tests**

Run:

```bash
uv run pytest tests/controller/test_asset_syncer.py -q
```

Expected: all tests in this file pass or only legacy bitfun validation tests fail if imports were changed too aggressively. Keep legacy bitfun helpers until later cleanup so current tests pass.

---

### Task 4: Complete API Wiring and Remove YAML-First Bitfun Special Case

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/server.py`
- Modify: `src/agent_eval_orchestrator/controller/harbor_yaml.py`
- Modify: `tests/controller/test_create_task_sync_api.py`

- [ ] **Step 1: Run combined API tests and capture failures**

Run:

```bash
uv run pytest tests/controller/test_create_task_sync_api.py -q
```

Expected: remaining failures point to API wiring, manifest shape, or old bitfun expectations.

- [ ] **Step 2: Finalize server.py imports and bind asset flow**

Ensure the import block in `src/agent_eval_orchestrator/controller/server.py` is exactly:

```python
from agent_eval_orchestrator.controller.harbor_yaml import (
    HarborYamlError,
    build_batch_harbor_yaml,
    discover_bind_assets,
    parse_harbor_yaml,
)
```

Ensure `_create_yaml_eval_task` calls:

```python
            bind_assets = discover_bind_assets(plan.original_config)
```

after the existing `validate_dataset_assets` call, and no longer calls `extract_bitfun_mount_paths`.

Ensure `build_sync_manifest` receives:

```python
                bind_assets=bind_assets,
```

Ensure `build_batch_harbor_yaml` receives:

```python
                    bind_assets=bind_assets,
```

Ensure `initial_worker_steps` receives:

```python
                    include_assets=bool(bind_assets),
```

- [ ] **Step 3: Remove bitfun-specific rewrite call from YAML path**

In `src/agent_eval_orchestrator/controller/harbor_yaml.py`, delete these constants if no remaining code uses them:

```python
BITFUN_CLI_TARGET = "/usr/local/bin/bitfun-cli"
BITFUN_CONFIG_TARGET = "/root/.config/bitfun"
BITFUN_CONFIG_DIR_TARGET = "/root/.config/bitfun/config"
```

Delete functions if unused:

```python
extract_bitfun_mount_paths
_rewrite_bitfun_mounts
```

Keep `_environment_mounts` and `_normalized_target` if `discover_bind_assets` uses them.

- [ ] **Step 4: Update old bitfun API test expectations**

Ensure `test_create_task_yaml_first_syncs_bind_mounts_and_rewrites_worker_yaml` expects:

```python
    assert mounts[0]["source"] == str(tmp_path / "runtime-a" / "sync" / payload["run"]["run_id"] / "assets" / "bitfun-cli")
    assert mounts[1]["source"] == str(tmp_path / "runtime-a" / "sync" / payload["run"]["run_id"] / "assets" / "bitfun-config")
    assert mounts[1]["target"] == "/root/.config/bitfun"
```

Do not expect `/root/.config/bitfun/config` target rewrite anymore; generic bind sync preserves user mount target.

- [ ] **Step 5: Run API tests**

Run:

```bash
uv run pytest tests/controller/test_create_task_sync_api.py -q
```

Expected: all tests in this file pass.

- [ ] **Step 6: Commit API and YAML planning changes**

Run:

```bash
git add src/agent_eval_orchestrator/controller/harbor_yaml.py src/agent_eval_orchestrator/controller/server.py src/agent_eval_orchestrator/controller/asset_syncer.py tests/controller/test_create_task_sync_api.py tests/controller/test_asset_syncer.py
git commit -m "Generalize YAML bind asset sync"
```

---

### Task 5: Generalize Executor Mount Validation

**Files:**
- Modify: `src/agent_eval_orchestrator/executors/harbor.py`
- Modify: `tests/executors/test_harbor_executor.py`

- [ ] **Step 1: Add failing test for any bind mount source that is missing**

In `tests/executors/test_harbor_executor.py`, add after `test_prepare_rejects_bitfun_cli_mount_source_that_is_not_a_file`:

```python
def test_prepare_rejects_missing_bind_mount_source(tmp_path: Path) -> None:
    batch_root = tmp_path / "batch-root"
    batch_root.mkdir()
    dataset = tmp_path / "dataset" / "case-a"
    dataset.mkdir(parents=True)
    (dataset / "task.toml").write_text("", encoding="utf-8")
    harbor_repo = tmp_path / "harbor"
    harbor_repo.mkdir()
    missing = tmp_path / "missing"

    with pytest.raises(
        RuntimeError,
        match=f"bind mount source must exist as a file or directory: {missing}",
    ):
        HarborExecutor().prepare(
            batch={
                "batch_id": "batch-test",
                "batch_root": str(batch_root),
                "selected_case_ids": ["case-a"],
            },
            run={},
            template={},
            dataset_ref=str(dataset.parent),
            executor_config={
                "agentName": "codeagent",
                "envType": "docker",
                "nConcurrent": 1,
                "mounts": [
                    {
                        "type": "bind",
                        "source": str(missing),
                        "target": "/usr/local/bin/codeagentcli",
                        "read_only": True,
                    },
                ],
                "harborRepoPath": str(harbor_repo),
            },
            local_root=tmp_path / "local",
            shared_root=None,
        )
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
uv run pytest tests/executors/test_harbor_executor.py::test_prepare_rejects_missing_bind_mount_source -q
```

Expected: fails because only bitfun target is validated.

- [ ] **Step 3: Replace bitfun-specific validator**

In `src/agent_eval_orchestrator/executors/harbor.py`, replace:

```python
BITFUN_CLI_CONTAINER_PATH = "/usr/local/bin/bitfun-cli"
```

with no constant, unless other code still uses it.

Replace `_validate_bitfun_cli_mount` with:

```python
def _validate_bind_mount_sources(mounts: Any) -> None:
    if not isinstance(mounts, list):
        return
    for mount in mounts:
        if not isinstance(mount, dict):
            continue
        if str(mount.get("type") or "").strip() != "bind":
            continue
        source = Path(str(mount.get("source") or "")).expanduser()
        if not source.exists() or not (source.is_file() or source.is_dir()):
            raise RuntimeError(f"bind mount source must exist as a file or directory: {source}")
```

In `HarborExecutor.prepare`, replace:

```python
            _validate_bitfun_cli_mount(mounts)
```

with:

```python
            _validate_bind_mount_sources(mounts)
```

- [ ] **Step 4: Replace bitfun-specific directory rejection test with generic directory allowance test**

In `tests/executors/test_harbor_executor.py`, replace the whole `test_prepare_rejects_bitfun_cli_mount_source_that_is_not_a_file` function with:

```python
def test_prepare_allows_bind_mount_source_that_is_directory(tmp_path: Path) -> None:
    batch_root = tmp_path / "batch-root"
    batch_root.mkdir()
    dataset = tmp_path / "dataset" / "case-a"
    dataset.mkdir(parents=True)
    (dataset / "task.toml").write_text("", encoding="utf-8")

    harbor_repo = tmp_path / "harbor"
    harbor_repo.mkdir()
    bind_dir = tmp_path / "bitfun-cli"
    bind_dir.mkdir()

    prepared = HarborExecutor().prepare(
        batch={
            "batch_id": "batch-test",
            "batch_root": str(batch_root),
            "selected_case_ids": ["case-a"],
        },
        run={},
        template={},
        dataset_ref=str(dataset.parent),
        executor_config={
            "agentName": "bitfun-cli",
            "envType": "docker",
            "nConcurrent": 1,
            "mounts": [
                {
                    "type": "bind",
                    "source": str(bind_dir),
                    "target": "/usr/local/bin/bitfun-cli",
                    "read_only": True,
                },
            ],
            "harborRepoPath": str(harbor_repo),
        },
        local_root=tmp_path / "local",
        shared_root=None,
    )
    shell = " ".join(shlex.quote(part) for part in prepared.command)
    assert str(bind_dir) in shell
```

- [ ] **Step 5: Run executor tests**

Run:

```bash
uv run pytest tests/executors/test_harbor_executor.py -q
```

Expected: all executor tests pass.

- [ ] **Step 6: Commit executor validation changes**

Run:

```bash
git add src/agent_eval_orchestrator/executors/harbor.py tests/executors/test_harbor_executor.py
git commit -m "Validate generic Harbor bind mount sources"
```

---

### Task 6: Full Regression and Cleanup

**Files:**
- Inspect/modify only if failures indicate stale references:
  - `src/agent_eval_orchestrator/controller/asset_syncer.py`
  - `src/agent_eval_orchestrator/controller/harbor_yaml.py`
  - `tests/controller/test_asset_syncer.py`
  - `tests/controller/test_create_task_sync_api.py`
  - `tests/executors/test_harbor_executor.py`

- [ ] **Step 1: Search for stale bitfun-only YAML sync references**

Run:

```bash
rg -n "extract_bitfun_mount_paths|sync_bitfun|bitfunCliPath|bitfunConfigDir|sync_bitfun|BITFUN_" src tests
```

Expected: remaining hits are either legacy non-YAML task support tests/helpers or intentionally retained fallback helpers. No YAML-first code path in `server.py` should call `extract_bitfun_mount_paths`.

- [ ] **Step 2: Run targeted regression suite**

Run:

```bash
uv run pytest tests/controller/test_create_task_sync_api.py tests/controller/test_asset_syncer.py tests/executors/test_harbor_executor.py -q
```

Expected: all targeted tests pass.

- [ ] **Step 3: Run broader controller/executor suite**

Run:

```bash
uv run pytest tests/controller tests/executors -q
```

Expected: all tests pass. If unrelated existing failures appear, record the failing test names and do not hide them.

- [ ] **Step 4: Inspect final diff**

Run:

```bash
git status --short
git diff --stat HEAD
```

Expected: only intended source/test files are modified; `frontend/` and `runtime-v2/` remain untracked and unstaged.

- [ ] **Step 5: Commit cleanup if any files changed after previous commits**

If Step 4 shows intended tracked changes, run:

```bash
git add src/agent_eval_orchestrator/controller/asset_syncer.py src/agent_eval_orchestrator/controller/harbor_yaml.py src/agent_eval_orchestrator/controller/server.py src/agent_eval_orchestrator/executors/harbor.py tests/controller/test_asset_syncer.py tests/controller/test_create_task_sync_api.py tests/executors/test_harbor_executor.py
git commit -m "Complete YAML bind asset sync regression cleanup"
```

If there are no tracked changes, skip this commit.

---

## Self-Review Notes

- Spec coverage:
  - Explicit bind source validation is covered in Task 2.
  - Dataset/task sharding remains covered by existing tests and targeted regression in Task 6.
  - Recursive path rewrite and longest-prefix behavior are covered in Task 1.
  - Local and remote file/directory sync are covered in Task 3.
  - Executor fallback validation is covered in Task 5.
  - Bitfun generic compatibility is covered by the renamed generic bind mount API test in Task 2.

- Scope:
  - This plan only changes YAML-first distributed task creation and executor fallback validation.
  - It does not change non-YAML create task semantics beyond retaining compatibility arguments in shared helpers.

- Placeholder scan:
  - No implementation steps contain TBD/TODO/fill-in placeholders.
  - All test additions include concrete code and exact commands.
