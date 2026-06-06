from app.model import repo_case_runs as repo
from app.model import repo_batches, repo_runs


def test_replace_inserts_and_replaces(session):
    repo.replace_for_batch(session, "batch-1", [
        {"case_id": "c1", "status": "succeeded", "score": 1.0, "metrics": {}, "artifact_index": {}, "error_text": None},
        {"case_id": "c2", "status": "failed", "score": 0.0, "metrics": {}, "artifact_index": {}, "error_text": "boom"},
    ])
    session.commit()
    rows = repo.list_for_batch(session, "batch-1")
    assert len(rows) == 2
    assert {r.case_id for r in rows} == {"c1", "c2"}
    assert all(r.case_run_id.startswith("case-") for r in rows)

    # calling again replaces (not appends)
    repo.replace_for_batch(session, "batch-1", [
        {"case_id": "c1", "status": "succeeded", "score": 1.0, "metrics": {}, "artifact_index": {}, "error_text": None},
    ])
    session.commit()
    rows2 = repo.list_for_batch(session, "batch-1")
    assert len(rows2) == 1 and rows2[0].case_id == "c1"


def test_list_for_run(session):
    run = repo_runs.create_run(session, template_id="t", owner="a", display_name="R")
    session.commit()
    batch = repo_batches.create_batch(session, run_id=run.run_id, owner="a", executor_kind="harbor",
                                      selected_case_ids=["c1"], batch_options={}, batch_root="/tmp")
    session.commit()
    repo.replace_for_batch(session, batch.batch_id, [
        {"case_id": "c1", "status": "succeeded", "score": 1.0, "metrics": {}, "artifact_index": {}, "error_text": None},
    ])
    session.commit()
    rows = repo.list_for_run(session, run.run_id)
    assert len(rows) == 1 and rows[0].case_id == "c1"
