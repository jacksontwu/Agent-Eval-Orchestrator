from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_eval_orchestrator.core.defaults import (
    DEFAULT_AGENT_TIMEOUT_MULTIPLIER,
    DEFAULT_ENVIRONMENT_BUILD_TIMEOUT_MULTIPLIER,
    DEFAULT_ENVIRONMENT_DELETE,
    DEFAULT_ENVIRONMENT_FORCE_BUILD,
    DEFAULT_HARBOR_REPO,
    DEFAULT_MAX_RETRIES,
    DEFAULT_PER_WORKER_CONCURRENCY,
    DEFAULT_TIMEOUT_MULTIPLIER,
    DEFAULT_VERIFIER_TIMEOUT_MULTIPLIER,
)
from agent_eval_orchestrator.core.worker_paths import (
    build_harbor_bind_mounts,
    default_bitfun_config_dir,
    default_harbor_repo_from_shared_root,
    default_uv_binary_from_shared_root,
    repo_root_from_shared_root,
)


DEFAULT_AGENT_NAME = "bitfun-cli"


def _is_subpath(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _worker_shared_root(worker: dict[str, Any] | None) -> str:
    if not worker:
        return ""
    capabilities = worker.get("capabilities") if isinstance(worker.get("capabilities"), dict) else {}
    return str(capabilities.get("sharedRoot") or "").strip()


def _worker_repo_root(worker: dict[str, Any] | None) -> Path | None:
    shared_root = _worker_shared_root(worker)
    if not shared_root:
        return None
    return repo_root_from_shared_root(shared_root)


def _map_dataset_for_worker(dataset_ref: str, worker: dict[str, Any] | None) -> str:
    dataset_path = Path(dataset_ref).expanduser().resolve()
    repo_root = Path("/root/projects/agent-eval-orchestrator").resolve()
    worker_root = _worker_repo_root(worker)
    if worker_root and _is_subpath(dataset_path, repo_root):
        return str(worker_root / dataset_path.relative_to(repo_root))
    return str(dataset_path)


def _default_harbor_for_worker(worker_id: str, worker: dict[str, Any] | None) -> str:
    shared_root = _worker_shared_root(worker)
    if shared_root:
        harbor_path = default_harbor_repo_from_shared_root(shared_root)
        if harbor_path:
            return str(harbor_path)
    if worker_id == "remote-a":
        return "/home/wt/harbor"
    return str(DEFAULT_HARBOR_REPO)


def _default_uv_for_worker(worker_id: str, worker: dict[str, Any] | None) -> str:
    if worker_id == "local-a":
        return "/root/.local/bin/uv"
    shared_root = _worker_shared_root(worker)
    if shared_root:
        uv_path = default_uv_binary_from_shared_root(shared_root)
        if uv_path:
            return str(uv_path)
    if worker_id == "remote-a":
        return "/home/wt/.local/bin/uv"
    return "/root/.local/bin/uv"


def _default_bitfun_mounts(worker_id: str, worker: dict[str, Any] | None) -> list[dict[str, Any]]:
    shared_root = _worker_shared_root(worker)
    harbor_repo = _default_harbor_for_worker(worker_id, worker)
    uv_binary = _default_uv_for_worker(worker_id, worker)
    bitfun_config = default_bitfun_config_dir(worker_id=worker_id, shared_root=shared_root or None)
    return build_harbor_bind_mounts(
        uv_binary=uv_binary,
        harbor_repo=harbor_repo,
        bitfun_config_dir=bitfun_config,
    )


def build_executor_config(
    *,
    dataset_ref: str,
    worker_ids: list[str],
    workers: list[dict[str, Any]],
    body_config: dict[str, Any],
    jobs_dir: str,
) -> dict[str, Any]:
    workers_by_id = {str(worker["worker_id"]): worker for worker in workers}
    harbor_repo_by_worker: dict[str, str] = {}
    dataset_path_by_worker: dict[str, str] = {}
    uv_binary_by_worker: dict[str, str] = {}
    mounts_by_worker: dict[str, list[dict[str, Any]]] = {}
    for worker_id in worker_ids:
        worker = workers_by_id.get(worker_id)
        harbor_repo_by_worker[worker_id] = _default_harbor_for_worker(worker_id, worker)
        dataset_path_by_worker[worker_id] = _map_dataset_for_worker(dataset_ref, worker)
        uv_binary_by_worker[worker_id] = _default_uv_for_worker(worker_id, worker)
        mounts_by_worker[worker_id] = _default_bitfun_mounts(worker_id, worker)
    n_concurrent = int(body_config.get("nConcurrent") or DEFAULT_PER_WORKER_CONCURRENCY)
    timeout_multiplier = body_config.get("timeoutMultiplier")
    agent_timeout_multiplier = body_config.get("agentTimeoutMultiplier")
    verifier_timeout_multiplier = body_config.get("verifierTimeoutMultiplier")
    agent_setup_timeout_multiplier = body_config.get("agentSetupTimeoutMultiplier")
    environment_build_timeout_multiplier = body_config.get("environmentBuildTimeoutMultiplier")
    config = {
        "agentName": str(body_config.get("agentName") or DEFAULT_AGENT_NAME),
        "envType": str(body_config.get("envType") or "docker"),
        "nConcurrent": n_concurrent,
        "timeoutMultiplier": (
            float(timeout_multiplier) if timeout_multiplier not in (None, "") else DEFAULT_TIMEOUT_MULTIPLIER
        ),
        "agentTimeoutMultiplier": (
            float(agent_timeout_multiplier)
            if agent_timeout_multiplier not in (None, "")
            else DEFAULT_AGENT_TIMEOUT_MULTIPLIER
        ),
        "verifierTimeoutMultiplier": (
            float(verifier_timeout_multiplier)
            if verifier_timeout_multiplier not in (None, "")
            else DEFAULT_VERIFIER_TIMEOUT_MULTIPLIER
        ),
        "agentSetupTimeoutMultiplier": (
            float(agent_setup_timeout_multiplier) if agent_setup_timeout_multiplier not in (None, "") else None
        ),
        "environmentBuildTimeoutMultiplier": (
            float(environment_build_timeout_multiplier)
            if environment_build_timeout_multiplier not in (None, "")
            else DEFAULT_ENVIRONMENT_BUILD_TIMEOUT_MULTIPLIER
        ),
        "maxRetries": (
            int(body_config["maxRetries"])
            if body_config.get("maxRetries") not in (None, "")
            else DEFAULT_MAX_RETRIES
        ),
        "environmentForceBuild": (
            bool(body_config["environmentForceBuild"])
            if "environmentForceBuild" in body_config
            else DEFAULT_ENVIRONMENT_FORCE_BUILD
        ),
        "environmentDelete": (
            bool(body_config["environmentDelete"])
            if "environmentDelete" in body_config
            else DEFAULT_ENVIRONMENT_DELETE
        ),
        "harborRepoPathByWorker": harbor_repo_by_worker,
        "datasetPathByWorker": dataset_path_by_worker,
        "uvBinaryByWorker": uv_binary_by_worker,
        "mountsByWorker": mounts_by_worker,
        "collectJobs": True,
        "combinedJobsDir": jobs_dir,
    }
    for key in (
        "modelName",
        "modelNameByWorker",
        "agentKwargs",
        "agentKwargsByWorker",
        "agentEnv",
        "agentEnvByWorker",
        "processEnv",
        "processEnvByWorker",
        "extraArgs",
        "harborRepoPath",
        "datasetPath",
        "uvBinary",
        "mounts",
    ):
        if key in body_config and body_config[key] is not None:
            config[key] = body_config[key]
    return config


def build_asset_sync_executor_config(
    *,
    worker_ids: list[str],
    workers: list[dict[str, Any]],
    body_config: dict[str, Any],
    jobs_dir: str,
) -> dict[str, Any]:
    workers_by_id = {str(worker["worker_id"]): worker for worker in workers}
    harbor_repo_by_worker = {
        worker_id: _default_harbor_for_worker(worker_id, workers_by_id.get(worker_id))
        for worker_id in worker_ids
    }
    uv_binary_by_worker = {
        worker_id: _default_uv_for_worker(worker_id, workers_by_id.get(worker_id))
        for worker_id in worker_ids
    }
    return {
        **build_executor_config(
            dataset_ref="",
            worker_ids=worker_ids,
            workers=workers,
            body_config=body_config,
            jobs_dir=jobs_dir,
        ),
        "useAssetSync": True,
        "datasetPathByWorker": {},
        "mountsByWorker": {},
        "harborRepoPathByWorker": harbor_repo_by_worker,
        "uvBinaryByWorker": uv_binary_by_worker,
    }
