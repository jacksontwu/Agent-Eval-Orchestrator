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


@pytest.fixture
def seeded(session):
    run = repo_runs.create_run(session, template_id="tpl-1", owner="alice", display_name="R1")
    session.commit()
    batch = repo_batches.create_batch(session, run_id=run.run_id, owner="alice", executor_kind="harbor",
                                      selected_case_ids=["c1"], batch_options={}, batch_root="/tmp/b")
    session.commit()
    repo_case_runs.replace_for_batch(session, batch.batch_id, [
        {"case_id": "c1", "status": "succeeded", "score": 1.0, "metrics": {}, "artifact_index": {}, "error_text": None},
    ])
    session.commit()
    return run, batch


def test_run_detail(client, seeded):
    run, batch = seeded
    data = client.get(f"/api/eval-tasks/{run.run_id}").json()
    assert data["runId"] == run.run_id
    assert any(b["batchId"] == batch.batch_id for b in data["batches"])


def test_run_detail_404(client):
    assert client.get("/api/eval-tasks/run-nope").status_code == 404


def test_list_case_runs(client, seeded):
    run, batch = seeded
    cases = client.get(f"/api/case-runs?runId={run.run_id}").json()["caseRuns"]
    assert [c["caseId"] for c in cases] == ["c1"]


def test_get_batch(client, seeded):
    run, batch = seeded
    data = client.get(f"/api/batches/{batch.batch_id}").json()
    assert data["batchId"] == batch.batch_id and data["runId"] == run.run_id
