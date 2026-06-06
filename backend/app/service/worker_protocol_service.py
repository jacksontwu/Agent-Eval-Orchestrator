from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.ids import now_iso
from app.model import repo_batches, repo_case_runs, repo_workers
from app.model.tables import Worker
from app.schema.worker_protocol import HeartbeatRequest, RegisterRequest


def register(session: Session, req: RegisterRequest) -> Worker:
    worker = repo_workers.upsert_worker(
        session, worker_id=req.worker_id, display_name=req.display_name, host=req.host,
        slots_total=req.slots_total, capabilities=req.capabilities,
    )
    session.commit()
    return worker


def heartbeat(session: Session, req: HeartbeatRequest) -> None:
    repo_workers.update_runtime(
        session, req.worker_id,
        slots_used=req.slots_used,
        status="online" if req.status in (None, "online") else None,
        last_heartbeat_at=now_iso(),
    )
    if req.batch_id:
        if req.status and req.status != "online":
            current_step = None
            finished_at = now_iso() if req.finished else None
            repo_batches.set_status(
                session, req.batch_id, req.status,
                current_step=current_step, error_text=req.error_text, finished_at=finished_at,
            )
        if req.cases is not None:
            repo_case_runs.replace_for_batch(session, req.batch_id, _to_case_rows(req.cases))
        if req.summary is not None:
            repo_batches.set_summary(session, req.batch_id, req.summary, {})
    session.commit()


def _to_case_rows(cases: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for case in cases:
        rows.append({
            "case_id": case.get("caseId") or case.get("case_id"),
            "status": case.get("status", "pending"),
            "score": case.get("score"),
            "metrics": case.get("metrics") or {},
            "artifact_index": case.get("artifactIndex") or case.get("artifact_index") or {},
            "error_text": case.get("errorText") or case.get("error_text"),
        })
    return rows
