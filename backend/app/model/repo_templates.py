from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ids import new_id, now_iso
from app.model.tables import TaskTemplate


def create_template(session: Session, *, owner: str, name: str, dataset_ref: str,
                    executor_kind: str, executor_config: dict[str, Any],
                    model_profile_ref: str | None = None, note: str = "") -> TaskTemplate:
    now = now_iso()
    tpl = TaskTemplate(
        template_id=new_id("tpl"), owner=owner, name=name, dataset_ref=dataset_ref,
        executor_kind=executor_kind, executor_config=executor_config,
        model_profile_ref=model_profile_ref, note=note, created_at=now, updated_at=now,
    )
    session.add(tpl)
    return tpl


def get_template(session: Session, template_id: str) -> TaskTemplate | None:
    return session.get(TaskTemplate, template_id)


def list_templates(session: Session, *, owner: str | None = None) -> list[TaskTemplate]:
    stmt = select(TaskTemplate).order_by(TaskTemplate.created_at.desc())
    if owner is not None:
        stmt = stmt.where(TaskTemplate.owner == owner)
    return list(session.scalars(stmt))
