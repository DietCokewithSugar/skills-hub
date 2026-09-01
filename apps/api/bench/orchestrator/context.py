"""一次 Run 的执行上下文。"""

from __future__ import annotations

import hashlib
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from bench.config import get_settings
from bench.db.repo import ArtifactRepo, CardRepo, PartRepo, RunRepo, SessionRepo, StepRepo
from bench.events.emitter import Emitter
from bench.sandbox.base import SandboxRunner
from bench.skills.registry import LoadedSkill
from bench.storage.base import ObjectStore

#: 探索轨目录名。产物收集按目录硬排除它（R9）。
SCRATCH = "scratch"


class WaitingForInput(Exception):
    """interaction step 命中，Run 挂起等用户作答。

    这不是错误，是正常的控制流：worker 任务就此干净地结束，
    不占着槽位等 24 小时（R5 的持久化设计）。
    """

    def __init__(self, card_id: uuid.UUID, step_id: str) -> None:
        self.card_id = card_id
        self.step_id = step_id
        super().__init__(f"等待用户作答：{step_id}")


class RunCancelled(Exception):
    """用户取消（R8）。"""


#: 不可重试的失败各自的「下一步怎么办」。7.7 要求三段都说清：
#: 哪一步、什么原因、下一步怎么办 —— 只说前两段等于把人晾在那里。
_NEXT_STEP_HINT = {
    "preflight": "换一份包含所有必填列的数据，或在确认卡片里调整口径后重新开始。",
    "schema": "这一步的输出不符合它自己声明的 schema，属于 skill 的问题，"
              "请联系 skill 作者。",
    "provenance": "指标缺少来源标注，属于 skill 的问题，请联系 skill 作者。",
    "untrusted_code_refused": "当前沙箱不提供内核级隔离，无法执行模型生成的代码。",
}


class StepFailed(Exception):
    """某一步失败。payload 直接对应 7.7 的三段式错误展示。"""

    def __init__(self, step_id: str, message: str, *,
                 detail: dict[str, Any] | None = None,
                 retryable: bool = True) -> None:
        self.step_id = step_id
        self.message = message
        self.detail = detail or {}
        self.retryable = retryable
        if not retryable and "hint" not in self.detail:
            hint = _NEXT_STEP_HINT.get(str(self.detail.get("kind", "")))
            if hint:
                self.detail["hint"] = hint
        super().__init__(f"步骤 {step_id!r} 失败：{message}")

    def to_payload(self) -> dict[str, Any]:
        return {"step_id": self.step_id, "message": self.message,
                "detail": self.detail, "retryable": self.retryable}


@dataclass
class RunContext:
    db: AsyncSession
    emitter: Emitter
    runner: SandboxRunner
    store: ObjectStore
    skill: LoadedSkill
    run_id: uuid.UUID
    session_id: uuid.UUID
    user_id: uuid.UUID
    workspace: Path
    #: 卡片答案累积：step_id -> values
    answers: dict[str, Any] = field(default_factory=dict)
    #: 已完成 python step 的输出：step_id -> result.json
    outputs: dict[str, Any] = field(default_factory=dict)
    #: 会话级输入（首条消息里带的文件与参数）
    inputs: dict[str, Any] = field(default_factory=dict)

    # ── repo 便捷访问 ───────────────────────────────────────────
    @property
    def runs(self) -> RunRepo:
        return RunRepo(self.db, self.user_id)

    @property
    def steps(self) -> StepRepo:
        return StepRepo(self.db, self.user_id)

    @property
    def cards(self) -> CardRepo:
        return CardRepo(self.db, self.user_id)

    @property
    def artifacts(self) -> ArtifactRepo:
        return ArtifactRepo(self.db, self.user_id)

    @property
    def sessions(self) -> SessionRepo:
        return SessionRepo(self.db, self.user_id)

    @property
    def parts(self) -> PartRepo:
        return PartRepo(self.db, self.user_id)

    async def cancel_requested(self) -> bool:
        return await self.runs.is_cancel_requested(self.run_id)

    def step_params(self, step_id: str) -> dict[str, Any]:
        """拼这一步的 /meta/params.json。

        R5 第 6 条：「答案注入下一步的 params.json」。前序步骤的输出也一并给，
        这样 render 步骤不用重新读文件就能拿到 compute 的结果。
        """
        return {
            **self.inputs,
            **{k: v for a in self.answers.values() for k, v in (a or {}).items()},
            "_answers": self.answers,
            "_steps": self.outputs,
            "_step_id": step_id,
        }


def session_workspace(session_id: uuid.UUID | str) -> Path:
    """会话工作区。

    R1 验收：「会话的工作区文件不会串到另一个会话」—— 按 session_id
    分目录是这条的结构保证，不是靠调用方小心。
    """
    root = get_settings().workspace_root
    ws = (root / str(session_id)).resolve()
    if not ws.is_relative_to(root.resolve()):
        raise ValueError("非法的会话工作区路径")
    ws.mkdir(parents=True, exist_ok=True)
    (ws / SCRATCH).mkdir(exist_ok=True)
    return ws


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_inputs(workspace: Path) -> dict[str, str]:
    """R0.3：「输入文件的哈希值随会话记录，确保同一份数据 + 同一版 skill
    = 同一份报告。」"""
    out: dict[str, str] = {}
    for p in sorted(workspace.iterdir()):
        if p.is_file():
            out[p.name] = file_sha256(p)
    return out


def stage_skill_files(skill: LoadedSkill, workspace: Path) -> None:
    """把 skill 的脚本与模板拷进工作区，入口才找得到。"""
    for sub in ("steps", "templates", "schemas", "assets", "cards"):
        src = skill.root / sub
        if src.exists():
            shutil.copytree(src, workspace / sub, dirs_exist_ok=True)
