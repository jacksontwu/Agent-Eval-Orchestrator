from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from agent_eval_orchestrator.controller.asset_syncer import (
    build_sync_manifest,
    validate_create_task_assets,
    validate_dataset_assets,
)
from agent_eval_orchestrator.controller.executor_config import build_asset_sync_executor_config
from agent_eval_orchestrator.controller.harbor_exceptions import (
    exception_type_from_text,
    harbor_trial_case_id,
)
from agent_eval_orchestrator.controller.harbor_yaml import (
    HarborYamlPlan,
    build_batch_harbor_yaml,
    discover_bind_assets,
    parse_rerun_harbor_yaml,
)
from agent_eval_orchestrator.controller.rerun_artifacts import (
    copy_harbor_job,
    derived_jobs_dir_for_run,
    derived_rerun_job_name,
)
from agent_eval_orchestrator.core.defaults import DEFAULT_HARBOR_REPO, DEFAULT_PER_WORKER_CONCURRENCY
from agent_eval_orchestrator.core.ids import new_id, sanitize_name

if TYPE_CHECKING:
    from agent_eval_orchestrator.controller.asset_syncer import AssetSyncer
    from agent_eval_orchestrator.storage.store import Store

RERUN_CONFIG_KEYS = ("datasetPath", "bitfunCliPath", "bitfunConfigDir", "jobsDir", "executorConfig")
RERUN_SCOPE_KEYS = ("selectedErrorTypes",)


@dataclass(frozen=True)
class RerunScope:
    exception_items: list[dict[str, Any]]
    selected_error_types: list[str]
    grouped: dict[str, list[dict[str, Any]]]
    worker_shards: dict[str, list[str]]
    all_case_ids: list[str]
    dataset_path: Path


class RerunValidationError(Exception):
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class RunRerunCoordinator:
    def __init__(self, *, store: Store, asset_syncer: AssetSyncer | None) -> None:
        self.store = store
        self.asset_syncer = asset_syncer
        self._start_lock = threading.Lock()

    def start_rerun(self, run_id: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if not run:
            raise RerunValidationError(404, "run not found")
        if not self.store.is_run_primary_terminal(run_id):
            raise RerunValidationError(409, "run not finished")
        source_template = self.store.get_task_template(str(run["template_id"]))
        if not source_template:
            raise RerunValidationError(404, "task template not found")
        existing_manifest = dict(run.get("sync_manifest") or {})
        scope = self._resolve_rerun_scope(
            run=run,
            template=source_template,
            config=config,
        )
        grouped = scope.grouped
        worker_shards = scope.worker_shards
        all_case_ids = scope.all_case_ids
        selected_error_types = scope.selected_error_types
        asset_config = self._filter_config_for_assets(dict(config or {}))
        config_supplied = self._has_applicable_config(asset_config)
        harbor_yaml_plan = self._parse_harbor_yaml_config(
            config=config,
            selected_task_ids=all_case_ids,
        )
        if harbor_yaml_plan is None and config_supplied:
            self._prevalidate_config(
                config=dict(asset_config or {}),
                template=source_template,
                fallback_manifest=existing_manifest,
                worker_shards=worker_shards,
                all_case_ids=all_case_ids,
            )
        job_id = new_id("rerun")
        derived_run: dict[str, Any] | None = None
        try:
            with self._start_lock:
                current_run = self.store.get_run(run_id)
                if not current_run:
                    raise RerunValidationError(404, "run not found")
                if str(current_run.get("rerun_status") or "") in {"syncing", "running"}:
                    raise RerunValidationError(409, "rerun already in progress")
                if self.store.list_active_derived_reruns(run_id):
                    raise RerunValidationError(409, "rerun already in progress")
                derived_template = self.store.clone_task_template(
                    str(source_template["template_id"]),
                    name=f"{source_template['name']} rerun",
                )
                derived_run = self.store.create_run(
                    template_id=str(derived_template["template_id"]),
                    display_name=f"{run['display_name']} rerun",
                    parent_run_id=run_id,
                )
                source_job_dir = self._source_job_dir_for_run(run=run, template=source_template)
                if source_job_dir is not None:
                    final_job_name = derived_rerun_job_name(
                        source_job_name=source_job_dir.name,
                        run_id=str(derived_run["run_id"]),
                    )
                    updated_run = self.store.update_run_display_name(
                        str(derived_run["run_id"]),
                        final_job_name,
                    )
                    if updated_run is not None:
                        derived_run = updated_run
                self.store.create_run_rerun_job(
                    job_id=job_id,
                    run_id=str(derived_run["run_id"]),
                    case_ids=all_case_ids,
                    worker_shards=worker_shards,
                    rerun_batches={},
                    selected_error_types=selected_error_types,
                )
                self.store.update_run_rerun_fields(
                    run_id=str(derived_run["run_id"]),
                    rerun_status="syncing",
                    rerun_job_id=job_id,
                )
            rerun_concurrency: int | None = None
            if harbor_yaml_plan is None and config_supplied:
                rerun_concurrency = self._apply_config(
                    run=derived_run,
                    config=dict(asset_config or {}),
                    fallback_manifest=existing_manifest,
                    worker_shards=worker_shards,
                    all_case_ids=all_case_ids,
                )
            elif harbor_yaml_plan is None:
                self._set_derived_template_jobs_dir(run=derived_run, source_template=source_template)
            self._copy_and_prune_source_jobs(
                source_template=source_template,
                source_run=run,
                derived_run=derived_run,
            )

            cloned_batch_ids = self.store.clone_primary_batches_to_run(
                source_run_id=run_id,
                target_run_id=str(derived_run["run_id"]),
            )
            rerun_batches: dict[str, str | list[str]] = {}
            rerun_batch_case_ids: dict[str, list[str]] = {}
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
                    rerun_batch_case_ids[str(batch["batch_id"])] = list(case_ids)
                    batches_for_worker.append(str(batch["batch_id"]))
                rerun_batches[worker_id] = (
                    batches_for_worker[0] if len(batches_for_worker) == 1 else batches_for_worker
                )

            if harbor_yaml_plan is not None:
                self._apply_harbor_yaml_config(
                    run=derived_run,
                    plan=harbor_yaml_plan,
                    worker_shards=worker_shards,
                    rerun_batch_case_ids=rerun_batch_case_ids,
                )
            self.store.update_run_rerun_job(
                job_id,
                rerun_batches=rerun_batches,
            )
            if self.asset_syncer is not None:
                self.asset_syncer.start_rerun_sync_async(job_id=job_id, run_id=str(derived_run["run_id"]))
        except Exception as exc:
            if derived_run is not None:
                self._mark_derived_rerun_failed(
                    run_id=str(derived_run["run_id"]),
                    job_id=job_id,
                    error_text=str(exc),
                )
            raise

        return {
            "runId": str(derived_run["run_id"]),
            "parentRunId": run_id,
            "rerunJobId": job_id,
            "rerunStatus": "syncing",
            "exceptionCount": len(all_case_ids),
            "selectedErrorTypes": selected_error_types,
            "workerShards": {worker_id: len(case_ids) for worker_id, case_ids in worker_shards.items()},
        }

    def preview_harbor_yaml(self, run_id: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if not run:
            raise RerunValidationError(404, "run not found")
        if not self.store.is_run_primary_terminal(run_id):
            raise RerunValidationError(409, "run not finished")
        source_template = self.store.get_task_template(str(run["template_id"]))
        if not source_template:
            raise RerunValidationError(404, "task template not found")
        scope = self._resolve_rerun_scope(
            run=run,
            template=source_template,
            config=config,
        )
        harbor_yaml, source = self._preview_harbor_yaml_for_run(run=run, template=source_template)
        return {
            "harborYaml": harbor_yaml,
            "source": source,
            "exceptionCount": len(scope.all_case_ids),
            "selectedErrorTypes": scope.selected_error_types,
            "workerShards": {worker_id: len(case_ids) for worker_id, case_ids in scope.worker_shards.items()},
        }

    def _resolve_rerun_scope(
        self,
        *,
        run: dict[str, Any],
        template: dict[str, Any],
        config: dict[str, Any] | None,
        dataset_path: Path | None = None,
    ) -> RerunScope:
        existing_manifest = dict(run.get("sync_manifest") or {})
        resolved_dataset_path = dataset_path or self._resolve_dataset_path(
            config=config,
            template=template,
            existing_manifest=existing_manifest,
        )
        exception_items = self._list_rerun_exception_items(
            run=run,
            template=template,
            dataset_path=resolved_dataset_path,
        )
        selected_error_types = self._resolve_selected_error_types_from_items(
            exception_items,
            config=config,
        )
        if not selected_error_types:
            raise RerunValidationError(400, "no exception cases")
        selected_set = set(selected_error_types)
        filtered_items = [
            item for item in exception_items if str(item.get("error_type") or "") in selected_set
        ]
        if not filtered_items:
            raise RerunValidationError(400, "no matching exception cases")
        grouped = self.store.group_exception_items_by_worker(filtered_items)
        if not grouped:
            raise RerunValidationError(400, "no exception cases")
        worker_shards = self._resolve_worker_shards(grouped, resolved_dataset_path)
        all_case_ids = [
            case_id
            for case_ids in worker_shards.values()
            for case_id in case_ids
        ]
        return RerunScope(
            exception_items=filtered_items,
            selected_error_types=selected_error_types,
            grouped=grouped,
            worker_shards=worker_shards,
            all_case_ids=all_case_ids,
            dataset_path=resolved_dataset_path,
        )

    def _preview_harbor_yaml_for_run(
        self,
        *,
        run: dict[str, Any],
        template: dict[str, Any],
    ) -> tuple[str, str]:
        executor_config = dict(template.get("executor_config") or {})
        raw_yaml = str(executor_config.get("harborYaml") or "").strip()
        if raw_yaml:
            return raw_yaml, "original_yaml"
        return self._build_legacy_rerun_harbor_yaml(run=run, template=template), "generated_legacy_yaml"

    def _build_legacy_rerun_harbor_yaml(
        self,
        *,
        run: dict[str, Any],
        template: dict[str, Any],
    ) -> str:
        executor_config = dict(template.get("executor_config") or {})
        agent: dict[str, Any] = {"name": str(executor_config.get("agentName") or "bitfun-cli")}
        model_name = str(executor_config.get("modelName") or "").strip()
        if model_name:
            agent["model_name"] = model_name
        payload: dict[str, Any] = {
            "job_name": sanitize_name(str(run.get("display_name") or template.get("name") or "rerun")),
            "jobs_dir": str(executor_config.get("combinedJobsDir") or DEFAULT_HARBOR_REPO / "jobs"),
            "n_concurrent_trials": int(executor_config.get("nConcurrent") or DEFAULT_PER_WORKER_CONCURRENCY),
            "agents": [agent],
            "datasets": [
                {
                    "path": str(template.get("dataset_ref") or ""),
                    "task_names": self._source_run_case_ids(str(run["run_id"])),
                }
            ],
        }
        mapping = {
            "timeoutMultiplier": "timeout_multiplier",
            "agentTimeoutMultiplier": "agent_timeout_multiplier",
            "verifierTimeoutMultiplier": "verifier_timeout_multiplier",
            "environmentBuildTimeoutMultiplier": "environment_build_timeout_multiplier",
        }
        for source_key, yaml_key in mapping.items():
            value = executor_config.get(source_key)
            if value not in (None, ""):
                payload[yaml_key] = value
        env_type = str(executor_config.get("envType") or "").strip()
        mounts = executor_config.get("mounts")
        environment: dict[str, Any] = {}
        if env_type:
            environment["type"] = env_type
        if isinstance(mounts, list) and mounts:
            environment["mounts"] = mounts
        if environment:
            payload["environment"] = environment
        return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)

    def _source_run_case_ids(self, run_id: str) -> list[str]:
        case_ids: list[str] = []
        for batch in self.store.list_primary_batches_for_run(run_id):
            for case_id in batch.get("selected_case_ids") or []:
                value = str(case_id or "").strip()
                if value and value not in case_ids:
                    case_ids.append(value)
        return case_ids

    def _mark_derived_rerun_failed(self, *, run_id: str, job_id: str, error_text: str) -> None:
        job = self.store.get_run_rerun_job(job_id)
        self.store.update_run_rerun_fields(
            run_id=run_id,
            rerun_status="failed",
            rerun_job_id=job_id if job else None,
        )
        if job:
            self.store.update_run_rerun_job(
                job_id,
                status="failed",
                error_text=error_text,
                finished=True,
            )

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

    def _resolve_selected_error_types_from_items(
        self,
        items: list[dict[str, Any]],
        *,
        config: dict[str, Any] | None,
    ) -> list[str]:
        available = sorted(
            {
                str(item.get("error_type") or "").strip()
                for item in items
                if str(item.get("error_type") or "").strip()
            }
        )
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

    def _list_rerun_exception_items(
        self,
        *,
        run: dict[str, Any],
        template: dict[str, Any],
        dataset_path: Path,
    ) -> list[dict[str, Any]]:
        source_job_dir = self._source_job_dir_for_run(run=run, template=template)
        if source_job_dir is not None:
            return self._list_exception_items_from_job_dir(
                run_id=str(run["run_id"]),
                job_dir=source_job_dir,
                dataset_path=dataset_path,
            )
        return self._list_exception_items_from_db(str(run["run_id"]))

    def _source_job_dir_for_run(
        self,
        *,
        run: dict[str, Any],
        template: dict[str, Any],
    ) -> Path | None:
        raw_jobs_dir = str((template.get("executor_config") or {}).get("combinedJobsDir") or "").strip()
        if not raw_jobs_dir:
            return None
        jobs_dir = Path(raw_jobs_dir).expanduser()
        run_job_dir = jobs_dir / sanitize_name(str(run["display_name"]))
        if run_job_dir.is_dir():
            return run_job_dir
        if jobs_dir.is_dir() and (jobs_dir / "config.json").exists():
            return jobs_dir
        return None

    def _list_exception_items_from_db(self, run_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for item in self.store.list_exception_cases_for_run(run_id):
            case = dict(item.get("case") or {})
            error_type = self.store.case_error_type(case)
            enriched = dict(item)
            enriched["error_type"] = error_type
            items.append(enriched)
        return items

    def _list_exception_items_from_job_dir(
        self,
        *,
        run_id: str,
        job_dir: Path,
        dataset_path: Path,
    ) -> list[dict[str, Any]]:
        case_index = self._primary_case_index(run_id=run_id, dataset_path=dataset_path)
        items: list[dict[str, Any]] = []
        seen_cases: set[str] = set()
        for trial_dir in sorted(child for child in job_dir.iterdir() if child.is_dir()):
            exception_path = trial_dir / "exception.txt"
            if not exception_path.exists():
                continue
            case_id = harbor_trial_case_id(trial_dir)
            if not case_id:
                raise RerunValidationError(400, f"missing task_name for exception trial: {trial_dir.name}")
            if case_id in seen_cases:
                raise RerunValidationError(400, f"duplicate exception trial for case: {case_id}")
            seen_cases.add(case_id)
            source_item = case_index.get(case_id)
            if source_item is None and "/" in case_id:
                source_item = case_index.get(case_id.rsplit("/", 1)[-1])
            if source_item is None:
                raise RerunValidationError(400, f"exception trial is not part of run: {case_id}")
            error_type = exception_type_from_text(
                exception_path.read_text(encoding="utf-8", errors="replace")
            )
            item = dict(source_item)
            item["case_id"] = case_id
            item["error_type"] = error_type
            item["source_trial_dir"] = str(trial_dir)
            item["source_trial_name"] = trial_dir.name
            items.append(item)
        return items

    def _primary_case_index(self, *, run_id: str, dataset_path: Path) -> dict[str, dict[str, Any]]:
        indexed: dict[str, dict[str, Any]] = {}
        for batch in self.store.list_primary_batches_for_run(run_id):
            worker_id = str(batch.get("assigned_worker_id") or batch.get("preferred_worker_id") or "").strip()
            selected_case_ids = list(batch.get("selected_case_ids") or [])
            for selected_case_id in selected_case_ids:
                selected = str(selected_case_id or "").strip()
                if not selected:
                    continue
                indexed.setdefault(
                    selected,
                    {
                        "case_id": selected,
                        "parent_batch_id": str(batch["batch_id"]),
                        "worker_id": worker_id,
                        "case": {
                            "case_id": selected,
                            "original_case_id": selected,
                            "status": "errored",
                            "score": None,
                            "metrics": {},
                            "artifact_index": {},
                            "error_text": None,
                        },
                    },
                )
            for case in self.store.list_case_runs(str(batch["batch_id"])):
                item = {
                    "case_id": str(case["case_id"]),
                    "parent_batch_id": str(batch["batch_id"]),
                    "worker_id": worker_id,
                    "case": case,
                }
                keys = {str(case["case_id"])}
                resolved = self.store.resolve_dataset_case_id(
                    dataset_path=dataset_path,
                    case=case,
                    selected_case_ids=selected_case_ids,
                )
                if resolved:
                    keys.add(str(resolved))
                for key in keys:
                    indexed.setdefault(key, item)
        return indexed

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
            if key == "jobsDir":
                continue
            value = config.get(key)
            if key == "executorConfig":
                if value not in (None, {}):
                    return True
                continue
            if value not in (None, ""):
                return True
        return False

    def _parse_harbor_yaml_config(
        self,
        *,
        config: dict[str, Any] | None,
        selected_task_ids: list[str],
    ) -> HarborYamlPlan | None:
        if not isinstance(config, dict) or "harborYaml" not in config:
            return None
        raw_yaml = str(config.get("harborYaml") or "").strip()
        try:
            return parse_rerun_harbor_yaml(raw_yaml, selected_task_ids=selected_task_ids)
        except ValueError as exc:
            raise RerunValidationError(400, str(exc)) from exc

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
        worker_ids = list(worker_shards.keys())
        workers = self.store.list_workers()
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
        jobs_dir = str(
            existing_executor_config.get("combinedJobsDir")
            or (DEFAULT_HARBOR_REPO / "jobs")
        )
        workers_by_id = {str(item["worker_id"]): item for item in workers}
        try:
            build_asset_sync_executor_config(
                worker_ids=worker_ids,
                workers=workers,
                body_config=body_config,
                jobs_dir=jobs_dir,
            )
            build_sync_manifest(
                run_id="prevalidate-rerun",
                dataset_path=dataset_path.resolve(),
                bitfun_cli_path=bitfun_cli_path.resolve(),
                bitfun_config_dir=bitfun_config_dir.resolve(),
                worker_shards=worker_shards,
                workers_by_id=workers_by_id,
                controller_shared_root=controller_root,
            )
        except (KeyError, RuntimeError, TypeError, ValueError) as exc:
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
            existing_executor_config.get("combinedJobsDir")
            or derived_jobs_dir_for_run(store=self.store, run=run)
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

    def _apply_harbor_yaml_config(
        self,
        *,
        run: dict[str, Any],
        plan: HarborYamlPlan,
        worker_shards: dict[str, list[str]],
        rerun_batch_case_ids: dict[str, list[str]],
    ) -> None:
        template = self.store.get_task_template(str(run["template_id"]))
        if not template:
            raise RerunValidationError(404, "task template not found")
        controller_root = (
            self.asset_syncer.controller_shared_root
            if self.asset_syncer is not None
            else self.store.layout.root
        )
        workers = self.store.list_workers()
        workers_by_id = {str(item["worker_id"]): item for item in workers}
        worker_ids = list(worker_shards.keys())
        task_sources = (
            {task_id: str(task["path"]) for task_id, task in plan.tasks_by_id.items()}
            if plan.mode == "tasks"
            else None
        )
        try:
            bind_assets = discover_bind_assets(plan.original_config)
            validate_dataset_assets(
                dataset_path=Path(plan.dataset_ref),
                case_ids=plan.task_ids,
                workers=workers,
                worker_ids=worker_ids,
                controller_shared_root=controller_root,
                task_sources=task_sources,
            )
            manifest = build_sync_manifest(
                run_id=str(run["run_id"]),
                dataset_path=Path(plan.dataset_ref),
                worker_shards=worker_shards,
                workers_by_id=workers_by_id,
                controller_shared_root=controller_root,
                bind_assets=bind_assets,
                task_sources=task_sources,
            )
        except (RuntimeError, ValueError) as exc:
            raise RerunValidationError(400, str(exc)) from exc
        runtime_job_name = sanitize_name(str(run["display_name"]))
        derived_jobs_dir = derived_jobs_dir_for_run(store=self.store, run=run)
        self._copy_source_job_to_derived_jobs_dir(
            derived_run=run,
            target_jobs_dir=derived_jobs_dir,
        )
        runtime_plan = replace(plan, generated_job_name=runtime_job_name)
        yaml_by_batch_id: dict[str, str] = {}
        for batch_id, case_ids in rerun_batch_case_ids.items():
            batch = self.store.get_batch(batch_id)
            if not batch:
                raise RerunValidationError(404, f"rerun batch not found: {batch_id}")
            worker_id = str(batch.get("preferred_worker_id") or batch.get("assigned_worker_id") or "")
            worker_sync_root = str(manifest["workers"][worker_id]["targetRoot"])
            worker_dataset_path = str(Path(worker_sync_root) / "dataset")
            try:
                yaml_by_batch_id[batch_id] = build_batch_harbor_yaml(
                    runtime_plan,
                    batch_id=batch_id,
                    selected_task_ids=case_ids,
                    jobs_dir=str(Path(str(batch["batch_root"])) / "harbor" / "jobs"),
                    worker_dataset_path=worker_dataset_path,
                    worker_sync_root=worker_sync_root,
                    bind_assets=bind_assets,
                )
            except ValueError as exc:
                raise RerunValidationError(400, str(exc)) from exc
        self.store.update_task_template_executor_config(
            str(template["template_id"]),
            {
                "harborYaml": plan.original_yaml,
                "harborYamlMode": plan.mode,
                "harborYamlTaskIds": plan.task_ids,
                "harborYamlGeneratedJobName": runtime_job_name,
                "harborYamlByBatchId": yaml_by_batch_id,
                "collectJobs": True,
                "combinedJobsDir": str(derived_jobs_dir),
            },
            replace_keys={"harborYamlByBatchId"},
        )
        self.store.update_task_template_dataset_ref(str(template["template_id"]), plan.dataset_ref)
        self.store.update_run_sync_fields(
            run_id=str(run["run_id"]),
            sync_manifest=manifest,
        )

    def _copy_source_job_to_derived_jobs_dir(
        self,
        *,
        derived_run: dict[str, Any],
        target_jobs_dir: Path,
    ) -> None:
        parent_run_id = str(derived_run.get("parent_run_id") or "").strip()
        if not parent_run_id:
            return
        parent_run = self.store.get_run(parent_run_id)
        if not parent_run:
            return
        source_template = self.store.get_task_template(str(parent_run["template_id"]))
        if not source_template:
            return
        source_job_dir = self._source_job_dir_for_run(run=parent_run, template=source_template)
        if source_job_dir is None:
            return
        copy_harbor_job(source_job_dir, target_jobs_dir / source_job_dir.name)

    def _set_derived_template_jobs_dir(
        self,
        *,
        run: dict[str, Any],
        source_template: dict[str, Any],
    ) -> None:
        template = self.store.get_task_template(str(run["template_id"]))
        if not template:
            raise RerunValidationError(404, "task template not found")
        source_jobs_dir = str(
            (source_template.get("executor_config") or {}).get("combinedJobsDir") or ""
        ).strip()
        self.store.update_task_template_executor_config(
            str(template["template_id"]),
            {"combinedJobsDir": source_jobs_dir or str(derived_jobs_dir_for_run(store=self.store, run=run))},
        )

    def _copy_and_prune_source_jobs(
        self,
        *,
        source_template: dict[str, Any],
        source_run: dict[str, Any],
        derived_run: dict[str, Any],
    ) -> None:
        source_job_dir = self._source_job_dir_for_run(run=source_run, template=source_template)
        if source_job_dir is None:
            source_jobs_dir = str(
                (source_template.get("executor_config") or {}).get("combinedJobsDir") or ""
            ).strip()
            if source_jobs_dir:
                expected = Path(source_jobs_dir).expanduser() / sanitize_name(str(source_run["display_name"]))
                raise RuntimeError(f"source job directory not found: {expected}")
            return
        final_job_name = derived_rerun_job_name(
            source_job_name=source_job_dir.name,
            run_id=str(derived_run["run_id"]),
        )
        target_job_dir = source_job_dir.parent / final_job_name
        copy_harbor_job(source_job_dir, target_job_dir)

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
