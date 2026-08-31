"""FastAPI 依赖。

**认证**：v1 不做（PRD 第九章列为待定的阻塞问题）。current_user 固定返回
配置里的 dev 用户。

结构上留好了口子：所有路由只依赖 `CurrentUser`，接上 Supabase Auth 时
把 `current_user` 换成「验 JWT 取 sub」即可，路由与 repo 都不用改。
用户隔离目前由 repo 层的 user_id 作用域强制（见 db/repo/base.py），
RLS 策略也已写好，等 Auth 落地即成为第二道闸门。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from bench.config import get_settings
from bench.db.session import get_session_factory
from bench.skills.registry import SkillRegistry, get_registry


async def get_db() -> AsyncIterator[AsyncSession]:
    async with get_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def current_user(
    x_bench_user: Annotated[str | None, Header(alias="X-Bench-User")] = None,
) -> uuid.UUID:
    """当前用户。

    v1 无认证：默认取配置里的 dev 用户。X-Bench-User 头允许本地测试
    多用户隔离行为（生产接上 Auth 后这个头会被忽略）。
    """
    raw = x_bench_user or get_settings().dev_user_id
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="用户标识不是合法 UUID") from None


def registry() -> SkillRegistry:
    return get_registry()


async def get_queue(request: Request):
    """ARQ 队列。取不到时抛 503 而不是静默同步执行 —— 静默降级会让
    「提交后没反应」变成一个很难查的问题。"""
    pool = getattr(request.app.state, "queue", None)
    if pool is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="任务队列不可用，请检查 Redis 连接")
    return pool


CurrentUser = Annotated[uuid.UUID, Depends(current_user)]
Db = Annotated[AsyncSession, Depends(get_db)]
Registry = Annotated[SkillRegistry, Depends(registry)]
Queue = Annotated[object, Depends(get_queue)]
