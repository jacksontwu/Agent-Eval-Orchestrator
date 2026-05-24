from __future__ import annotations

import json
import shlex
import shutil
from pathlib import Path
from typing import Any

from agent_eval_orchestrator.core.defaults import DEFAULT_HARBOR_REPO
from agent_eval_orchestrator.core.worker_paths import resolve_harbor_repo, resolve_uv_binary
from agent_eval_orchestrator.executors.base import CollectedArtifacts, Executor, PreparedBatch


def _copy_selected_tasks(dataset_path: Path, selected_case_ids: list[str], target_root: Path) -> Path:
    target_root.mkdir(parents=True, exist_ok=True)
    for case_id in selected_case_ids:
        source_dir = dataset_path / case_id
        if not source_dir.exists():
            raise RuntimeError(f"selected case path not found: {source_dir}")
        shutil.copytree(source_dir, target_root / case_id, dirs_exist_ok=True)
    return target_root


class HarborExecutor(Executor):
    kind = "harbor-docker"

    @staticmethod
    def _resolve_worker_override(executor_config: dict[str, Any], worker_id: str | None, key: str, default: Any) -> Any:
        if worker_id:
            mapping = executor_config.get(f"{key}ByWorker")
            if isinstance(mapping, dict) and worker_id in mapping:
                return mapping[worker_id]
        return executor_config.get(key, default)

    @staticmethod
    def _worker_mapping_value(executor_config: dict[str, Any], worker_id: str | None, key: str) -> str | None:
        if not worker_id:
            return None
        mapping = executor_config.get(f"{key}ByWorker")
        if not isinstance(mapping, dict) or worker_id not in mapping:
            return None
        value = str(mapping[worker_id] or "").strip()
        return value or None

    def prepare(
        self,
        *,
        batch: dict[str, Any],
        run: dict[str, Any],
        template: dict[str, Any],
        dataset_ref: str,
        executor_config: dict[str, Any],
        local_root: Path,
        shared_root: Path | None = None,
    ) -> PreparedBatch:
        batch_root = Path(str(batch["batch_root"])).resolve()
        batch_root.mkdir(parents=True, exist_ok=True)
        worker_id = str(batch.get("assigned_worker_id") or batch.get("preferred_worker_id") or "").strip() or None
        harbor_root = resolve_harbor_repo(
            explicit=str(executor_config.get("harborRepoPath") or "").strip() or None,
            shared_root=shared_root,
            configured=self._worker_mapping_value(executor_config, worker_id, "harborRepoPath"),
            default=DEFAULT_HARBOR_REPO,
        )

        dataset_path = Path(
            str(self._resolve_worker_override(executor_config, worker_id, "datasetPath", dataset_ref))
        ).expanduser().resolve()
        if not dataset_path.exists():
            raise RuntimeError(f"dataset path not found: {dataset_path}")

        selected_case_ids = list(batch.get("selected_case_ids") or [])
        effective_dataset_path = dataset_path
        if selected_case_ids and not (dataset_path / "task.toml").exists():
            effective_dataset_path = _copy_selected_tasks(
                dataset_path,
                selected_case_ids,
                local_root / "dataset-subset",
            )

        jobs_dir = batch_root / "harbor" / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        job_name = str(batch["batch_id"])
        job_dir = jobs_dir / job_name
        worker_log_path = batch_root / "worker.log"

        uv_binary = resolve_uv_binary(
            explicit=str(executor_config.get("uvBinary") or "").strip() or None,
            configured=self._worker_mapping_value(executor_config, worker_id, "uvBinary"),
            shared_root=shared_root,
        )
        harbor_args = [
            "run",
            "harbor",
            "run",
            "--job-name",
            job_name,
            "--jobs-dir",
            str(jobs_dir),
            "-p",
            str(effective_dataset_path),
            "-a",
            str(self._resolve_worker_override(executor_config, worker_id, "agentName", "oracle")),
            "-e",
            str(self._resolve_worker_override(executor_config, worker_id, "envType", "docker")),
            "--n-concurrent",
            str(min(
                int(executor_config.get("nConcurrent") or batch.get("batch_options", {}).get("concurrency") or 1),
                max(1, len(selected_case_ids) or 1),
            )),
            "-y",
        ]
        model_name = str(self._resolve_worker_override(executor_config, worker_id, "modelName", "") or "").strip()
        if model_name:
            harbor_args.extend(["-m", model_name])

        agent_kwargs = self._resolve_worker_override(executor_config, worker_id, "agentKwargs", {}) or {}
        for key, value in sorted(agent_kwargs.items()):
            harbor_args.extend(["--ak", f"{key}={value}"])
        agent_env = self._resolve_worker_override(executor_config, worker_id, "agentEnv", {}) or {}
        for key, value in sorted(agent_env.items()):
            harbor_args.extend(["--ae", f"{key}={value}"])
        timeout_multiplier = executor_config.get("timeoutMultiplier")
        if timeout_multiplier is not None:
            harbor_args.extend(["--timeout-multiplier", str(timeout_multiplier)])
        agent_timeout_multiplier = executor_config.get("agentTimeoutMultiplier")
        if agent_timeout_multiplier is not None:
            harbor_args.extend(["--agent-timeout-multiplier", str(agent_timeout_multiplier)])
        verifier_timeout_multiplier = executor_config.get("verifierTimeoutMultiplier")
        if verifier_timeout_multiplier is not None:
            harbor_args.extend(["--verifier-timeout-multiplier", str(verifier_timeout_multiplier)])
        agent_setup_timeout_multiplier = executor_config.get("agentSetupTimeoutMultiplier")
        if agent_setup_timeout_multiplier is not None:
            harbor_args.extend(["--agent-setup-timeout-multiplier", str(agent_setup_timeout_multiplier)])
        environment_build_timeout_multiplier = executor_config.get("environmentBuildTimeoutMultiplier")
        if environment_build_timeout_multiplier is not None:
            harbor_args.extend(
                ["--environment-build-timeout-multiplier", str(environment_build_timeout_multiplier)]
            )
        mounts = self._resolve_worker_override(executor_config, worker_id, "mounts", None)
        if mounts:
            harbor_args.extend(["--mounts", json.dumps(mounts, ensure_ascii=False)])
        for extra_arg in executor_config.get("extraArgs") or []:
            harbor_args.append(str(extra_arg))

        quoted_uv = shlex.quote(uv_binary)
        quoted_args = " ".join(shlex.quote(arg) for arg in harbor_args)
        command = [
            "/bin/bash",
            "-lc",
            (
                f'UV={quoted_uv}; '
                f'if ! command -v "$UV" >/dev/null 2>&1 && [ ! -x "$UV" ]; then '
                "curl -LsSf https://astral.sh/uv/install.sh | sh; "
                'UV="$(command -v uv || true)"; '
                "fi; "
                'if [ -z "$UV" ]; then echo "uv not found after install" >&2; exit 127; fi; '
                f'exec "$UV" {quoted_args}'
            ),
        ]

        env = {
            **{
                key: str(value)
                for key, value in (self._resolve_worker_override(executor_config, worker_id, "processEnv", {}) or {}).items()
            },
            "PYTHONUNBUFFERED": "1",
        }
        metadata = {
            "executorKind": self.kind,
            "harborRepoPath": str(harbor_root),
            "jobName": job_name,
            "jobsDir": str(jobs_dir),
            "datasetPath": str(effective_dataset_path),
            "selectedCaseIds": selected_case_ids,
            "command": command,
            "uvBinary": uv_binary,
            "collectJobs": bool(executor_config.get("collectJobs")),
            "combinedJobsDir": str(executor_config.get("combinedJobsDir") or ""),
        }
        return PreparedBatch(
            command=command,
            env=env,
            cwd=harbor_root,
            batch_root=batch_root,
            local_root=local_root,
            job_name=job_name,
            jobs_dir=jobs_dir,
            job_dir=job_dir,
            dataset_path=effective_dataset_path,
            worker_log_path=worker_log_path,
            metadata=metadata,
        )

    def collect(self, prepared: PreparedBatch) -> CollectedArtifacts:
        result_path = prepared.job_dir / "result.json"
        return CollectedArtifacts(
            job_dir=prepared.job_dir,
            job_result_path=result_path if result_path.exists() else None,
            metadata={
                **prepared.metadata,
                "jobDirExists": prepared.job_dir.exists(),
                "jobResultPath": str(result_path),
            },
        )
