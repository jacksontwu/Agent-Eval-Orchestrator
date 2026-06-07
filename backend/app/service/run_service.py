from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.layout import default_layout
from app.model import repo_batches, repo_case_runs, repo_runs, repo_templates, repo_workers
from app.model.tables import Batch, CaseRun, Run
from app.schema.runs import CreateDistributeRequest, CreateDistributeResponse
from app.service.errors import ServiceError, NotFoundError
from app.service.orchestration import asset_syncer


def get_run(session: Session, run_id: str) -> Run:
    run = repo_runs.get_run(session, run_id)
    if run is None:
        raise NotFoundError(f"run not found: {run_id}")
    return run


def get_run_detail(session: Session, run_id: str) -> tuple[Run, list[Batch]]:
    run = get_run(session, run_id)
    batches = repo_batches.list_batches_for_run(session, run_id)
    return run, batches


def list_case_runs(session: Session, run_id: str) -> list[CaseRun]:
    return repo_case_runs.list_for_run(session, run_id)


def get_batch(session: Session, batch_id: str) -> Batch:
    batch = repo_batches.get_batch(session, batch_id)
    if batch is None:
        raise NotFoundError(f"batch not found: {batch_id}")
    return batch


def _list_dataset_case_ids(dataset_path: Path) -> list[str]:
    if not dataset_path.is_dir():
        return []
    return sorted(child.name for child in dataset_path.iterdir() if child.is_dir())


def _shard_round_robin(case_ids: list[str], n: int) -> list[list[str]]:
    shards: list[list[str]] = [[] for _ in range(n)]
    for index, case_id in enumerate(case_ids):
        shards[index % n].append(case_id)
    return shards


def _sub_split(case_ids: list[str], parts: int) -> list[list[str]]:
    parts = max(1, min(parts, len(case_ids))) if case_ids else 1
    buckets: list[list[str]] = [[] for _ in range(parts)]
    for index, case_id in enumerate(case_ids):
        buckets[index % parts].append(case_id)
    return [bucket for bucket in buckets if bucket]


def create_and_distribute(session: Session, req: CreateDistributeRequest, *, owner: str) -> CreateDistributeResponse:
    dataset_path = Path(req.dataset_path).expanduser()
    case_ids = list(req.selected_case_ids) or _list_dataset_case_ids(dataset_path)
    if not case_ids:
        raise ServiceError("no cases to distribute")

    if req.worker_ids:
        workers = [w for w in (repo_workers.get_worker(session, wid) for wid in req.worker_ids) if w is not None]
    else:
        workers = [w for w in repo_workers.list_workers(session, only_enabled=True) if w.status == "online"]
    if not workers:
        raise ServiceError("no eligible workers")

    template = repo_templates.create_template(
        session, owner=owner, name=req.name, dataset_ref=str(dataset_path),
        executor_kind="harbor-docker", executor_config=req.executor_config,
        model_profile_ref=req.model_profile_ref,
    )
    run = repo_runs.create_run(session, template_id=template.template_id, owner=owner,
                               display_name=req.name)
    layout = default_layout(get_settings().shared_root)

    worker_ids = [w.worker_id for w in workers]
    shards = _shard_round_robin(case_ids, len(worker_ids))
    worker_shards: dict[str, list[str]] = {}
    batch_ids: list[str] = []
    executor_metadata_base = {
        "datasetPath": str(dataset_path),
        "bitfunCliPath": req.bitfun_cli_path,
        "bitfunConfigDir": req.bitfun_config_dir,
        "executorConfig": req.executor_config,
        "modelProfileRef": req.model_profile_ref,
    }
    for worker_id, shard in zip(worker_ids, shards):
        if not shard:
            continue
        worker_shards[worker_id] = shard
        for sub in _sub_split(shard, req.per_worker_concurrency):
            batch = repo_batches.create_batch(
                session, run_id=run.run_id, owner=owner, executor_kind="harbor-docker",
                selected_case_ids=sub, batch_options={"concurrency": req.per_worker_concurrency},
                batch_root=str(layout.batch_dir(owner, run.run_id, "pending")),
                preferred_worker_id=worker_id, executor_metadata=dict(executor_metadata_base),
            )
            session.flush()
            batch_ids.append(batch.batch_id)

    try:
        workers_by_id = {
            w.worker_id: {"worker_id": w.worker_id, "host": w.host, "capabilities": w.capabilities}
            for w in workers
        }
        manifest = asset_syncer.build_sync_manifest(
            run_id=run.run_id,
            dataset_path=dataset_path,
            bitfun_cli_path=Path(req.bitfun_cli_path),
            bitfun_config_dir=Path(req.bitfun_config_dir),
            worker_shards=worker_shards,
            workers_by_id=workers_by_id,
            controller_shared_root=Path(get_settings().shared_root),
        )
        repo_runs.set_sync(session, run.run_id, status="pending", manifest=manifest)
    except Exception:
        # Manifest is best-effort; missing worker sharedRoot must not block task creation.
        pass

    if batch_ids:
        repo_runs.set_latest_batch(session, run.run_id, batch_ids[-1])
    session.commit()
    return CreateDistributeResponse(run_id=run.run_id, batch_ids=batch_ids)
