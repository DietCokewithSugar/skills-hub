"""集中配置。所有密钥、URL、模型名都在这里读环境变量。

PRD 6.5：「模型名、base_url 走环境变量，代码里只出现一个 LLMClient」。
PRD 6.4：「secrets：沙箱内不注入任何平台凭据」——本模块提供的任何值
都不得进入沙箱环境；沙箱只看到 /meta/params.json。
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BENCH_",
        env_file=(_REPO_ROOT / ".env", Path(".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── 数据层 ───────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"
    supabase_url: str = ""
    supabase_service_key: str = ""
    storage_bucket: str = "bench-artifacts"
    signed_url_ttl: int = 3600

    # ── 队列 ─────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── 模型 ─────────────────────────────────────────────────
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_model: str = "deepseek-v4-flash"
    llm_thinking: bool = True
    llm_temperature: float = 0.1
    prompt_version: str = "v1"

    # ── 沙箱 ─────────────────────────────────────────────────
    sandbox: Literal["e2b", "local"] = "e2b"
    e2b_template: str = "bench-python-312"

    sandbox_timeout_s: int = 300
    sandbox_memory_mb: int = 1024
    sandbox_disk_mb: int = 2048
    sandbox_cpus: float = 1.0
    sandbox_pids: int = 128

    # ── 平台 ─────────────────────────────────────────────────
    skills_dir: Path = _REPO_ROOT / "skills"
    dev_user_id: str = "00000000-0000-0000-0000-000000000001"
    card_timeout_hours: int = 24
    artifact_retention_days: int = 30
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # 工作区暂存根目录（平台侧，用于沙箱上下行文件）
    workspace_root: Path = _REPO_ROOT / ".bench-workspaces"

    @field_validator("skills_dir", "workspace_root", mode="after")
    @classmethod
    def _resolve(cls, v: Path) -> Path:
        return v if v.is_absolute() else (_REPO_ROOT / v).resolve()

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # E2B_API_KEY 不带 BENCH_ 前缀（E2B SDK 自己也读这个名字），单独取。
    e2b_api_key: str = Field(default="", validation_alias="E2B_API_KEY")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def audit_startup(s: Settings | None = None) -> list[str]:
    """启动自检：把配置上的安全缺口大声说出来，而不是默默跑下去。

    PRD 6.4：「出网是第一优先级的配置项」。返回告警列表（也用于测试断言）。
    """
    s = s or get_settings()
    warnings: list[str] = []

    if s.sandbox == "local":
        warnings.append(
            "沙箱设为 local：本地子进程执行，无任何内核级隔离。"
            "仅可用于 fixtures 回归与 CI；探索轨（模型生成代码）会被硬拒绝。"
        )
    if s.sandbox == "e2b":
        if not s.e2b_api_key:
            warnings.append("BENCH_SANDBOX=e2b 但 E2B_API_KEY 为空，沙箱调用会失败。")
    if not s.llm_api_key:
        warnings.append("BENCH_LLM_API_KEY 为空，LLM 调用会失败。")
    if not s.supabase_service_key:
        warnings.append("BENCH_SUPABASE_SERVICE_KEY 为空，产物上传与签名 URL 会失败。")

    for w in warnings:
        logger.warning("[startup-audit] %s", w)
    return warnings
