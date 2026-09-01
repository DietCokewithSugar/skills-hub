"""所有 repo 的共同基类。

**这是 v1 的用户隔离边界。** PRD R1 要求「用户 A 无法通过任何 API 路径读取
用户 B 的数据（含直接构造 id）」。v1 不做认证，后端以 service role 连接
Supabase，RLS 被绕过，因此隔离必须在这一层强制：

    每个 repo 都持有 user_id，每个查询都必须经过 _scope() 或 _owns()。

不要在 repo 之外直接用 session.get(Model, id) —— 那样会绕过作用域。
接上 Auth 后 RLS 成为第二道闸门，这一层保留不动。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from bench.db.models import Session as SessionModel


class NotFound(Exception):
    """找不到，或不属于当前用户 —— 对外一律 404，不泄露存在性。"""


class BaseRepo:
    def __init__(self, db: AsyncSession, user_id: uuid.UUID | str) -> None:
        self.db = db
        self.user_id = uuid.UUID(str(user_id))

    async def owns_session(self, session_id: uuid.UUID | str) -> bool:
        """当前用户是否拥有该会话（软删除的视为不存在）。"""
        stmt = select(
            exists().where(
                SessionModel.id == uuid.UUID(str(session_id)),
                SessionModel.user_id == self.user_id,
                SessionModel.deleted_at.is_(None),
            )
        )
        return bool(await self.db.scalar(stmt))

    async def require_session(self, session_id: uuid.UUID | str) -> uuid.UUID:
        sid = uuid.UUID(str(session_id))
        if not await self.owns_session(sid):
            raise NotFound(f"session {session_id} not found")
        return sid

    def _scope_by_session(self, stmt: Select[Any], model: Any) -> Select[Any]:
        """把任意子表查询收敛到当前用户拥有的会话内。"""
        return stmt.where(
            model.session_id.in_(
                select(SessionModel.id).where(
                    SessionModel.user_id == self.user_id,
                    SessionModel.deleted_at.is_(None),
                )
            )
        )
