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

RERUN_CONFIG_KEYS = ("datasetPath", "bitfunCliPath", "bitfunConfigDir", "jobsDir", "executorConfig")
RERUN_SCOPE_KEYS = ("selectedErrorTypes",)


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
        if str(run.get("rerun_status") or "") in {"syncing", "running"}:
            raise RerunValidationError(409, "rerun already in progress")
        if self.store.list_active_derived_reruns(run_id):
            raise RerunValidationError(409, "rerun already in progress")
        selected_error_types = self._resolve_selected_error_types(run_id=run_id, config=config)
        if not selected_error_types:
            raise RerunValidationError(400, "no exception cases")

        filtered_items = self.store.filter_exception_cases_by_types(run_id, selected_error_types)
        if not filtered_items:
            raise RerunValidationError(400, "no matching exception cases")

        grouped = self.store.group_exception_items_by_worker(filtered_items)
        if not grouped:
            raise RerunValidationError(400, "no exception cases")

        source_template = self.store.get_task_template(str(run["template_id"]))
        if not source_template:
            raise RerunValidationError(404, "task template not found")
        existing_manifest = dict(run.get("sync_manifest") or {})
        dataset_path = self._resolve_dataset_path(
            config=config,
            template=source_template,
            existing_manifest=existing_manifest,
        )
        worker_shards = self._resolve_worker_shards(grouped, dataset_path)
        all_case_ids = [
            case_id
            for case_ids in worker_shards.values()
            for case_id in case_ids
        ]
        asset_config = self._filter_config_for_assets(dict(config or {}))
        config_supplied = self._has_applicable_config(asset_config)
        if config_supplied:
            self._prevalidate_config(
                config=dict(asset_config or {}),
                template=source_template,
                fallback_manifest=existing_manifest,
                worker_shards=worker_shards,
                all_case_ids=all_case_ids,
            )
        derived_template = self.store.clone_task_template(
            str(source_template["template_id"]),
            name=f"{source_template['name']} rerun",
        )
        derived_run = self.store.create_run(
            template_id=str(derived_template["template_id"]),
            display_name=f"{run['display_name']} rerun",
            parent_run_id=run_id,
        )
        rerun_concurrency: int | None = None
        if config_supplied:
            rerun_concurrency = self._apply_config(
                run=derived_run,
                config=dict(asset_config or {}),
                fallback_manifest=existing_manifest,
                worker_shards=worker_shards,
                all_case_ids=all_case_ids,
            )

        cloned_batch_ids = self.store.clone_primary_batches_to_run(
            source_run_id=run_id,
            target_run_id=str(derived_run["run_id"]),
        )
        job_id = new_id("rerun")
        rerun_batches: dict[str, str | list[str]] = {}
        for worker_id, items in grouped.items():
            batches_for_worker: list[str] = []
            by_parent: dict[str, list[str]] = {}
            for item, case_id in zip(items, worker_shards[worker_id]):
                by_parent.setdefault(str(item["parent_batch_id"]), []).append(case_id)
            for original_parent_batch_id, case_ids in by_parent.items():
                parent_batch_id = cloned_batch_ids[original_parent_batch_id]
                parent = self.store.get_batch(parent_batch_id)
                batch_options = dict((parent or {}).get("batch_options") or {})
                if rerun_concurrency is not None:
                    batch_options["concurrency"] = rerun_concurrency
                batch = self.store.create_batch(
                    run_id=str(derived_run["run_id"]),
                    selected_case_ids=case_ids,
                    preferred_worker_id=worker_id,
                    batch_options=batch_options,
                    initial_status="pending_sync",
                    batch_kind="exception_rerun",
                    parent_batch_id=parent_batch_id,
                )
                batches_for_worker.append(str(batch["batch_id"]))
            rerun_batches[worker_id] = batches_for_worker[0] if len(batches_for_worker) == 1 else batches_for_worker

        self.store.create_run_rerun_job(
            job_id=job_id,
            run_id=str(derived_run["run_id"]),
            case_ids=all_case_ids,
            worker_shards=worker_shards,
            rerun_batches=rerun_batches,
            selected_error_types=selected_error_types,
        )
        self.store.update_run_rerun_fields(
            run_id=str(derived_run["run_id"]),
            rerun_status="syncing",
            rerun_job_id=job_id,
        )
        if self.asset_syncer is not None:
            self.asset_syncer.start_rerun_sync_async(job_id=job_id, run_id=str(derived_run["run_id"]))

        return {
            "runId": str(derived_run["run_id"]),
            "parentRunId": run_id,
            "rerunJobId": job_id,
            "rerunStatus": "syncing",
            "exceptionCount": len(all_case_ids),
            "selectedErrorTypes": selected_error_types,
            "workerShards": {worker_id: len(case_ids) for worker_id, case_ids in worker_shards.items()},
        }

    def _resolve_selected_error_types(
        self,
        *,
        run_id: str,
        config: dict[str, Any] | None,
    ) -> list[str]:
        summary = self.store.summarize_exception_types_for_run(run_id)
        available = [entry["errorType"] for entry in summary["byType"]]
        if not available:
            return []
        raw = (config or {}).get("selectedErrorTypes")
        if raw is None:
            return available
        if not isinstance(raw, list):
            raise RerunValidationError(400, "selectedErrorTypes must be an array")
        selected = [str(item).strip() for item in raw if str(item).strip()]
        if not selected:
            raise RerunValidationError(400, "at least one error type required")
        available_set = set(available)
        invalid = sorted({item for item in selected if item not in available_set})
        if invalid:
            raise RerunValidationError(400, f"invalid error type(s): {', '.join(invalid)}")
        return selected

    def _filter_config_for_assets(self, config: dict[str, Any] | None) -> dict[str, Any] | None:
        if not config:
            return None
        filtered = {
            key: value
            for key, value in config.items()
            if key in RERUN_CONFIG_KEYS
        }
        return filtered or None

    def _resolve_dataset_path(
        self,
        *,
        config: dict[str, Any] | None,
        template: dict[str, Any] | None,
        existing_manifest: dict[str, Any],
    ) -> Path:
        return Path(
            str(
                (config or {}).get("datasetPath")
                or existing_manifest.get("datasetPath")
                or (template or {}).get("dataset_ref")
                or ""
            )
        ).expanduser()

    def _resolve_worker_shards(
        self,
        grouped: dict[str, list[dict[str, Any]]],
        dataset_path: Path,
    ) -> dict[str, list[str]]:
        worker_shards: dict[str, list[str]] = {}
        for worker_id, items in grouped.items():
            resolved_ids: list[str] = []
            for item in items:
                case = dict(item.get("case") or {})
                parent = self.store.get_batch(str(item["parent_batch_id"]))
                selected_case_ids = list((parent or {}).get("selected_case_ids") or [])
                resolved = self.store.resolve_dataset_case_id(
                    dataset_path=dataset_path,
                    case=case,
                    selected_case_ids=selected_case_ids,
                )
                resolved_ids.append(resolved or str(item["case_id"]))
            worker_shards[worker_id] = resolved_ids
        return worker_shards

    def _has_applicable_config(self, config: dict[str, Any] | None) -> bool:
        if not config:
            return False
        for key in RERUN_CONFIG_KEYS:
            if key not in config:
                continue
            value = config.get(key)
            if key == "executorConfig":
                if value not in (None, {}):
                    return True
                continue
            if value not in (None, ""):
                return True
        return False

    def _prevalidate_config(
        self,
        *,
        config: dict[str, Any],
        template: dict[str, Any],
        fallback_manifest: dict[str, Any],
        worker_shards: dict[str, list[str]],
        all_case_ids: list[str],
    ) -> None:
        existing_executor_config = dict(template.get("executor_config") or {})
        controller_root = (
            self.asset_syncer.controller_shared_root
            if self.asset_syncer is not None
            else self.store.layout.root
        )
        dataset_path = Path(
            str(
                config.get("datasetPath")
                or fallback_manifest.get("datasetPath")
                or template.get("dataset_ref")
                or ""
            )
        ).expanduser()
        bitfun_cli_path = Path(
            str(
                config.get("bitfunCliPath")
                or fallback_manifest.get("bitfunCliPath")
                or ""
            )
        ).expanduser()
        bitfun_config_dir = Path(
            str(
                config.get("bitfunConfigDir")
                or fallback_manifest.get("bitfunConfigDir")
                or ""
            )
        ).expanduser()
        submitted_executor_config = config.get("executorConfig")
        if submitted_executor_config is None:
            submitted_executor_config = {}
        elif not isinstance(submitted_executor_config, dict):
            raise RerunValidationError(400, "executorConfig must be an object")
        body_config = self._filter_worker_maps(
            {
                **existing_executor_config,
                **submitted_executor_config,
            },
            list(worker_shards.keys()),
        )
        self._validate_executor_config(body_config)
        try:
            validate_create_task_assets(
                dataset_path=dataset_path,
                bitfun_cli_path=bitfun_cli_path,
                bitfun_config_dir=bitfun_config_dir,
                case_ids=all_case_ids,
                workers=self.store.list_workers(),
                worker_ids=list(worker_shards.keys()),
                controller_shared_root=controller_root,
            )
        except RuntimeError as exc:
            raise RerunValidationError(400, str(exc)) from exc

    def _apply_config(
        self,
        *,
        run: dict[str, Any],
        config: dict[str, Any],
        fallback_manifest: dict[str, Any] | None = None,
        worker_shards: dict[str, list[str]],
        all_case_ids: list[str],
    ) -> int:
        template = self.store.get_task_template(str(run["template_id"]))
        if not template:
            raise RerunValidationError(404, "task template not found")
        existing_executor_config = dict(template.get("executor_config") or {})
        existing_manifest = dict(run.get("sync_manifest") or {})
        fallback_manifest = dict(fallback_manifest or {})
        controller_root = (
            self.asset_syncer.controller_shared_root
            if self.asset_syncer is not None
            else self.store.layout.root
        )

        dataset_path = Path(
            str(
                config.get("datasetPath")
                or existing_manifest.get("datasetPath")
                or fallback_manifest.get("datasetPath")
                or template.get("dataset_ref")
                or ""
            )
        ).expanduser()
        bitfun_cli_path = Path(
            str(
                config.get("bitfunCliPath")
                or existing_manifest.get("bitfunCliPath")
                or fallback_manifest.get("bitfunCliPath")
                or ""
            )
        ).expanduser()
        bitfun_config_dir = Path(
            str(
                config.get("bitfunConfigDir")
                or existing_manifest.get("bitfunConfigDir")
                or fallback_manifest.get("bitfunConfigDir")
                or ""
            )
        ).expanduser()
        jobs_dir = str(
            config.get("jobsDir")
            or existing_executor_config.get("combinedJobsDir")
            or (DEFAULT_HARBOR_REPO / "jobs")
        )
        worker_ids = list(worker_shards.keys())
        submitted_executor_config = config.get("executorConfig")
        if submitted_executor_config is None:
            submitted_executor_config = {}
        elif not isinstance(submitted_executor_config, dict):
            raise RerunValidationError(400, "executorConfig must be an object")
        body_config = self._filter_worker_maps(
            {
                **existing_executor_config,
                **submitted_executor_config,
            },
            worker_ids,
        )
        self._validate_executor_config(body_config)
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
        except RuntimeError as exc:
            raise RerunValidationError(400, str(exc)) from exc

        try:
            executor_config = build_asset_sync_executor_config(
                worker_ids=worker_ids,
                workers=workers,
                body_config=body_config,
                jobs_dir=jobs_dir,
            )
            for key, value in body_config.items():
                if key.endswith("ByWorker") and isinstance(value, dict) and key not in executor_config:
                    executor_config[key] = value
            manifest = build_sync_manifest(
                run_id=str(run["run_id"]),
                dataset_path=dataset_path.resolve(),
                bitfun_cli_path=bitfun_cli_path.resolve(),
                bitfun_config_dir=bitfun_config_dir.resolve(),
                worker_shards=worker_shards,
                workers_by_id=workers_by_id,
                controller_shared_root=controller_root,
            )
        except (KeyError, RuntimeError, TypeError, ValueError) as exc:
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
                key
                for key, value in executor_config.items()
                if key.endswith("ByWorker") and isinstance(value, dict)
            },
        )
        self.store.update_task_template_dataset_ref(str(template["template_id"]), str(dataset_path.resolve()))
        self.store.update_run_sync_fields(
            run_id=str(run["run_id"]),
            sync_manifest=manifest,
        )
        return int(executor_config.get("nConcurrent") or DEFAULT_PER_WORKER_CONCURRENCY)

    def _filter_worker_maps(self, body_config: dict[str, Any], worker_ids: list[str]) -> dict[str, Any]:
        allowed = set(worker_ids)
        filtered: dict[str, Any] = {}
        for key, value in body_config.items():
            if key.endswith("ByWorker") and isinstance(value, dict):
                filtered[key] = {
                    str(worker_id): worker_value
                    for worker_id, worker_value in value.items()
                    if str(worker_id) in allowed
                }
                continue
            filtered[key] = value
        return filtered

    def _validate_executor_config(self, body_config: dict[str, Any]) -> None:
        if "nConcurrent" in body_config and body_config.get("nConcurrent") not in (None, ""):
            raw_n_concurrent = body_config["nConcurrent"]
            if isinstance(raw_n_concurrent, bool):
                raise RerunValidationError(400, "executorConfig.nConcurrent must be a positive integer")
            if isinstance(raw_n_concurrent, int):
                n_concurrent = raw_n_concurrent
            elif isinstance(raw_n_concurrent, str) and raw_n_concurrent.strip().isdigit():
                n_concurrent = int(raw_n_concurrent.strip())
            else:
                raise RerunValidationError(400, "executorConfig.nConcurrent must be a positive integer")
            if n_concurrent < 1:
                raise RerunValidationError(400, "executorConfig.nConcurrent must be a positive integer")

        for key in (
            "timeoutMultiplier",
            "agentTimeoutMultiplier",
            "verifierTimeoutMultiplier",
            "environmentBuildTimeoutMultiplier",
        ):
            if key not in body_config or body_config.get(key) in (None, ""):
                continue
            try:
                value = float(body_config[key])
            except (TypeError, ValueError) as exc:
                raise RerunValidationError(400, f"executorConfig.{key} must be a positive number") from exc
            if value <= 0:
                raise RerunValidationError(400, f"executorConfig.{key} must be a positive number")
