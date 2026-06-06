from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import db_session
from app.schema.templates import TemplateCreate, TemplateRead
from app.service import template_service

router = APIRouter()


@router.post("/task-templates", response_model=TemplateRead, status_code=status.HTTP_201_CREATED)
def create_template(body: TemplateCreate, session: Session = Depends(db_session)) -> TemplateRead:
    tpl = template_service.create_template(session, body)
    return TemplateRead.model_validate(tpl)


@router.get("/task-templates")
def list_templates(session: Session = Depends(db_session)) -> dict:
    items = template_service.list_templates(session)
    return {"templates": [TemplateRead.model_validate(t).model_dump(by_alias=True) for t in items]}
