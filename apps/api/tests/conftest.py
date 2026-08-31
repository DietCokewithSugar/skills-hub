"""测试基础设施。

需要真实 Postgres 与 Redis —— R1 的隔离、R2 的补发这类验收标准用 mock
测不出来（mock 会按你以为的方式行为，而不是按 Postgres 实际的方式）。
连不上就 skip，不静默降级。
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = REPO_ROOT / "infra" / "supabase" / "migrations"

TEST_DB_URL = os.getenv(
    "BENCH_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres@127.0.0.1:55432/bench_test",
)
TEST_REDIS_URL = os.getenv("BENCH_TEST_REDIS_URL", "redis://localhost:6379/15")


def _psql_url() -> str:
    return TEST_DB_URL.replace("postgresql+asyncpg://", "postgresql://")


def _pg_available() -> bool:
    try:
        r = subprocess.run(
            ["psql", _psql_url().rsplit("/", 1)[0] + "/postgres", "-Atc", "select 1"],
            capture_output=True, timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


def _redis_available() -> bool:
    try:
        import redis
        redis.from_url(TEST_REDIS_URL).ping()
        return True
    except Exception:
        return False


requires_pg = pytest.mark.skipif(not _pg_available(), reason="需要本地 Postgres")
requires_redis = pytest.mark.skipif(not _redis_available(), reason="需要本地 Redis")


@pytest.fixture(scope="session")
def migrated_db() -> str:
    """建库并跑全部迁移。整个测试会话建一次。"""
    if not _pg_available():
        pytest.skip("需要本地 Postgres")
    base = _psql_url().rsplit("/", 1)[0]
    dbname = _psql_url().rsplit("/", 1)[1]
    subprocess.run(["psql", f"{base}/postgres", "-Atc", f'drop database if exists "{dbname}"'],
                   capture_output=True, check=True)
    subprocess.run(["psql", f"{base}/postgres", "-Atc", f'create database "{dbname}"'],
                   capture_output=True, check=True)
    for f in sorted(MIGRATIONS.glob("*.sql")):
        r = subprocess.run(["psql", _psql_url(), "-v", "ON_ERROR_STOP=1", "-q", "-f", str(f)],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"迁移 {f.name} 失败:\n{r.stderr}"
    return TEST_DB_URL


@pytest_asyncio.fixture
async def engine(migrated_db: str):
    eng = create_async_engine(migrated_db, poolclass=None)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def db(db_factory):
    async with db_factory() as s:
        yield s
        await s.rollback()


@pytest.fixture
def user_a() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def user_b() -> uuid.UUID:
    return uuid.uuid4()


@pytest_asyncio.fixture
async def bus():
    from bench.events.bus import EventBus
    if not _redis_available():
        pytest.skip("需要本地 Redis")
    b = EventBus(TEST_REDIS_URL)
    yield b
    await b.close()
