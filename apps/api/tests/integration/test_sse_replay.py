"""R2 验收：历史回放与 SSE 断线补发。

    - Given 执行中断网 30 秒，When 网络恢复，Then 补齐期间事件且顺序正确
    - 回放内容与实时看到的完全一致，不省略推理片段与失败记录
"""

from __future__ import annotations

import asyncio
import json

import pytest

from bench.api.sse import event_stream, parse_last_event_id
from bench.db.repo.parts import PartRepo
from bench.db.repo.sessions import SessionRepo
from bench.events.emitter import Emitter
from tests.conftest import requires_pg, requires_redis

pytestmark = [requires_pg, requires_redis, pytest.mark.asyncio]


def _parse(chunk: str) -> tuple[int | None, str, dict]:
    sid, etype, data = None, "", {}
    for line in chunk.strip().splitlines():
        if line.startswith("id: "):
            sid = int(line[4:])
        elif line.startswith("event: "):
            etype = line[7:]
        elif line.startswith("data: "):
            data = json.loads(line[6:])
    return sid, etype, data


async def _drain(gen, n, timeout=6.0):
    """取 n 条非心跳事件。"""
    out = []
    async def go():
        async for chunk in gen:
            seq, etype, data = _parse(chunk)
            if etype == "heartbeat":
                continue
            out.append((seq, etype, data))
            if len(out) >= n:
                return
    await asyncio.wait_for(go(), timeout=timeout)
    return out


@requires_pg
@requires_redis
async def test_seq_is_monotonic_and_unique(db, user_a):
    """seq 在会话内单调递增 —— SSE 补发的全部依据。"""
    sessions = SessionRepo(db, user_a)
    s = await sessions.create(skill_id="hello-world")
    await db.commit()

    parts = PartRepo(db, user_a)
    msg = await parts.create_message(s.id, "assistant")
    seqs = []
    for i in range(20):
        p = await parts.append(session_id=s.id, message_id=msg.id,
                               type="text", payload={"text": f"第{i}条"})
        seqs.append(p.seq)
    await db.commit()

    assert seqs == sorted(seqs), "seq 必须单调"
    assert len(set(seqs)) == len(seqs), "seq 必须唯一"
    assert seqs == list(range(1, 21)), "seq 从 1 连续递增"


@requires_pg
@requires_redis
async def test_concurrent_seq_allocation_has_no_duplicates(db_factory, user_a):
    """并发写入不会拿到重复 seq —— 靠 UPDATE...RETURNING 的行锁，不是靠运气。"""
    async with db_factory() as db:
        s = await SessionRepo(db, user_a).create()
        await db.commit()
        sid = s.id

    async def writer(n: int):
        async with db_factory() as d:
            parts = PartRepo(d, user_a)
            msg = await parts.create_message(sid, "assistant")
            got = []
            for i in range(10):
                p = await parts.append(session_id=sid, message_id=msg.id,
                                       type="text", payload={"w": n, "i": i})
                got.append(p.seq)
            await d.commit()
            return got

    results = await asyncio.gather(*(writer(i) for i in range(5)))
    allseq = [s for r in results for s in r]
    assert len(set(allseq)) == 50, f"50 次并发写出现重复 seq: {len(set(allseq))}"
    assert sorted(allseq) == list(range(1, 51))


@requires_pg
@requires_redis
async def test_reconnect_backfills_without_gap_or_duplicate(db_factory, bus, user_a):
    """断线重连：补齐期间事件，顺序正确，不重不漏。"""
    async with db_factory() as db:
        s = await SessionRepo(db, user_a).create()
        await db.commit()
        sid = str(s.id)

    # 第一段连接：收到 seq 1..3
    async with db_factory() as db:
        em = Emitter(db, sid, user_a, bus=bus)
        await em.open_message()
        for i in range(1, 4):
            await em.text(f"连接前 {i}")

    gen = event_stream(session_id=sid, user_id=user_a, db_factory=db_factory,
                       bus=bus, last_event_id=0, heartbeat_seconds=0.2)
    first = await _drain(gen, 3)
    await gen.aclose()
    assert [e[0] for e in first] == [1, 2, 3]
    last_seen = first[-1][0]

    # ── 断网期间：产生 seq 4..8，客户端完全没收到 ──
    async with db_factory() as db:
        em = Emitter(db, sid, user_a, bus=bus)
        await em.open_message()
        await em.reasoning("断网期间的推理片段")      # 4
        await em.text("断网期间的正文")                # 5
        await em.error(step_id="compute", message="断网期间的失败")  # 6
        await em.text("断网期间之七")                  # 7
        await em.text("断网期间之八")                  # 8

    # ── 重连，携带 Last-Event-ID = 3 ──
    gen2 = event_stream(session_id=sid, user_id=user_a, db_factory=db_factory,
                        bus=bus, last_event_id=parse_last_event_id(str(last_seen)),
                        heartbeat_seconds=0.2)
    back = await _drain(gen2, 5)
    await gen2.aclose()

    seqs = [e[0] for e in back]
    assert seqs == [4, 5, 6, 7, 8], f"补发应恰好是 4..8，实际 {seqs}"
    types = [e[2]["type"] for e in back]
    assert types == ["reasoning", "text", "error", "text", "text"], (
        f"推理片段与失败记录都必须在回放里（失败也是历史的一部分），实际 {types}"
    )


@requires_pg
@requires_redis
async def test_live_events_during_backfill_are_not_duplicated(db_factory, bus, user_a):
    """补发窗口内到达的实时事件不会重复送达。

    这是「先订阅、再查库、然后按 seq 去重」那套顺序真正要防的东西。
    """
    async with db_factory() as db:
        s = await SessionRepo(db, user_a).create()
        await db.commit()
        sid = str(s.id)
    async with db_factory() as db:
        em = Emitter(db, sid, user_a, bus=bus)
        await em.open_message()
        for i in range(1, 4):
            await em.text(f"历史 {i}")

    gen = event_stream(session_id=sid, user_id=user_a, db_factory=db_factory,
                       bus=bus, last_event_id=0, heartbeat_seconds=0.2)

    collected: list[tuple] = []
    async def consume():
        async for chunk in gen:
            seq, etype, data = _parse(chunk)
            if etype == "heartbeat":
                continue
            collected.append((seq, data["payload"].get("text")))
            if len(collected) >= 5:
                return

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)          # 让补发跑起来
    async with db_factory() as db:      # 补发窗口内并发写入
        em = Emitter(db, sid, user_a, bus=bus)
        await em.open_message()
        await em.text("并发 4")
        await em.text("并发 5")

    await asyncio.wait_for(task, timeout=6.0)
    await gen.aclose()

    seqs = [c[0] for c in collected]
    assert seqs == [1, 2, 3, 4, 5], f"不重不漏，实际 {seqs}"
    assert len(set(seqs)) == len(seqs), "出现重复投递"
