import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.api.deps import db_session
from app.model import repo_runs, repo_batches, repo_case_runs


@pytest.fixture
def client(session, monkeypatch):
    monkeypatch.setenv("AEO_ALLOW_NO_AUTH", "1")
    monkeypatch.delenv("AEO_TOKEN", raising=False)
    from app.core.config import get_settings
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[db_session] = lambda: session
    return TestClient(app)


def test_dashboard_tasks(client, session):
    run = repo_runs.create_run(session, template_id="tpl-1", owner="alice", display_name="R1")
    session.commit()
    batch = repo_batches.create_batch(session, run_id=run.run_id, owner="alice", executor_kind="harbor",
                                      selected_case_ids=["c1", "c2"], batch_options={}, batch_root="/tmp/b")
    session.commit()
    repo_case_runs.replace_for_batch(session, batch.batch_id, [
        {"case_id": "c1", "status": "succeeded", "score": 1.0, "metrics": {}, "artifact_index": {}, "error_text": None},
        {"case_id": "c2", "status": "failed", "score": 0.0, "metrics": {}, "artifact_index": {}, "error_text": None},
    ])
    session.commit()

    tasks = client.get("/api/dashboard/tasks").json()["tasks"]
    assert len(tasks) == 1
    task = tasks[0]
    assert task["runId"] == run.run_id
    assert task["counts"]["succeeded"] == 1
    assert task["counts"]["failed"] == 1
    assert task["status"] == "queued"


def test_dashboard_batches(client, session):
    run = repo_runs.create_run(session, template_id="tpl-1", owner="alice", display_name="R1")
    session.commit()
    batch = repo_batches.create_batch(session, run_id=run.run_id, owner="alice", executor_kind="harbor",
                                      selected_case_ids=["c1"], batch_options={}, batch_root="/tmp/b")
    session.commit()
    batches = client.get("/api/dashboard/batches").json()["batches"]
    assert any(b["batchId"] == batch.batch_id for b in batches)
