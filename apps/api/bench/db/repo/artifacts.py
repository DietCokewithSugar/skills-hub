from __future__ import annotations

import uuid

from sqlalchemy import select

from bench.db.models import Artifact
from bench.db.repo.base import BaseRepo, NotFound


class ArtifactRepo(BaseRepo):
    async def create(self, *, session_id: uuid.UUID, run_id: uuid.UUID | None,
                     filename: str, mime: str, storage_key: str, size_bytes: int,
                     track: str = "trusted",
                     preview_key: str | None = None) -> Artifact:
        row = Artifact(
            session_id=session_id, run_id=run_id, filename=filename, mime=mime,
            storage_key=storage_key, size_bytes=size_bytes, track=track,
            preview_key=preview_key,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def get(self, artifact_id: uuid.UUID | str) -> Artifact:
        stmt = select(Artifact).where(Artifact.id == uuid.UUID(str(artifact_id)))
        stmt = self._scope_by_session(stmt, Artifact)
        row = await self.db.scalar(stmt)
        if row is None:
            raise NotFound(f"artifact {artifact_id} not found")
        return row

    async def list_for_session(self, session_id: uuid.UUID | str, *,
                               track: str | None = "trusted") -> list[Artifact]:
        """默认只列正式产物。

        R9 验收：探索轨（/workspace/scratch/）的产物不出现在产物列表中。
        收集阶段就已按目录排除，这里的 track 过滤是第二道闸门。
        """
        await self.require_session(session_id)
        stmt = (
            select(Artifact)
            .where(Artifact.session_id == uuid.UUID(str(session_id)))
            .order_by(Artifact.created_at.desc())
        )
        if track is not None:
            stmt = stmt.where(Artifact.track == track)
        return list((await self.db.scalars(stmt)).all())
