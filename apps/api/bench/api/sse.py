"""SSE 网关。

**断线补发为什么是这个顺序**（R2：不重不漏）：

    1. 先订阅 Redis，把期间到达的事件放进内存缓冲——先订阅才不会在
       第 2 步查库的窗口里漏掉新事件；
    2. 再按 Last-Event-ID 从库里读 seq > last_id 的 Part 补发；
    3. 然后把缓冲里 seq 大于「已补发最大 seq」的事件吐出去——这一步去重，
       因为步骤 1 和 2 必然有重叠；
    4. 之后转入纯实时。

反过来（先查库再订阅）会在两步之间丢事件；不去重则会重复。两个错误都
违反 R2，所以这个顺序不是实现细节，是契约。

心跳每 15 秒一次，防代理超时（PRD 6.3）。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator

from bench.db.repo.parts import PartRepo
from bench.events.bus import EventBus
from bench.events.types import Event, EventType

logger = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 15.0
#: 步骤 1 的缓冲上限。超过说明事件产生速度远快于补发，属异常，丢弃最旧的。
BUFFER_LIMIT = 1000


def part_to_event(part_type: str, part_id: str, payload: dict, seq: int) -> Event:
    return Event(
        type=EventType.PART_CREATED, seq=seq,
        data={"part_id": part_id, "type": part_type, "payload": payload},
    )


async def event_stream(
    *,
    session_id: uuid.UUID | str,
    user_id: uuid.UUID | str,
    db_factory,
    bus: EventBus,
    last_event_id: int = 0,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
) -> AsyncIterator[str]:
    session_id = str(session_id)
    buffer: list[Event] = []
    buffering = True

    subscription = bus.subscribe(session_id)

    async def pump() -> None:
        """步骤 1 起、贯穿全程的订阅泵。"""
        try:
            async for ev in subscription:
                if buffering:
                    buffer.append(ev)
                    if len(buffer) > BUFFER_LIMIT:
                        del buffer[0]
                else:
                    await live.put(ev)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("SSE 订阅泵异常 session=%s: %s", session_id, exc)

    live: asyncio.Queue[Event] = asyncio.Queue()
    pump_task = asyncio.create_task(pump())

    try:
        # ── 步骤 2：按 Last-Event-ID 从库里补发 ──────────────────
        replayed_max = last_event_id
        async with db_factory() as db:
            parts = PartRepo(db, user_id)
            rows = await parts.list_after(session_id, after_seq=last_event_id)
        for p in rows:
            yield part_to_event(p.type, str(p.id), p.payload, p.seq).to_sse()
            replayed_max = max(replayed_max, p.seq)

        # ── 步骤 3：吐缓冲，按 seq 去重 ─────────────────────────
        buffering = False
        for ev in buffer:
            # 瞬时事件没有 seq，不参与去重（它们描述此刻，补发也无意义，
            # 但缓冲期间到达的还是要送达，否则用户会看到进度卡住）
            if ev.seq is not None and ev.seq <= replayed_max:
                continue
            yield ev.to_sse()
        buffer.clear()

        # ── 步骤 4：实时 + 心跳 ─────────────────────────────────
        while True:
            try:
                ev = await asyncio.wait_for(live.get(), timeout=heartbeat_seconds)
            except asyncio.TimeoutError:
                yield Event(type=EventType.HEARTBEAT, data={}).to_sse()
                continue
            if ev.seq is not None and ev.seq <= replayed_max:
                continue
            if ev.seq is not None:
                replayed_max = max(replayed_max, ev.seq)
            yield ev.to_sse()
    finally:
        pump_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await pump_task
        with contextlib.suppress(Exception):
            await subscription.aclose()


def parse_last_event_id(raw: str | None) -> int:
    """浏览器 EventSource 会自动带上 Last-Event-ID 头。脏值一律当 0（全量回放）。"""
    if not raw:
        return 0
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(v, 0)
