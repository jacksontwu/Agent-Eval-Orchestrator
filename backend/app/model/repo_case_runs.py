from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.ids import new_id, now_iso
from app.model.tables import Batch, CaseRun


def replace_for_batch(session: Session, batch_id: str, cases: list[dict[str, Any]]) -> None:
    session.execute(delete(CaseRun).where(CaseRun.batch_id == batch_id))
    now = now_iso()
    for case in cases:
        session.add(CaseRun(
            case_run_id=new_id("case"), batch_id=batch_id, case_id=case["case_id"],
            status=case.get("status", "pending"), score=case.get("score"),
            metrics=case.get("metrics") or {}, artifact_index=case.get("artifact_index") or {},
            error_text=case.get("error_text"), created_at=now, updated_at=now,
        ))


def list_for_batch(session: Session, batch_id: str) -> list[CaseRun]:
    stmt = select(CaseRun).where(CaseRun.batch_id == batch_id).order_by(CaseRun.case_id)
    return list(session.scalars(stmt))


def list_for_run(session: Session, run_id: str) -> list[CaseRun]:
    stmt = (
        select(CaseRun)
        .join(Batch, Batch.batch_id == CaseRun.batch_id)
        .where(Batch.run_id == run_id)
        .order_by(CaseRun.case_id)
    )
    return list(session.scalars(stmt))
