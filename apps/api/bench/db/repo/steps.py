from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import select, update

from bench.db.models import StepRun
from bench.db.repo.base import BaseRepo, NotFound


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class StepRepo(BaseRepo):
    async def create_many(self, *, run_id: uuid.UUID, session_id: uuid.UUID,
                          steps: list[tuple[str, str]]) -> list[StepRun]:
        """steps: [(step_id, step_type), ...] 按声明顺序。"""
        rows = [
            StepRun(run_id=run_id, session_id=session_id, step_id=sid,
                    idx=i, type=stype, status="pending")
            for i, (sid, stype) in enumerate(steps)
        ]
        self.db.add_all(rows)
        await self.db.flush()
        return rows

    async def get(self, run_id: uuid.UUID | str, step_id: str) -> StepRun:
        stmt = select(StepRun).where(
            StepRun.run_id == uuid.UUID(str(run_id)), StepRun.step_id == step_id
        ).order_by(StepRun.attempt.desc()).limit(1)
        stmt = self._scope_by_session(stmt, StepRun)
        row = await self.db.scalar(stmt)
        if row is None:
            raise NotFound(f"step {step_id} not found in run {run_id}")
        return row

    async def list_for_run(self, run_id: uuid.UUID | str) -> list[StepRun]:
        stmt = (
            select(StepRun)
            .where(StepRun.run_id == uuid.UUID(str(run_id)))
            .order_by(StepRun.idx, StepRun.attempt)
        )
        stmt = self._scope_by_session(stmt, StepRun)
        return list((await self.db.scalars(stmt)).all())

    async def start(self, step_pk: uuid.UUID) -> None:
        await self.db.execute(
            update(StepRun).where(StepRun.id == step_pk)
            .values(status="running", started_at=_now())
        )

    async def finish(self, step_pk: uuid.UUID, *, status: str,
                     output: dict[str, Any] | None = None,
                     error: dict[str, Any] | None = None) -> None:
        await self.db.execute(
            update(StepRun).where(StepRun.id == step_pk)
            .values(status=status, output=output, error=error, ended_at=_now())
        )

    async def set_status(self, step_pk: uuid.UUID, status: str) -> None:
        await self.db.execute(
            update(StepRun).where(StepRun.id == step_pk).values(status=status)
        )

    async def first_failed(self, run_id: uuid.UUID | str) -> StepRun | None:
        """R8：找到失败的那一步，重试从这里开始而不是整个重跑。"""
        rows = await self.list_for_run(run_id)
        for r in rows:
            if r.status == "failed":
                return r
        return None

    async def outputs_so_far(self, run_id: uuid.UUID | str) -> dict[str, Any]:
        """已成功步骤的输出，注入后续步骤的 params.json。"""
        return {
            r.step_id: r.output
            for r in await self.list_for_run(run_id)
            if r.status == "succeeded" and r.output is not None
        }
