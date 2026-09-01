from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, update

from bench.db.models import Message, Part
from bench.db.models import Session as SessionModel
from bench.db.repo.base import BaseRepo, NotFound


class PartRepo(BaseRepo):
    """Part 是 append-only 的历史真相。

    seq 的分配必须是原子的：单条 UPDATE ... RETURNING 由 Postgres 保证行级
    串行化，因此并发写入不会拿到重复号。这是 R2「SSE 断线补发不重不漏」
    与「回放顺序与实时一致」的底层依据 —— 表上还有
    unique(session_id, seq) 兜底，写重了会直接报错而不是悄悄错序。
    """

    async def next_seq(self, session_id: uuid.UUID | str) -> int:
        stmt = (
            update(SessionModel)
            .where(SessionModel.id == uuid.UUID(str(session_id)))
            .values(seq_counter=SessionModel.seq_counter + 1)
            .returning(SessionModel.seq_counter)
        )
        seq = await self.db.scalar(stmt)
        if seq is None:
            raise NotFound(f"session {session_id} not found")
        return int(seq)

    async def create_message(self, session_id: uuid.UUID | str, role: str) -> Message:
        sid = await self.require_session(session_id)
        msg = Message(session_id=sid, role=role)
        self.db.add(msg)
        await self.db.flush()
        return msg

    async def append(self, *, session_id: uuid.UUID | str, message_id: uuid.UUID,
                     type: str, payload: dict[str, Any]) -> Part:
        """写一个 Part 并分配 seq。调用方负责随后发到事件总线。"""
        sid = uuid.UUID(str(session_id))
        seq = await self.next_seq(sid)
        part = Part(message_id=message_id, session_id=sid, seq=seq,
                    type=type, payload=payload)
        self.db.add(part)
        await self.db.flush()
        return part

    async def list_after(self, session_id: uuid.UUID | str, *, after_seq: int = 0,
                         limit: int = 2000) -> list[Part]:
        """回放：按 seq 取 after_seq 之后的 Part。

        既服务 GET /sessions/{id}/parts（历史回放），也服务 SSE 的
        Last-Event-ID 补发 —— 两条路径共用同一个查询，所以「回放内容与
        实时看到的完全一致」不需要额外保证。
        """
        await self.require_session(session_id)
        stmt = (
            select(Part)
            .where(Part.session_id == uuid.UUID(str(session_id)), Part.seq > after_seq)
            .order_by(Part.seq)
            .limit(limit)
        )
        return list((await self.db.scalars(stmt)).all())

    async def max_seq(self, session_id: uuid.UUID | str) -> int:
        row = await self.db.scalar(
            select(SessionModel.seq_counter).where(
                SessionModel.id == uuid.UUID(str(session_id)),
                SessionModel.user_id == self.user_id,
            )
        )
        if row is None:
            raise NotFound(f"session {session_id} not found")
        return int(row)

    async def update_payload(self, part_id: uuid.UUID, payload: dict[str, Any]) -> None:
        """就地更新 payload。

        只用于流式片段的最终定稿（reasoning / text 的 part.completed）以及
        card_request 被作答后的状态翻转 —— 历史语义不变，不新增 seq。
        """
        stmt = select(Part).where(Part.id == part_id)
        stmt = self._scope_by_session(stmt, Part)
        part = await self.db.scalar(stmt)
        if part is None:
            raise NotFound(f"part {part_id} not found")
        await self.db.execute(update(Part).where(Part.id == part_id).values(payload=payload))
