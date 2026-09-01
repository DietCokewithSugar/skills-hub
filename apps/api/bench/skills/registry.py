"""Skill 注册表。

R3 验收：「新增一个 skill 目录并注册后，无需重启服务即可在列表中出现」。

做法：注册表不缓存永久快照，而是按目录 mtime 做失效检查 —— 每次 list()
先扫一遍 skills 根目录，目录集合或 skill.yaml 的 mtime 变了就重新加载
那一个 skill。坏掉的 skill 不会拖垮整张表：它被单独记进 errors，列表照常
返回其他可用 skill（一个 skill 写错了不该让整个平台的 skill 列表打不开）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from bench.config import get_settings
from bench.skills.manifest import Manifest, ManifestError, load_manifest

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LoadedSkill:
    manifest: Manifest
    root: Path
    mtime: float

    @property
    def id(self) -> str:
        return self.manifest.id

    def file(self, rel: str) -> Path:
        """解析 skill 内的相对路径，并阻止越出 skill 根目录。"""
        p = (self.root / rel).resolve()
        root = self.root.resolve()
        if not p.is_relative_to(root):
            raise ValueError(f"路径越出 skill 目录：{rel}")
        return p


@dataclass
class SkillRegistry:
    skills_dir: Path
    _cache: dict[str, LoadedSkill] = field(default_factory=dict)
    #: skill_id -> 人类可读的错误（给平台维护者看失败日志用）
    errors: dict[str, str] = field(default_factory=dict)

    def _candidate_dirs(self) -> list[Path]:
        if not self.skills_dir.exists():
            return []
        return sorted(
            d for d in self.skills_dir.iterdir()
            if d.is_dir() and (d / "skill.yaml").exists() and not d.name.startswith(".")
        )

    def refresh(self) -> None:
        """重扫目录。只重新加载变过的，不整表重建。"""
        seen: set[str] = set()
        for d in self._candidate_dirs():
            manifest_path = d / "skill.yaml"
            mtime = manifest_path.stat().st_mtime
            cached = self._cache.get(d.name)
            if cached is not None and cached.mtime == mtime and cached.root == d:
                seen.add(cached.id)
                continue
            try:
                manifest = load_manifest(d)
            except ManifestError as exc:
                self.errors[d.name] = str(exc)
                logger.error("skill 加载失败 %s:\n%s", d.name, exc)
                self._cache.pop(d.name, None)
                continue
            self.errors.pop(d.name, None)
            self._cache[d.name] = LoadedSkill(manifest=manifest, root=d, mtime=mtime)
            seen.add(manifest.id)

        # 目录被删掉的 skill 也要从表里消失
        for key in [k for k, v in self._cache.items() if not (v.root / "skill.yaml").exists()]:
            self._cache.pop(key, None)

    def list(self) -> list[LoadedSkill]:
        self.refresh()
        return sorted(self._cache.values(), key=lambda s: s.id)

    def get(self, skill_id: str) -> LoadedSkill | None:
        self.refresh()
        return next((s for s in self._cache.values() if s.id == skill_id), None)

    def require(self, skill_id: str) -> LoadedSkill:
        s = self.get(skill_id)
        if s is None:
            if skill_id in self.errors:
                raise KeyError(f"skill {skill_id!r} 存在但加载失败：\n{self.errors[skill_id]}")
            raise KeyError(f"skill {skill_id!r} 未注册")
        return s


_registry: SkillRegistry | None = None


def get_registry() -> SkillRegistry:
    global _registry
    if _registry is None:
        _registry = SkillRegistry(skills_dir=get_settings().skills_dir)
    return _registry


def reset_registry(skills_dir: Path | None = None) -> SkillRegistry:
    global _registry
    _registry = SkillRegistry(skills_dir=skills_dir or get_settings().skills_dir)
    return _registry
