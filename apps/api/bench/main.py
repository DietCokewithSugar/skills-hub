"""FastAPI 应用入口。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from bench.api.routes import router
from bench.config import audit_startup, get_settings
from bench.db.repo.base import NotFound
from bench.db.session import dispose_engine
from bench.events.bus import reset_bus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    # 启动自检：把配置上的安全缺口大声说出来（见 config.audit_startup）
    audit_startup(s)
    try:
        app.state.queue = await create_pool(RedisSettings.from_dsn(s.redis_url))
    except Exception as exc:  # noqa: BLE001
        # 队列连不上时不假装能跑 —— 路由会返回 503，比静默同步执行诚实
        logger.error("无法连接任务队列（%s）：%s", s.redis_url, exc)
        app.state.queue = None
    yield
    if app.state.queue is not None:
        await app.state.queue.aclose()
    await reset_bus()
    await dispose_engine()


app = FastAPI(
    title="Bench（工位）API",
    description="在线 Skill 执行平台。P2-R17：Web 只是其中一个 client，"
                "所有业务逻辑在服务端，此文档即 SDK 生成源。",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # SSE 的 Last-Event-ID 要能被前端读到
    expose_headers=["Last-Event-ID"],
)

app.include_router(router)


@app.exception_handler(NotFound)
async def _not_found(_: Request, exc: NotFound) -> JSONResponse:
    """不存在与无权限返回同一种错误，不泄露存在性（R1）。"""
    return JSONResponse(status_code=status.HTTP_404_NOT_FOUND,
                        content={"detail": str(exc)})


@app.get("/health", tags=["meta"])
async def health() -> dict[str, object]:
    s = get_settings()
    return {
        "ok": True,
        "sandbox": s.sandbox,
        "model": s.llm_model,
        "warnings": audit_startup(s),
    }
