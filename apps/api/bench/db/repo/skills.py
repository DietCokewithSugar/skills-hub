from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from bench.db.models import Skill


class SkillRepo:
    """Skill 注册表是全局的，不带用户作用域，因此不继承 BaseRepo。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def upsert(self, *, skill_id: str, version: str, manifest: dict[str, Any],
                     source_ref: str | None = None, enabled: bool = True) -> None:
        stmt = insert(Skill).values(
            id=skill_id, version=version, manifest=manifest,
            source_ref=source_ref, enabled=enabled,
        ).on_conflict_do_update(
            index_elements=[Skill.id, Skill.version],
            set_={"manifest": manifest, "source_ref": source_ref, "enabled": enabled},
        )
        await self.db.execute(stmt)

    async def list_enabled(self) -> list[Skill]:
        return list((await self.db.scalars(
            select(Skill).where(Skill.enabled.is_(True)).order_by(Skill.id, Skill.version)
        )).all())

    async def get(self, skill_id: str, version: str | None = None) -> Skill | None:
        stmt = select(Skill).where(Skill.id == skill_id)
        if version:
            stmt = stmt.where(Skill.version == version)
        else:
            stmt = stmt.order_by(Skill.registered_at.desc()).limit(1)
        return await self.db.scalar(stmt)
