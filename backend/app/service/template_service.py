from __future__ import annotations

from sqlalchemy.orm import Session

from app.model import repo_templates
from app.model.tables import TaskTemplate
from app.schema.templates import TemplateCreate


def create_template(session: Session, data: TemplateCreate) -> TaskTemplate:
    tpl = repo_templates.create_template(
        session, owner=data.owner, name=data.name, dataset_ref=data.dataset_ref,
        executor_kind=data.executor_kind, executor_config=data.executor_config,
        model_profile_ref=data.model_profile_ref, note=data.note,
    )
    session.commit()
    return tpl


def list_templates(session: Session, owner: str | None = None) -> list[TaskTemplate]:
    return repo_templates.list_templates(session, owner=owner)
