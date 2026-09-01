"""对象存储抽象。

P2-R16：私有化部署时不得与具体供应商 SDK 耦合。所以业务代码只认这个
接口，Supabase 是其中一个实现。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class StorageError(Exception):
    pass


class ObjectStore(Protocol):
    async def upload(self, key: str, path: Path, *,
                     content_type: str = "application/octet-stream") -> int: ...

    async def signed_url(self, key: str, *, expires_in: int = 3600,
                         download_name: str | None = None) -> str: ...

    async def delete(self, keys: list[str]) -> None: ...

    async def download(self, key: str, dest: Path) -> Path: ...


def get_store() -> ObjectStore:
    """按配置返回存储实现。

    没有 Supabase 凭据时退回本地文件系统存储，让本地开发与冒烟测试能跑通。
    两者对外契约一致（都返回带过期时间的签名 URL），所以「本地跑通了、
    上云又是另一套行为」这种事不会发生。
    """
    from bench.config import get_settings

    if get_settings().supabase_service_key:
        from bench.storage.supabase import SupabaseStore
        return SupabaseStore()

    from bench.storage.local import LocalObjectStore
    return LocalObjectStore()


def artifact_key(user_id: str, session_id: str, run_id: str, filename: str) -> str:
    """产物的存储路径。

    带 user_id 前缀，这样将来给 Storage 加 RLS 策略时，
    「路径首段等于 auth.uid()」就是现成的规则。
    """
    safe = filename.replace("/", "_").replace("\\", "_").lstrip(".")
    return f"{user_id}/{session_id}/{run_id}/{safe}"
