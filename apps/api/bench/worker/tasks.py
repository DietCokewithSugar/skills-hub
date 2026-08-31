"""ARQ worker。

三类任务：
  · execute_run   —— 跑一次 Run（或从某一步续跑 / 重试）
  · sweep_cards   —— cron：把超时未作答的卡片置 expired（R5）
  · cleanup       —— cron：清理超过保留期的软删除会话产物（R1）
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid

from arq.connections import RedisSettings

from bench.config import audit_startup, get_settings
from bench.db.session import db_session, dispose_engine
from bench.events.types import EventType

logger = logging.getLogger(__name__)


async def execute_run(_ctx, run_id: str, user_id: str, *,
                      start_index: int = 0) -> str:
    """跑一次 Run。"""
    from bench.orchestrator.loop import build_context, execute_run as drive

    async with db_session() as db:
        ctx = await build_context(db, run_id=uuid.UUID(run_id),
                                  user_id=uuid.UUID(user_id))
        status = await drive(ctx, start_index=start_index)
        logger.info("run %s 结束于 %s", run_id, status)
        return status


async def sweep_cards(_ctx) -> int:
    """R5 验收：卡片超过 timeout_hours 未作答，Run 置为 expired 并可一键重启。

    「我希望会话被标记为已挂起而不是永久卡住」——用户故事 11。
    """
    from bench.db.repo.cards import sweep_expired
    from bench.db.repo.runs import RunRepo
    from bench.db.repo.sessions import SessionRepo
    from bench.events.bus import get_bus
    from bench.events.types import Event

    bus = get_bus()
    n = 0
    async with db_session() as db:
        for card in await sweep_expired(db):
            # 平台侧维护任务，不带用户作用域；用卡片自己的 session 反查 owner
            from sqlalchemy import select

            from bench.db.models import Session as SM
            owner = await db.scalar(select(SM.user_id).where(SM.id == card.session_id))
            if owner is None:
                continue
            await RunRepo(db, owner).set_status(
                card.run_id, "expired", current_step=card.step_id,
                error={"kind": "card_timeout", "step_id": card.step_id,
                       "message": "这一步等待超过时限已挂起",
                       "retryable": True})
            await SessionRepo(db, owner).set_status(card.session_id, "expired")
            await db.commit()
            await bus.publish(str(card.session_id), Event(
                type=EventType.CARD_EXPIRED,
                data={"card_id": str(card.id), "step_id": card.step_id,
                      "run_id": str(card.run_id)}))
            await bus.publish(str(card.session_id), Event(
                type=EventType.RUN_EXPIRED,
                data={"run_id": str(card.run_id), "step_id": card.step_id}))
            n += 1
    if n:
        logger.info("挂起了 %s 张超时卡片", n)
    return n


async def cleanup_deleted_sessions(_ctx) -> int:
    """R1 验收：会话删除为软删除，30 天后清理产物文件。"""
    from sqlalchemy import select

    from bench.db.models import Artifact
    from bench.db.models import Session as SM
    from bench.storage.base import get_store

    s = get_settings()
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(
        days=s.artifact_retention_days)
    store = get_store()
    n = 0
    async with db_session() as db:
        stale = list((await db.scalars(
            select(SM.id).where(SM.deleted_at.is_not(None), SM.deleted_at < cutoff)
        )).all())
        for sid in stale:
            keys = list((await db.scalars(
                select(Artifact.storage_key).where(Artifact.session_id == sid)
            )).all())
            if keys:
                await store.delete(keys)
                n += len(keys)
    if n:
        logger.info("清理了 %s 个过期产物", n)
    return n


async def startup(_ctx) -> None:
    audit_startup()
    logger.info("worker 就绪（沙箱=%s）", get_settings().sandbox)


async def shutdown(_ctx) -> None:
    from bench.events.bus import reset_bus
    await reset_bus()
    await dispose_engine()


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


class WorkerSettings:
    functions = [execute_run]
    cron_jobs = []          # 在下面按需装配
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 10
    # 一个 Run 可能跑满 300s 沙箱 + 上传，给足余量
    job_timeout = 900

    @property
    def redis_settings(self) -> RedisSettings:  # pragma: no cover
        return _redis_settings()


def _build_cron():
    from arq import cron
    return [
        # 每 10 分钟扫一次超时卡片。卡片超时是小时级的，不必更频繁。
        cron(sweep_cards, minute={0, 10, 20, 30, 40, 50}, run_at_startup=True),
        # 每天凌晨清理过期产物
        cron(cleanup_deleted_sessions, hour={3}, minute={17}),
    ]


WorkerSettings.cron_jobs = _build_cron()
WorkerSettings.redis_settings = _redis_settings()
