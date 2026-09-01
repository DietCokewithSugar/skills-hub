from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import select, update

from bench.db.models import Card
from bench.db.repo.base import BaseRepo, NotFound


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class CardRepo(BaseRepo):
    async def create(self, *, run_id: uuid.UUID, session_id: uuid.UUID, step_id: str,
                     spec: dict[str, Any], timeout_hours: int,
                     request_seq: int | None = None) -> Card:
        card = Card(
            run_id=run_id, session_id=session_id, step_id=step_id, spec=spec,
            status="pending", request_seq=request_seq,
            expires_at=_now() + dt.timedelta(hours=timeout_hours),
        )
        self.db.add(card)
        await self.db.flush()
        return card

    async def get(self, card_id: uuid.UUID | str) -> Card:
        stmt = select(Card).where(Card.id == uuid.UUID(str(card_id)))
        stmt = self._scope_by_session(stmt, Card)
        card = await self.db.scalar(stmt)
        if card is None:
            raise NotFound(f"card {card_id} not found")
        return card

    async def pending_for_run(self, run_id: uuid.UUID | str) -> Card | None:
        stmt = (
            select(Card)
            .where(Card.run_id == uuid.UUID(str(run_id)), Card.status == "pending")
            .order_by(Card.created_at.desc()).limit(1)
        )
        stmt = self._scope_by_session(stmt, Card)
        return await self.db.scalar(stmt)

    async def answer(self, card_id: uuid.UUID | str,
                     response: dict[str, Any]) -> Card:
        card = await self.get(card_id)
        if card.status != "pending":
            raise ValueError(f"card 已是 {card.status} 状态，不能重复作答")
        await self.db.execute(
            update(Card).where(Card.id == card.id)
            .values(status="answered", response=response, answered_at=_now())
        )
        await self.db.refresh(card)
        return card

    async def expire(self, card_id: uuid.UUID | str) -> None:
        await self.db.execute(
            update(Card)
            .where(Card.id == uuid.UUID(str(card_id)), Card.status == "pending")
            .values(status="expired")
        )

    async def cancel_pending_for_run(self, run_id: uuid.UUID | str) -> None:
        await self.db.execute(
            update(Card)
            .where(Card.run_id == uuid.UUID(str(run_id)), Card.status == "pending")
            .values(status="cancelled")
        )


async def sweep_expired(db: Any, now: dt.datetime | None = None) -> list[Card]:
    """跨用户扫描超时卡片，供 ARQ cron 调用（R5：超过 timeout_hours 置 expired）。

    这是唯一不带 user 作用域的查询：它是平台侧维护任务，不响应用户请求。
    """
    now = now or _now()
    rows = list((await db.scalars(
        select(Card).where(Card.status == "pending", Card.expires_at <= now)
    )).all())
    for c in rows:
        await db.execute(update(Card).where(Card.id == c.id).values(status="expired"))
    return rows
