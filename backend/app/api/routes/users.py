from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_permission
from app.core.permissions import PermissionCode
from app.schema.users import PasswordReset, UserCreate, UserRead, UserUpdate
from app.service import rbac_service

router = APIRouter(dependencies=[Depends(require_permission(PermissionCode.USERS_MANAGE))])


@router.get("/users")
def list_users(session: Session = Depends(db_session)) -> dict:
    return {
        "users": [
            UserRead.model_validate(item).model_dump(by_alias=True)
            for item in rbac_service.list_users(session)
        ]
    }


@router.post("/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, session: Session = Depends(db_session)) -> UserRead:
    item = rbac_service.create_user(
        session,
        username=body.username,
        display_name=body.display_name,
        password=body.password,
        groups=body.groups,
    )
    return UserRead.model_validate(item)


@router.get("/users/{user_id}", response_model=UserRead)
def get_user(user_id: str, session: Session = Depends(db_session)) -> UserRead:
    return UserRead.model_validate(rbac_service.get_user_read(session, user_id))


@router.patch("/users/{user_id}", response_model=UserRead)
def update_user(user_id: str, body: UserUpdate, session: Session = Depends(db_session)) -> UserRead:
    return UserRead.model_validate(
        rbac_service.update_user(
            session,
            user_id,
            display_name=body.display_name,
            is_active=body.is_active,
            groups=body.groups,
        )
    )


@router.delete("/users/{user_id}")
def delete_user(user_id: str, session: Session = Depends(db_session)) -> dict:
    rbac_service.disable_user(session, user_id)
    return {"ok": True}


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: str, body: PasswordReset, session: Session = Depends(db_session)) -> dict:
    rbac_service.reset_password(session, user_id, body.password)
    return {"ok": True}
