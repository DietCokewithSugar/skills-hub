"""本地文件系统对象存储 —— **仅供本地开发与冒烟测试**。

生产走 Supabase Storage（见 supabase.py）。这个实现存在的理由是：
没有 Supabase 凭据时也能把端到端流程跑通一次。

它刻意保持**与 Supabase 实现完全相同的对外契约**：

  * `signed_url()` 返回一个带 HMAC 签名与过期时间的 URL，不是文件路径；
  * 下载仍然经过 `/api/artifacts/{id}/download` 的 302，前端拿不到直链；
  * 签名过期后链接失效。

契约一致才有意义 —— 否则本地跑通了，换到 Supabase 又是另一套行为。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import shutil
import time
from pathlib import Path
from urllib.parse import quote

from bench.config import get_settings
from bench.storage.base import StorageError

logger = logging.getLogger(__name__)


def _root() -> Path:
    p = get_settings().workspace_root.parent / ".bench-storage"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _secret() -> bytes:
    """签名密钥。本地开发用固定盐 + 存储根路径派生，不需要额外配置。"""
    s = get_settings()
    raw = (s.dev_user_id + str(_root())).encode()
    return hashlib.sha256(raw).digest()


def sign(key: str, expires_at: int) -> str:
    msg = f"{key}:{expires_at}".encode()
    return hmac.new(_secret(), msg, hashlib.sha256).hexdigest()[:32]


def verify(key: str, expires_at: int, token: str) -> bool:
    if expires_at < int(time.time()):
        return False
    return hmac.compare_digest(sign(key, expires_at), token)


class LocalObjectStore:
    """开发用对象存储。"""

    def __init__(self) -> None:
        logger.warning(
            "未配置 Supabase Storage，使用本地文件系统存储（仅限本地开发）。"
            "产物落在 %s，不会有任何跨机器可用性。", _root(),
        )

    def _path(self, key: str) -> Path:
        p = (_root() / key).resolve()
        if not p.is_relative_to(_root().resolve()):
            raise StorageError(f"非法的存储 key：{key}")
        return p

    async def upload(self, key: str, path: Path, *,
                     content_type: str = "application/octet-stream") -> int:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        return dest.stat().st_size

    async def signed_url(self, key: str, *, expires_in: int = 3600,
                         download_name: str | None = None) -> str:
        exp = int(time.time()) + expires_in
        token = sign(key, exp)
        url = (f"/api/_local-storage/{quote(key)}"
               f"?expires={exp}&token={token}")
        if download_name:
            url += f"&download={quote(download_name)}"
        return url

    async def delete(self, keys: list[str]) -> None:
        for k in keys:
            try:
                self._path(k).unlink(missing_ok=True)
            except (StorageError, OSError) as exc:
                logger.warning("删除本地产物失败 %s: %s", k, exc)

    async def download(self, key: str, dest: Path) -> Path:
        src = self._path(key)
        if not src.exists():
            raise StorageError(f"产物不存在：{key}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return dest

    def resolve(self, key: str) -> Path:
        return self._path(key)
