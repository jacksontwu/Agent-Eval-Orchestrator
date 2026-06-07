from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import now_iso
from app.model.base import Base


class User(Base):
    __tablename__ = "users"
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)
    last_login_at: Mapped[str | None] = mapped_column(String, nullable=True)


class Group(Base):
    __tablename__ = "groups"
    group_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)


class Permission(Base):
    __tablename__ = "permissions"
    permission_id: Mapped[str] = mapped_column(String, primary_key=True)
    code: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")


class UserGroup(Base):
    __tablename__ = "user_groups"
    __table_args__ = (UniqueConstraint("user_id", "group_id", name="uq_user_groups_user_group"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    group_id: Mapped[str] = mapped_column(String, nullable=False, index=True)


class GroupPermission(Base):
    __tablename__ = "group_permissions"
    __table_args__ = (
        UniqueConstraint("group_id", "permission_id", name="uq_group_permissions_group_permission"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    permission_id: Mapped[str] = mapped_column(String, nullable=False, index=True)


class TaskTemplate(Base):
    __tablename__ = "task_templates"
    template_id: Mapped[str] = mapped_column(String, primary_key=True)
    owner: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    dataset_ref: Mapped[str] = mapped_column(String, nullable=False)
    executor_kind: Mapped[str] = mapped_column(String, nullable=False)
    executor_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    model_profile_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)


class Run(Base):
    __tablename__ = "runs"
    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    template_id: Mapped[str] = mapped_column(String, nullable=False)
    owner: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    bound_worker_id: Mapped[str | None] = mapped_column(String, nullable=True)
    latest_batch_id: Mapped[str | None] = mapped_column(String, nullable=True)
    parent_run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    sync_status: Mapped[str] = mapped_column(String, nullable=False, default="")
    sync_job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    sync_manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    rerun_status: Mapped[str] = mapped_column(String, nullable=False, default="idle")
    rerun_job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)


class Batch(Base):
    __tablename__ = "batches"
    batch_id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    owner: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    current_step: Mapped[str | None] = mapped_column(String, nullable=True)
    preferred_worker_id: Mapped[str | None] = mapped_column(String, nullable=True)
    assigned_worker_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    executor_kind: Mapped[str] = mapped_column(String, nullable=False)
    executor_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    selected_case_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    batch_options: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    artifact_index: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    batch_root: Mapped[str] = mapped_column(String, nullable=False)
    parent_batch_id: Mapped[str | None] = mapped_column(String, nullable=True)
    batch_kind: Mapped[str] = mapped_column(String, nullable=False, default="primary")
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)
    started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String, nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class CaseRun(Base):
    __tablename__ = "case_runs"
    case_run_id: Mapped[str] = mapped_column(String, primary_key=True)
    batch_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    case_id: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    artifact_index: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)


class Worker(Base):
    __tablename__ = "workers"
    worker_id: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    host: Mapped[str] = mapped_column(String, nullable=False)
    slots_total: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    slots_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(Integer, nullable=False, default=1)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    allocation_weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    last_heartbeat_at: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)


class AssetSyncJob(Base):
    __tablename__ = "asset_sync_jobs"
    job_id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    current_step: Mapped[str | None] = mapped_column(String, nullable=True)
    steps: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    log_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)
    finished_at: Mapped[str | None] = mapped_column(String, nullable=True)


class RunRerunJob(Base):
    __tablename__ = "run_rerun_jobs"
    job_id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    sync_job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    case_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    worker_shards: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    rerun_batches: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    selected_error_types: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)
    finished_at: Mapped[str | None] = mapped_column(String, nullable=True)
