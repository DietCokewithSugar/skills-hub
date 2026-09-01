"""Supabase Storage 实现。

R6：「下载走签名 URL（有效期 1 小时），不暴露存储直链。」

用 REST API 而不是 supabase-py：少一个依赖，且请求形状一目了然
（这层出问题时，能直接拿 curl 复现比翻 SDK 源码快）。
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from bench.config import get_settings
from bench.storage.base import StorageError

logger = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(60.0, connect=10.0)


class SupabaseStore:
    def __init__(self, *, url: str | None = None, service_key: str | None = None,
                 bucket: str | None = None) -> None:
        s = get_settings()
        self.url = (url or s.supabase_url).rstrip("/")
        self.key = service_key or s.supabase_service_key
        self.bucket = bucket or s.storage_bucket
        self.default_ttl = s.signed_url_ttl

    def _headers(self, **extra: str) -> dict[str, str]:
        if not self.key:
            raise StorageError("BENCH_SUPABASE_SERVICE_KEY 未配置，无法访问对象存储")
        return {"Authorization": f"Bearer {self.key}", "apikey": self.key, **extra}

    async def upload(self, key: str, path: Path, *,
                     content_type: str = "application/octet-stream") -> int:
        data = path.read_bytes()
        url = f"{self.url}/storage/v1/object/{self.bucket}/{key}"
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(
                url, content=data,
                headers=self._headers(**{
                    "Content-Type": content_type,
                    # 同 key 覆盖：重试同一步骤时不该因为文件已存在而失败
                    "x-upsert": "true",
                }),
            )
        if r.status_code >= 300:
            raise StorageError(f"上传失败 {key}：HTTP {r.status_code} {r.text[:300]}")
        return len(data)

    async def signed_url(self, key: str, *, expires_in: int | None = None,
                         download_name: str | None = None) -> str:
        ttl = expires_in or self.default_ttl
        url = f"{self.url}/storage/v1/object/sign/{self.bucket}/{key}"
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(url, json={"expiresIn": ttl},
                             headers=self._headers(**{"Content-Type": "application/json"}))
        if r.status_code >= 300:
            raise StorageError(f"签名失败 {key}：HTTP {r.status_code} {r.text[:300]}")
        signed = r.json().get("signedURL") or r.json().get("signedUrl")
        if not signed:
            raise StorageError(f"签名响应里没有 URL：{r.text[:300]}")
        full = f"{self.url}/storage/v1{signed}" if signed.startswith("/") else signed
        if download_name:
            sep = "&" if "?" in full else "?"
            full = f"{full}{sep}download={httpx.QueryParams({'d': download_name})['d']}"
        return full

    async def delete(self, keys: list[str]) -> None:
        if not keys:
            return
        url = f"{self.url}/storage/v1/object/{self.bucket}"
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.request("DELETE", url, json={"prefixes": keys},
                                headers=self._headers(**{"Content-Type": "application/json"}))
        if r.status_code >= 300:
            logger.warning("删除产物失败：HTTP %s %s", r.status_code, r.text[:200])

    async def download(self, key: str, dest: Path) -> Path:
        url = f"{self.url}/storage/v1/object/{self.bucket}/{key}"
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(url, headers=self._headers())
        if r.status_code >= 300:
            raise StorageError(f"下载失败 {key}：HTTP {r.status_code}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        return dest
