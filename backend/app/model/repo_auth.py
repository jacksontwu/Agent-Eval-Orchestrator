from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.ids import new_id, now_iso
from app.core.permissions import BUILTIN_GROUPS, DEFAULT_GROUP_PERMISSIONS, PERMISSION_DESCRIPTIONS
from app.model.tables import Group, GroupPermission, Permission, User, UserGroup


def bootstrap_rbac(session: Session) -> None:
    permissions_by_code = {p.code: p for p in session.scalars(select(Permission)).all()}
    for code, description in PERMISSION_DESCRIPTIONS.items():
        if code not in permissions_by_code:
            session.add(Permission(permission_id=new_id("perm"), code=code, description=description))
    session.flush()

    groups_by_name = {g.name: g for g in session.scalars(select(Group)).all()}
    for name, meta in BUILTIN_GROUPS.items():
        group = groups_by_name.get(name)
        if group is None:
            group = Group(
                group_id=new_id("grp"),
                name=name,
                display_name=meta["display_name"],
                description=meta["description"],
                is_builtin=True,
                is_active=True,
            )
            session.add(group)
        else:
            group.is_builtin = True
            group.is_active = True
    session.flush()

    for group_name, permission_codes in DEFAULT_GROUP_PERMISSIONS.items():
        group = get_group_by_name(session, group_name)
        assert group is not None
        set_group_permissions(session, group.group_id, permission_codes)


def list_permissions(session: Session) -> list[Permission]:
    return list(session.scalars(select(Permission).order_by(Permission.code)).all())


def list_groups(session: Session, *, include_inactive: bool = True) -> list[Group]:
    stmt = select(Group).order_by(Group.name)
    if not include_inactive:
        stmt = stmt.where(Group.is_active.is_(True))
    return list(session.scalars(stmt).all())


def get_group(session: Session, group_id: str) -> Group | None:
    return session.get(Group, group_id)


def get_group_by_name(session: Session, name: str) -> Group | None:
    return session.scalar(select(Group).where(Group.name == name))


def get_user(session: Session, user_id: str) -> User | None:
    return session.get(User, user_id)


def get_user_by_username(session: Session, username: str) -> User | None:
    return session.scalar(select(User).where(User.username == username))


def list_users(session: Session, *, include_inactive: bool = True) -> list[User]:
    stmt = select(User).order_by(User.username)
    if not include_inactive:
        stmt = stmt.where(User.is_active.is_(True))
    return list(session.scalars(stmt).all())


def create_group(session: Session, *, name: str, display_name: str, description: str) -> Group:
    group = Group(
        group_id=new_id("grp"),
        name=name,
        display_name=display_name,
        description=description,
        is_builtin=False,
        is_active=True,
    )
    session.add(group)
    session.flush()
    return group


def create_user(
    session: Session,
    *,
    username: str,
    display_name: str,
    password_hash: str,
    group_names: list[str],
) -> User:
    user = User(
        user_id=new_id("usr"),
        username=username,
        display_name=display_name,
        password_hash=password_hash,
        is_active=True,
    )
    session.add(user)
    session.flush()
    set_user_groups(session, user.user_id, group_names)
    return user


def set_user_groups(session: Session, user_id: str, group_names: list[str]) -> None:
    groups = list(
        session.scalars(select(Group).where(Group.name.in_(group_names), Group.is_active.is_(True))).all()
    )
    found = {group.name for group in groups}
    missing = sorted(set(group_names) - found)
    if missing:
        raise ValueError(f"unknown or inactive groups: {', '.join(missing)}")
    session.execute(delete(UserGroup).where(UserGroup.user_id == user_id))
    for group in groups:
        session.add(UserGroup(user_id=user_id, group_id=group.group_id))
    session.flush()


def set_group_permissions(session: Session, group_id: str, permission_codes: list[str]) -> None:
    permissions = list(session.scalars(select(Permission).where(Permission.code.in_(permission_codes))).all())
    found = {permission.code for permission in permissions}
    missing = sorted(set(permission_codes) - found)
    if missing:
        raise ValueError(f"unknown permissions: {', '.join(missing)}")
    session.execute(delete(GroupPermission).where(GroupPermission.group_id == group_id))
    for permission in permissions:
        session.add(GroupPermission(group_id=group_id, permission_id=permission.permission_id))
    session.flush()


def group_names_for_user(session: Session, user_id: str) -> list[str]:
    stmt = (
        select(Group.name)
        .join(UserGroup, UserGroup.group_id == Group.group_id)
        .where(UserGroup.user_id == user_id, Group.is_active.is_(True))
        .order_by(Group.name)
    )
    return list(session.scalars(stmt).all())


def permissions_for_group(session: Session, group_name: str) -> set[str]:
    stmt = (
        select(Permission.code)
        .join(GroupPermission, GroupPermission.permission_id == Permission.permission_id)
        .join(Group, Group.group_id == GroupPermission.group_id)
        .where(Group.name == group_name, Group.is_active.is_(True))
    )
    return set(session.scalars(stmt).all())


def permissions_for_user(session: Session, user_id: str) -> set[str]:
    stmt = (
        select(Permission.code)
        .join(GroupPermission, GroupPermission.permission_id == Permission.permission_id)
        .join(Group, Group.group_id == GroupPermission.group_id)
        .join(UserGroup, UserGroup.group_id == Group.group_id)
        .where(UserGroup.user_id == user_id, Group.is_active.is_(True))
    )
    return set(session.scalars(stmt).all())


def touch_user_login(session: Session, user_id: str) -> None:
    user = get_user(session, user_id)
    if user is not None:
        user.last_login_at = now_iso()
