"""SQLAlchemy 映射。

DDL 的唯一来源是 infra/supabase/migrations/*.sql —— 这里只是它的读写映射，
不要用 metadata.create_all() 建表（会与迁移漂移）。
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# ── 枚举值（与迁移里的 PG 枚举严格对应）─────────────────────────
RUN_STATUSES = (
    "queued", "running", "waiting_for_input",
    "succeeded", "failed", "cancelled", "expired",
)
SESSION_STATUSES = ("idle", "running", "waiting_for_input", "failed", "expired")
PART_TYPES = (
    "text", "reasoning", "tool_call", "tool_result",
    "card_request", "card_response", "artifact", "error",
)
MESSAGE_ROLES = ("user", "assistant", "system")
STEP_TYPES = ("python", "interaction", "llm")
TRACKS = ("trusted", "exploratory")
CARD_STATUSES = ("pending", "answered", "expired", "cancelled")
STEP_STATUSES = (
    "pending", "running", "waiting_for_input",
    "succeeded", "failed", "skipped", "cancelled",
)


def _pg_enum(name: str, values: tuple[str, ...]) -> Enum:
    # create_type=False：类型由迁移创建，ORM 不负责建
    return Enum(*values, name=name, create_type=False, native_enum=True)


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[str] = mapped_column(Text, primary_key=True)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    source_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    registered_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True),
                                                       server_default=func.now())


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    # P2-R19 预留，v1 恒等于 user_id
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    skill_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(Text, default="新会话")
    status: Mapped[str] = mapped_column(
        _pg_enum("session_status", SESSION_STATUSES), default="idle")
    seq_counter: Mapped[int] = mapped_column(BigInteger, default=0)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True),
                                                    server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True),
                                                    server_default=func.now())
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True),
                                                           nullable=True)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(_pg_enum("message_role", MESSAGE_ROLES))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True),
                                                    server_default=func.now())


class Part(Base):
    """append-only。(session_id, seq) 唯一 —— R2 回放与 SSE 补发的全部依据。"""

    __tablename__ = "parts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"))
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(BigInteger)
    type: Mapped[str] = mapped_column(_pg_enum("part_type", PART_TYPES))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True),
                                                    server_default=func.now())


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"))
    skill_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    skill_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(_pg_enum("run_status", RUN_STATUSES),
                                        default="queued")
    current_step: Mapped[str | None] = mapped_column(Text, nullable=True)
    sandbox_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True),
                                                           nullable=True)
    ended_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True),
                                                         nullable=True)
    token_in: Mapped[int] = mapped_column(BigInteger, default=0)
    token_out: Mapped[int] = mapped_column(BigInteger, default=0)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # R0.6 复现三件套
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_hashes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True),
                                                    server_default=func.now())


class StepRun(Base):
    """R8：从失败的 step 重试，需要每步独立状态与输出。"""

    __tablename__ = "step_runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"))
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"))
    step_id: Mapped[str] = mapped_column(Text)
    idx: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(_pg_enum("step_type", STEP_TYPES))
    track: Mapped[str] = mapped_column(_pg_enum("track", TRACKS), default="trusted")
    status: Mapped[str] = mapped_column(_pg_enum("step_status", STEP_STATUSES),
                                        default="pending")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True),
                                                           nullable=True)
    ended_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True),
                                                         nullable=True)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True),
                                                    server_default=func.now())


class Card(Base):
    """R5：卡片落库，因此「隔天再打开仍在原位」是结构保证而非巧合。"""

    __tablename__ = "cards"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"))
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"))
    step_id: Mapped[str] = mapped_column(Text)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(_pg_enum("card_status", CARD_STATUSES),
                                        default="pending")
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    request_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    answered_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True),
                                                            nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True),
                                                    server_default=func.now())


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="SET NULL"), nullable=True)
    filename: Mapped[str] = mapped_column(Text)
    mime: Mapped[str] = mapped_column(String, default="application/octet-stream")
    storage_key: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    track: Mapped[str] = mapped_column(_pg_enum("track", TRACKS), default="trusted")
    preview_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True),
                                                    server_default=func.now())
