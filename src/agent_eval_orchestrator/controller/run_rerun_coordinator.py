from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_eval_orchestrator.controller.asset_syncer import build_sync_manifest, validate_create_task_assets
from agent_eval_orchestrator.controller.executor_config import build_asset_sync_executor_config
from agent_eval_orchestrator.core.defaults import DEFAULT_HARBOR_REPO, DEFAULT_PER_WORKER_CONCURRENCY
from agent_eval_orchestrator.core.ids import new_id

if TYPE_CHECKING:
    from agent_eval_orchestrator.controller.asset_syncer import AssetSyncer
    from agent_eval_orchestrator.storage.store import Store

RERUN_CONFIG_KEYS = {"datasetPath", "bitfunCliPath", "bitfunConfigDir", "jobsDir", "executorConfig"}


class RerunValidationError(Exception):
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class RunRerunCoordinator:
    def __init__(self, *, store: Store, asset_syncer: AssetSyncer | None) -> None:
        self.store = store
        self.asset_syncer = asset_syncer

    def start_rerun(self, run_id: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
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

        worker_shards = {
            worker_id: [str(item["case_id"]) for item in items]
            for worker_id, items in grouped.items()
        }
        all_case_ids = [
            case_id
            for case_ids in worker_shards.values()
            for case_id in case_ids
        ]
        config_supplied = self._has_applicable_config(config)
        rerun_concurrency: int | None = None
        if config_supplied:
            rerun_concurrency = self._apply_config(
                run=run,
                config=dict(config or {}),
                worker_shards=worker_shards,
                all_case_ids=all_case_ids,
            )

        job_id = new_id("rerun")
        rerun_batches: dict[str, str] = {}
        for worker_id, items in grouped.items():
            case_ids = worker_shards[worker_id]
            parent_batch_id = str(items[0]["parent_batch_id"])
            parent = self.store.get_batch(parent_batch_id)
            batch_options = dict((parent or {}).get("batch_options") or {})
            if rerun_concurrency is not None:
                batch_options["concurrency"] = rerun_concurrency
            batch = self.store.create_batch(
                run_id=run_id,
                selected_case_ids=case_ids,
                preferred_worker_id=worker_id,
                batch_options=batch_options,
                initial_status="pending_sync",
                batch_kind="exception_rerun",
                parent_batch_id=parent_batch_id,
            )
            rerun_batches[worker_id] = str(batch["batch_id"])

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

    def _has_applicable_config(self, config: dict[str, Any] | None) -> bool:
        if not config:
            return False
        return any(key in config for key in RERUN_CONFIG_KEYS)

    def _apply_config(
        self,
        *,
        run: dict[str, Any],
        config: dict[str, Any],
        worker_shards: dict[str, list[str]],
        all_case_ids: list[str],
    ) -> int:
        template = self.store.get_task_template(str(run["template_id"]))
        if not template:
            raise RerunValidationError(404, "task template not found")
        existing_executor_config = dict(template.get("executor_config") or {})
        existing_manifest = dict(run.get("sync_manifest") or {})
        controller_root = (
            self.asset_syncer.controller_shared_root
            if self.asset_syncer is not None
            else self.store.layout.root
        )

        dataset_path = Path(
            str(
                config.get("datasetPath")
                or existing_manifest.get("datasetPath")
                or template.get("dataset_ref")
                or ""
            )
        ).expanduser()
        bitfun_cli_path = Path(
            str(config.get("bitfunCliPath") or existing_manifest.get("bitfunCliPath") or "")
        ).expanduser()
        bitfun_config_dir = Path(
            str(config.get("bitfunConfigDir") or existing_manifest.get("bitfunConfigDir") or "")
        ).expanduser()
        jobs_dir = str(
            config.get("jobsDir")
            or existing_executor_config.get("combinedJobsDir")
            or (DEFAULT_HARBOR_REPO / "jobs")
        )
        body_config = {
            **existing_executor_config,
            **dict(config.get("executorConfig") or {}),
        }
        worker_ids = list(worker_shards.keys())
        workers = self.store.list_workers()
        workers_by_id = {str(item["worker_id"]): item for item in workers}

        try:
            validate_create_task_assets(
                dataset_path=dataset_path,
                bitfun_cli_path=bitfun_cli_path,
                bitfun_config_dir=bitfun_config_dir,
                case_ids=all_case_ids,
                workers=workers,
                worker_ids=worker_ids,
                controller_shared_root=controller_root,
            )
            executor_config = build_asset_sync_executor_config(
                worker_ids=worker_ids,
                workers=workers,
                body_config=body_config,
                jobs_dir=jobs_dir,
            )
            manifest = build_sync_manifest(
                run_id=str(run["run_id"]),
                dataset_path=dataset_path.resolve(),
                bitfun_cli_path=bitfun_cli_path.resolve(),
                bitfun_config_dir=bitfun_config_dir.resolve(),
                worker_shards=worker_shards,
                workers_by_id=workers_by_id,
                controller_shared_root=controller_root,
            )
        except Exception as exc:
            raise RerunValidationError(400, str(exc)) from exc

        existing_workers = existing_manifest.get("workers") or {}
        for worker_id, entry in manifest["workers"].items():
            existing_entry = dict(existing_workers.get(worker_id) or {})
            for key in ("targetRoot", "transport", "sshHostAlias"):
                if key in existing_entry:
                    entry[key] = existing_entry[key]

        self.store.update_task_template_executor_config(
            str(template["template_id"]),
            executor_config,
            replace_keys={
                "uvBinaryByWorker",
                "harborRepoPathByWorker",
                "datasetPathByWorker",
                "mountsByWorker",
            },
        )
        self.store.update_task_template_dataset_ref(str(template["template_id"]), str(dataset_path.resolve()))
        self.store.update_run_sync_fields(
            run_id=str(run["run_id"]),
            sync_manifest=manifest,
        )
        return int(executor_config.get("nConcurrent") or DEFAULT_PER_WORKER_CONCURRENCY)
