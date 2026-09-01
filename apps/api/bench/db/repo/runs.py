from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import select, update

from bench.db.models import Run
from bench.db.repo.base import BaseRepo, NotFound

TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "expired"})


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class RunRepo(BaseRepo):
    async def create(self, *, session_id: uuid.UUID | str, skill_id: str | None,
                     skill_version: str | None, model: str | None,
                     prompt_version: str | None,
                     input_hashes: dict[str, Any] | None = None) -> Run:
        sid = await self.require_session(session_id)
        run = Run(
            session_id=sid, skill_id=skill_id, skill_version=skill_version,
            status="queued", model=model, prompt_version=prompt_version,
            # R0.6：模型版本、prompt 版本、skill 版本、输入哈希随每次 Run 记录
            input_hashes=input_hashes or {},
        )
        self.db.add(run)
        await self.db.flush()
        return run

    async def get(self, run_id: uuid.UUID | str) -> Run:
        stmt = select(Run).where(Run.id == uuid.UUID(str(run_id)))
        stmt = self._scope_by_session(stmt, Run)
        run = await self.db.scalar(stmt)
        if run is None:
            raise NotFound(f"run {run_id} not found")
        return run

    async def latest_for_session(self, session_id: uuid.UUID | str) -> Run | None:
        await self.require_session(session_id)
        stmt = (
            select(Run)
            .where(Run.session_id == uuid.UUID(str(session_id)))
            .order_by(Run.created_at.desc())
            .limit(1)
        )
        return await self.db.scalar(stmt)

    async def set_status(self, run_id: uuid.UUID | str, status: str, *,
                         current_step: str | None = None,
                         error: dict[str, Any] | None = None) -> None:
        values: dict[str, Any] = {"status": status}
        if current_step is not None:
            values["current_step"] = current_step
        if error is not None:
            values["error"] = error
        if status == "running":
            values["started_at"] = _now()
        if status in TERMINAL_STATUSES:
            values["ended_at"] = _now()
        await self.db.execute(
            update(Run).where(Run.id == uuid.UUID(str(run_id))).values(**values)
        )

    async def request_cancel(self, run_id: uuid.UUID | str) -> Run:
        """协作式取消：置标志，由沙箱与编排循环在检查点观测。

        R8 要求「执行中可取消，沙箱在 5 秒内被销毁」——标志由
        orchestrator/loop.py 的 cancel watcher 每秒轮询，命中即 kill 沙箱。
        """
        run = await self.get(run_id)
        if run.status in TERMINAL_STATUSES:
            return run
        await self.db.execute(
            update(Run).where(Run.id == run.id).values(cancel_requested=True)
        )
        return run

    async def is_cancel_requested(self, run_id: uuid.UUID | str) -> bool:
        return bool(await self.db.scalar(
            select(Run.cancel_requested).where(Run.id == uuid.UUID(str(run_id)))
        ))

    async def set_sandbox_ref(self, run_id: uuid.UUID | str, ref: str | None) -> None:
        await self.db.execute(
            update(Run).where(Run.id == uuid.UUID(str(run_id))).values(sandbox_ref=ref)
        )

    async def add_usage(self, run_id: uuid.UUID | str, *, token_in: int,
                        token_out: int) -> None:
        await self.db.execute(
            update(Run)
            .where(Run.id == uuid.UUID(str(run_id)))
            .values(token_in=Run.token_in + token_in, token_out=Run.token_out + token_out)
        )

    async def set_input_hashes(self, run_id: uuid.UUID | str,
                               hashes: dict[str, Any]) -> None:
        await self.db.execute(
            update(Run).where(Run.id == uuid.UUID(str(run_id))).values(input_hashes=hashes)
        )
