from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent_eval_orchestrator.core.ids import new_id

if TYPE_CHECKING:
    from agent_eval_orchestrator.controller.asset_syncer import AssetSyncer
    from agent_eval_orchestrator.storage.store import Store


class RerunValidationError(Exception):
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class RunRerunCoordinator:
    def __init__(self, *, store: Store, asset_syncer: AssetSyncer | None) -> None:
        self.store = store
        self.asset_syncer = asset_syncer

    def start_rerun(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if not run:
            raise RerunValidationError(404, "run not found")
        if not self.store.is_run_primary_terminal(run_id):
            raise RerunValidationError(409, "run not finished")
        rerun_status = str(run.get("rerun_status") or "idle")
        if rerun_status in {"syncing", "running"}:
            raise RerunValidationError(409, "rerun already in progress")
        grouped = self.store.group_exception_cases_by_worker(run_id)
        if not grouped:
            raise RerunValidationError(400, "no exception cases")

        job_id = new_id("rerun")
        rerun_batches: dict[str, str] = {}
        worker_shards: dict[str, list[str]] = {}
        all_case_ids: list[str] = []
        for worker_id, items in grouped.items():
            case_ids = [str(item["case_id"]) for item in items]
            parent_batch_id = str(items[0]["parent_batch_id"])
            parent = self.store.get_batch(parent_batch_id)
            batch = self.store.create_batch(
                run_id=run_id,
                selected_case_ids=case_ids,
                preferred_worker_id=worker_id,
                batch_options=dict((parent or {}).get("batch_options") or {}),
                initial_status="pending_sync",
                batch_kind="exception_rerun",
                parent_batch_id=parent_batch_id,
            )
            rerun_batches[worker_id] = str(batch["batch_id"])
            worker_shards[worker_id] = case_ids
            all_case_ids.extend(case_ids)

        self.store.create_run_rerun_job(
            job_id=job_id,
            run_id=run_id,
            case_ids=all_case_ids,
            worker_shards=worker_shards,
            rerun_batches=rerun_batches,
        )
        self.store.update_run_rerun_fields(
            run_id=run_id,
            rerun_status="syncing",
            rerun_job_id=job_id,
        )
        if self.asset_syncer is not None:
            self.asset_syncer.start_rerun_sync_async(job_id=job_id, run_id=run_id)

        return {
            "rerunJobId": job_id,
            "rerunStatus": "syncing",
            "exceptionCount": len(all_case_ids),
            "workerShards": {worker_id: len(case_ids) for worker_id, case_ids in worker_shards.items()},
        }
