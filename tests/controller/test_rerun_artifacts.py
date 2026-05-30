import json
from pathlib import Path

import pytest

from agent_eval_orchestrator.controller.rerun_artifacts import (
    copy_jobs_tree,
    delete_trials_for_cases,
    derived_jobs_dir_for_run,
)


def _write_trial(job_dir: Path, trial_name: str, *, task_name: str | None = None) -> None:
    trial_dir = job_dir / trial_name
    trial_dir.mkdir(parents=True)
    payload = {"trial_name": trial_name}
    if task_name is not None:
        payload["task_name"] = task_name
    (trial_dir / "result.json").write_text(json.dumps(payload), encoding="utf-8")


def test_derived_jobs_dir_for_run_uses_run_archive(store):
    template = store.create_task_template(
        owner="default",
        name="x",
        dataset_ref="/tmp/dataset",
        executor_kind="harbor-docker",
        executor_config={},
        model_profile_ref=None,
        note="",
    )
    run = store.create_run(template_id=template["template_id"], display_name="child")

    jobs_dir = derived_jobs_dir_for_run(store=store, run=run)

    assert jobs_dir == store.layout.run_dir("default", run["run_id"]) / "harbor" / "jobs"


def test_copy_jobs_tree_replaces_existing_target(tmp_path):
    source = tmp_path / "source-jobs"
    target = tmp_path / "target-jobs"
    _write_trial(source / "merged", "case-a__old")
    _write_trial(target / "stale", "case-stale__old")

    copy_jobs_tree(source, target)

    assert (target / "merged" / "case-a__old" / "result.json").exists()
    assert not (target / "stale").exists()


def test_copy_jobs_tree_rejects_same_source_and_target(tmp_path):
    source = tmp_path / "jobs"
    _write_trial(source / "merged", "case-a__old")

    with pytest.raises(RuntimeError) as exc:
        copy_jobs_tree(source, source)

    assert "must differ" in str(exc.value)
    assert (source / "merged" / "case-a__old" / "result.json").exists()


def test_copy_jobs_tree_rejects_missing_source(tmp_path):
    source = tmp_path / "missing-source"
    target = tmp_path / "target-jobs"

    with pytest.raises(RuntimeError) as exc:
        copy_jobs_tree(source, target)

    assert "source jobs directory not found" in str(exc.value)
    assert not target.exists()


def test_copy_jobs_tree_rejects_non_directory_source(tmp_path):
    source = tmp_path / "source-jobs"
    target = tmp_path / "target-jobs"
    source.write_text("not a directory", encoding="utf-8")

    with pytest.raises(RuntimeError) as exc:
        copy_jobs_tree(source, target)

    assert "source jobs directory not found" in str(exc.value)
    assert source.read_text(encoding="utf-8") == "not a directory"
    assert not target.exists()


def test_copy_jobs_tree_rejects_target_inside_source(tmp_path):
    source = tmp_path / "jobs"
    target = source / "nested-target"
    _write_trial(source / "merged", "case-a__old")

    with pytest.raises(RuntimeError) as exc:
        copy_jobs_tree(source, target)

    assert "must not overlap" in str(exc.value)
    assert (source / "merged" / "case-a__old" / "result.json").exists()


def test_copy_jobs_tree_rejects_source_inside_target(tmp_path):
    target = tmp_path / "jobs"
    source = target / "source"
    _write_trial(source / "merged", "case-a__old")

    with pytest.raises(RuntimeError) as exc:
        copy_jobs_tree(source, target)

    assert "must not overlap" in str(exc.value)
    assert (source / "merged" / "case-a__old" / "result.json").exists()


def test_copy_jobs_tree_rejects_non_directory_target(tmp_path):
    source = tmp_path / "source-jobs"
    target = tmp_path / "target-jobs"
    _write_trial(source / "merged", "case-a__old")
    target.write_text("not a directory", encoding="utf-8")

    with pytest.raises(RuntimeError) as exc:
        copy_jobs_tree(source, target)

    assert "target jobs path exists but is not a directory" in str(exc.value)
    assert target.read_text(encoding="utf-8") == "not a directory"


def test_copy_jobs_tree_rejects_symlink_target(tmp_path):
    source = tmp_path / "source-jobs"
    link_target = tmp_path / "real-target"
    target = tmp_path / "target-link"
    _write_trial(source / "merged", "case-a__old")
    link_target.mkdir()
    try:
        target.symlink_to(link_target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unsupported: {exc}")

    with pytest.raises(RuntimeError) as exc:
        copy_jobs_tree(source, target)

    assert "target jobs path exists but is not a directory" in str(exc.value)
    assert target.is_symlink()


def test_delete_trials_for_cases_missing_jobs_dir_returns_empty(tmp_path):
    removed = delete_trials_for_cases(
        jobs_dir=tmp_path / "missing",
        case_ids=["case-a"],
    )

    assert removed == []


def test_delete_trials_for_cases_removes_only_selected_trials(tmp_path):
    jobs_dir = tmp_path / "jobs"
    merged = jobs_dir / "merged-job"
    _write_trial(merged, "case-a__old", task_name="case-a")
    _write_trial(merged, "case-b__old", task_name="case-b")
    _write_trial(merged, "case-c__old")

    removed = delete_trials_for_cases(jobs_dir=jobs_dir, case_ids=["case-a", "case-c"])

    assert sorted(path.name for path in removed) == ["case-a__old", "case-c__old"]
    assert not (merged / "case-a__old").exists()
    assert (merged / "case-b__old" / "result.json").exists()
    assert not (merged / "case-c__old").exists()
