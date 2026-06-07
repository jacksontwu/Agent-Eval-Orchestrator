"""add auth rbac tables

Revision ID: 0002_auth_rbac
Revises: 0001_init
Create Date: 2026-06-07 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.core.permissions import BUILTIN_GROUPS, DEFAULT_GROUP_PERMISSIONS, PERMISSION_DESCRIPTIONS

revision: str = "0002_auth_rbac"
down_revision: Union[str, None] = "0001_init"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("last_login_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)
    op.create_table(
        "groups",
        sa.Column("group_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("is_builtin", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("group_id"),
    )
    op.create_index(op.f("ix_groups_name"), "groups", ["name"], unique=True)
    op.create_table(
        "permissions",
        sa.Column("permission_id", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("permission_id"),
    )
    op.create_index(op.f("ix_permissions_code"), "permissions", ["code"], unique=True)
    op.create_table(
        "user_groups",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("group_id", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "group_id", name="uq_user_groups_user_group"),
    )
    op.create_index(op.f("ix_user_groups_user_id"), "user_groups", ["user_id"], unique=False)
    op.create_index(op.f("ix_user_groups_group_id"), "user_groups", ["group_id"], unique=False)
    op.create_table(
        "group_permissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("group_id", sa.String(), nullable=False),
        sa.Column("permission_id", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_id", "permission_id", name="uq_group_permissions_group_permission"),
    )
    op.create_index(op.f("ix_group_permissions_group_id"), "group_permissions", ["group_id"], unique=False)
    op.create_index(op.f("ix_group_permissions_permission_id"), "group_permissions", ["permission_id"], unique=False)

    permissions = []
    for index, (code, description) in enumerate(PERMISSION_DESCRIPTIONS.items(), start=1):
        permissions.append({"permission_id": f"perm-seed-{index:03d}", "code": code, "description": description})
    op.bulk_insert(
        sa.table("permissions", sa.column("permission_id"), sa.column("code"), sa.column("description")),
        permissions,
    )

    groups = []
    for index, (name, meta) in enumerate(BUILTIN_GROUPS.items(), start=1):
        groups.append(
            {
                "group_id": f"grp-seed-{index:03d}",
                "name": name,
                "display_name": meta["display_name"],
                "description": meta["description"],
                "is_builtin": True,
                "is_active": True,
                "created_at": "2026-06-07T00:00:00Z",
                "updated_at": "2026-06-07T00:00:00Z",
            }
        )
    op.bulk_insert(
        sa.table(
            "groups",
            sa.column("group_id"),
            sa.column("name"),
            sa.column("display_name"),
            sa.column("description"),
            sa.column("is_builtin"),
            sa.column("is_active"),
            sa.column("created_at"),
            sa.column("updated_at"),
        ),
        groups,
    )

    permission_ids = {item["code"]: item["permission_id"] for item in permissions}
    group_ids = {item["name"]: item["group_id"] for item in groups}
    group_permissions = []
    counter = 1
    for group_name, codes in DEFAULT_GROUP_PERMISSIONS.items():
        for code in codes:
            group_permissions.append(
                {
                    "id": counter,
                    "group_id": group_ids[group_name],
                    "permission_id": permission_ids[code],
                }
            )
            counter += 1
    op.bulk_insert(
        sa.table("group_permissions", sa.column("id"), sa.column("group_id"), sa.column("permission_id")),
        group_permissions,
    )


def downgrade() -> None:
    op.drop_table("group_permissions")
    op.drop_table("user_groups")
    op.drop_index(op.f("ix_permissions_code"), table_name="permissions")
    op.drop_table("permissions")
    op.drop_index(op.f("ix_groups_name"), table_name="groups")
    op.drop_table("groups")
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_table("users")
