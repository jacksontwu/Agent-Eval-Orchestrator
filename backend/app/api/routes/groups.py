from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_permission
from app.core.permissions import PermissionCode
from app.schema.groups import GroupCreate, GroupRead, GroupUpdate, PermissionAssignment, PermissionRead
from app.service import rbac_service

router = APIRouter(dependencies=[Depends(require_permission(PermissionCode.GROUPS_MANAGE))])


@router.get("/groups")
def list_groups(session: Session = Depends(db_session)) -> dict:
    return {
        "groups": [
            GroupRead.model_validate(item).model_dump(by_alias=True)
            for item in rbac_service.list_groups(session)
        ]
    }


@router.post("/groups", response_model=GroupRead, status_code=status.HTTP_201_CREATED)
def create_group(body: GroupCreate, session: Session = Depends(db_session)) -> GroupRead:
    return GroupRead.model_validate(
        rbac_service.create_group(
            session,
            name=body.name,
            display_name=body.display_name,
            description=body.description,
        )
    )


@router.get("/groups/{group_id}", response_model=GroupRead)
def get_group(group_id: str, session: Session = Depends(db_session)) -> GroupRead:
    return GroupRead.model_validate(rbac_service.get_group_read(session, group_id))


@router.patch("/groups/{group_id}", response_model=GroupRead)
def update_group(group_id: str, body: GroupUpdate, session: Session = Depends(db_session)) -> GroupRead:
    return GroupRead.model_validate(
        rbac_service.update_group(
            session,
            group_id,
            display_name=body.display_name,
            description=body.description,
            is_active=body.is_active,
        )
    )


@router.delete("/groups/{group_id}")
def delete_group(group_id: str, session: Session = Depends(db_session)) -> dict:
    rbac_service.disable_group(session, group_id)
    return {"ok": True}


@router.get("/permissions")
def list_permissions(session: Session = Depends(db_session)) -> dict:
    return {
        "permissions": [
            PermissionRead.model_validate(item).model_dump(by_alias=True)
            for item in rbac_service.list_permissions(session)
        ]
    }


@router.put("/groups/{group_id}/permissions", response_model=GroupRead)
def set_permissions(group_id: str, body: PermissionAssignment, session: Session = Depends(db_session)) -> GroupRead:
    return GroupRead.model_validate(rbac_service.set_group_permissions(session, group_id, body.permissions))
