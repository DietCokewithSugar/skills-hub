"""API 的请求/响应模型。

P2-R17：「所有业务逻辑在服务端，Web 只是其中一个 client；
API 用 OpenAPI 描述并生成 SDK。」所以这些模型是对外契约，
前端的 TypeScript 类型由它们生成。
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, Field


class SkillSummary(BaseModel):
    id: str
    name: str
    version: str
    description: str = ""
    inputs: list[dict[str, Any]] = Field(default_factory=list)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    outputs: list[dict[str, Any]] = Field(default_factory=list)


class SessionCreate(BaseModel):
    skill_id: str | None = None
    title: str | None = None


class SessionOut(BaseModel):
    id: uuid.UUID
    skill_id: str | None
    title: str
    status: str
    created_at: dt.datetime
    updated_at: dt.datetime
    #: 会话列表的状态点需要知道最近一次 Run 的样子
    latest_run_id: uuid.UUID | None = None


class SessionListOut(BaseModel):
    items: list[SessionOut]
    next_cursor: dt.datetime | None = None


class PartOut(BaseModel):
    id: uuid.UUID
    seq: int
    type: str
    payload: dict[str, Any]
    created_at: dt.datetime


class PartsOut(BaseModel):
    items: list[PartOut]
    max_seq: int


class MessageCreate(BaseModel):
    text: str = ""
    #: 输入参数，key 对应 skill.yaml 的 inputs[].key；文件用已上传的文件名
    inputs: dict[str, Any] = Field(default_factory=dict)


class RunOut(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    skill_id: str | None
    skill_version: str | None
    status: str
    current_step: str | None
    error: dict[str, Any] | None = None
    token_in: int = 0
    token_out: int = 0
    started_at: dt.datetime | None = None
    ended_at: dt.datetime | None = None


class StepOut(BaseModel):
    step_id: str
    idx: int
    type: str
    track: str
    status: str
    attempt: int
    started_at: dt.datetime | None = None
    ended_at: dt.datetime | None = None
    error: dict[str, Any] | None = None


class RunDetailOut(RunOut):
    steps: list[StepOut] = Field(default_factory=list)
    pending_card_id: uuid.UUID | None = None


class CardOut(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    step_id: str
    spec: dict[str, Any]
    status: str
    response: dict[str, Any] | None = None
    expires_at: dt.datetime


class ResumeIn(BaseModel):
    card_id: uuid.UUID
    action: str = "confirm"
    values: dict[str, Any] = Field(default_factory=dict)


class ArtifactOut(BaseModel):
    id: uuid.UUID
    filename: str
    mime: str
    size_bytes: int
    track: str
    has_preview: bool = False
    created_at: dt.datetime


class UploadOut(BaseModel):
    filename: str
    size_bytes: int
    sha256: str


class ErrorOut(BaseModel):
    detail: str
    field_errors: dict[str, str] = Field(default_factory=dict)
