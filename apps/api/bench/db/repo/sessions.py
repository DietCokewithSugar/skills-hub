from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import select, update

from bench.db.models import Session as SessionModel
from bench.db.repo.base import BaseRepo, NotFound


class SessionRepo(BaseRepo):
    async def create(self, *, skill_id: str | None = None,
                     title: str = "新会话") -> SessionModel:
        row = SessionModel(
            user_id=self.user_id,
            # P2-R19：v1 workspace_id 恒等于 user_id
            workspace_id=self.user_id,
            skill_id=skill_id,
            title=title,
            status="idle",
            seq_counter=0,
            settings={},
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def get(self, session_id: uuid.UUID | str) -> SessionModel:
        stmt = select(SessionModel).where(
            SessionModel.id == uuid.UUID(str(session_id)),
            SessionModel.user_id == self.user_id,
            SessionModel.deleted_at.is_(None),
        )
        row = await self.db.scalar(stmt)
        if row is None:
            raise NotFound(f"session {session_id} not found")
        return row

    async def list(self, *, limit: int = 50,
                   cursor: dt.datetime | None = None) -> list[SessionModel]:
        """按最近更新排序，游标分页（PRD R1：会话列表按最近更新排序）。"""
        stmt = (
            select(SessionModel)
            .where(SessionModel.user_id == self.user_id,
                   SessionModel.deleted_at.is_(None))
            .order_by(SessionModel.updated_at.desc())
            .limit(limit)
        )
        if cursor is not None:
            stmt = stmt.where(SessionModel.updated_at < cursor)
        return list((await self.db.scalars(stmt)).all())

    async def set_status(self, session_id: uuid.UUID | str, status: str) -> None:
        await self.require_session(session_id)
        await self.db.execute(
            update(SessionModel)
            .where(SessionModel.id == uuid.UUID(str(session_id)))
            .values(status=status)
        )

    async def set_title(self, session_id: uuid.UUID | str, title: str) -> None:
        await self.require_session(session_id)
        await self.db.execute(
            update(SessionModel)
            .where(SessionModel.id == uuid.UUID(str(session_id)))
            .values(title=title)
        )

    async def update_settings(self, session_id: uuid.UUID | str,
                              patch: dict[str, Any]) -> dict[str, Any]:
        """合并式更新会话设置（如 R9 的「本会话内不再询问」）。"""
        row = await self.get(session_id)
        merged = {**(row.settings or {}), **patch}
        await self.db.execute(
            update(SessionModel)
            .where(SessionModel.id == row.id)
            .values(settings=merged)
        )
        return merged

    async def soft_delete(self, session_id: uuid.UUID | str) -> None:
        """R1 验收：会话删除为软删除，30 天后清理产物文件。"""
        await self.require_session(session_id)
        await self.db.execute(
            update(SessionModel)
            .where(SessionModel.id == uuid.UUID(str(session_id)))
            .values(deleted_at=dt.datetime.now(dt.timezone.utc))
        )

    async def touch(self, session_id: uuid.UUID | str) -> None:
        await self.db.execute(
            update(SessionModel)
            .where(SessionModel.id == uuid.UUID(str(session_id)),
                   SessionModel.user_id == self.user_id)
            .values(updated_at=dt.datetime.now(dt.timezone.utc))
        )
