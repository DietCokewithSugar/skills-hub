"""事件总线：Redis pub/sub 扇出 + 数据库回补。

分工很清楚，值得说明白：

  * **Redis 只负责「现在」**——把事件推给此刻连着的 SSE 连接。它不是
    存储，丢一条不影响正确性。
  * **Postgres 的 parts 表负责「历史」**——所有非瞬时事件都先落库拿到
    seq，再发 Redis。

因此 R2「断网 30 秒后补齐期间事件且顺序正确」不依赖 Redis 的可靠投递：
重连时按 Last-Event-ID 从库里读，Redis 期间推了什么无关紧要。这也是
为什么落库必须先于发布 —— 反过来会出现「客户端收到了 seq=5，但库里
还没有 5」的窗口，重连补发就会漏。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

import redis.asyncio as aioredis

from bench.config import get_settings
from bench.events.types import Event

logger = logging.getLogger(__name__)

CHANNEL_PREFIX = "bench:session:"


def channel_for(session_id: str) -> str:
    return f"{CHANNEL_PREFIX}{session_id}"


class EventBus:
    def __init__(self, redis_url: str | None = None) -> None:
        self._url = redis_url or get_settings().redis_url
        self._redis: aioredis.Redis | None = None

    async def connect(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(self._url, decode_responses=True)
        return self._redis

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def publish(self, session_id: str, event: Event) -> None:
        """发布事件。

        发布失败只记日志不抛 —— 事件总线是尽力投递的实时通道，
        它挂了不该让一次正在成功的执行失败。历史已经在库里了。
        """
        event.session_id = session_id
        try:
            r = await self.connect()
            await r.publish(channel_for(session_id), event.to_json())
        except Exception as exc:  # noqa: BLE001
            logger.warning("事件发布失败 session=%s type=%s: %s",
                           session_id, event.type, exc)

    async def subscribe(self, session_id: str) -> AsyncIterator[Event]:
        """订阅某会话的实时事件流。"""
        r = await self.connect()
        pubsub = r.pubsub(ignore_subscribe_messages=True)
        await pubsub.subscribe(channel_for(session_id))
        try:
            while True:
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if msg is None:
                    await asyncio.sleep(0)
                    continue
                data = msg.get("data")
                if not data:
                    continue
                try:
                    yield Event.from_json(data)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("事件反序列化失败: %s", exc)
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(channel_for(session_id))
                await pubsub.aclose()


_bus: EventBus | None = None


def get_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


async def reset_bus() -> None:
    global _bus
    if _bus is not None:
        await _bus.close()
    _bus = None
