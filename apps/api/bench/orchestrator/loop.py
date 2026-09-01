"""Run 状态机驱动。

Run **不是一个长活协程**，是一台可恢复的状态机：每次被调度时从
`start_index` 往下跑，遇到 interaction step 就落库挂起并干净退出。
用户作答后再调度一次，从下一步继续。

这样三件事同时成立：
  · 一张等 24 小时的卡片不占 worker 槽位；
  · 关掉浏览器、重启服务，卡片仍在原位可作答（R5 验收）；
  · 从失败的 step 重试只要换个 start_index（R8），不用整个重跑。

状态流转（runs.status）：
    queued → running → {waiting_for_input → running}* → succeeded
                     ↘ failed / cancelled / expired
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from bench.db.repo.base import NotFound
from bench.events.types import EventType
from bench.orchestrator.context import (
    RunCancelled,
    RunContext,
    StepFailed,
    WaitingForInput,
    hash_inputs,
    session_workspace,
    stage_skill_files,
)
from bench.orchestrator.steps import (
    run_interaction_step,
    run_llm_step,
    run_python_step,
)
from bench.sandbox.base import UntrustedCodeRefused

logger = logging.getLogger(__name__)


async def execute_run(ctx: RunContext, *, start_index: int = 0) -> str:
    """从 start_index 开始执行。返回 Run 的终态。"""
    manifest = ctx.skill.manifest
    steps = manifest.steps

    await ctx.runs.set_status(ctx.run_id, "running")
    await ctx.sessions.set_status(ctx.session_id, "running")
    await ctx.db.commit()
    await ctx.emitter.signal(EventType.RUN_STARTED, {
        "run_id": str(ctx.run_id), "skill_id": manifest.id,
        "skill_version": manifest.version, "from_step": start_index,
    })

    try:
        for idx in range(start_index, len(steps)):
            step = steps[idx]

            if await ctx.cancel_requested():
                raise RunCancelled()

            await ctx.runs.set_status(ctx.run_id, "running", current_step=step.id)
            ctx.emitter.current_step = step.id
            await ctx.db.commit()

            try:
                step_row = await ctx.steps.get(ctx.run_id, step.id)
                await ctx.steps.start(step_row.id)
                await ctx.db.commit()
            except NotFound:
                step_row = None

            if step.type == "python":
                out = await run_python_step(ctx, step, idx)
                ctx.outputs[step.id] = out
                if step_row:
                    await ctx.steps.finish(step_row.id, status="succeeded", output=out)
            elif step.type == "llm":
                out = await run_llm_step(ctx, step, idx)
                ctx.outputs[step.id] = out
                if step_row:
                    await ctx.steps.finish(step_row.id, status="succeeded", output=out)
            elif step.type == "interaction":
                if step_row:
                    await ctx.steps.set_status(step_row.id, "waiting_for_input")
                    await ctx.db.commit()
                await run_interaction_step(ctx, step, idx)   # 抛 WaitingForInput
            else:  # pragma: no cover — manifest 已经挡住了
                raise StepFailed(step.id, f"未知的步骤类型 {step.type!r}",
                                 retryable=False)
            await ctx.db.commit()

        await ctx.runs.set_status(ctx.run_id, "succeeded")
        await ctx.sessions.set_status(ctx.session_id, "idle")
        await ctx.db.commit()
        await ctx.emitter.signal(EventType.RUN_COMPLETED, {"run_id": str(ctx.run_id)})
        return "succeeded"

    except WaitingForInput:
        # 正常挂起：状态已在 run_interaction_step 里落库并广播
        return "waiting_for_input"

    except RunCancelled:
        await _finish_cancelled(ctx)
        return "cancelled"

    except UntrustedCodeRefused as exc:
        await _finish_failed(ctx, StepFailed(
            ctx.skill.manifest.steps[start_index].id if steps else "(未知)",
            str(exc), detail={"kind": "untrusted_code_refused"}, retryable=False))
        return "failed"

    except StepFailed as exc:
        await _finish_failed(ctx, exc)
        return "failed"

    except Exception as exc:  # noqa: BLE001
        logger.exception("Run %s 意外失败", ctx.run_id)
        await _finish_failed(ctx, StepFailed(
            "(平台)", f"平台内部错误：{exc}",
            detail={"kind": "internal"}, retryable=True))
        return "failed"


async def _finish_failed(ctx: RunContext, exc: StepFailed) -> None:
    """失败收尾。

    失败也是历史的一部分（R2）—— 所以写 error Part 而不只是记日志。
    payload 的三段（哪一步 / 什么原因 / 能否重试）直接喂给 7.7 的错误面板。
    """
    payload = exc.to_payload()
    try:
        step_row = await ctx.steps.get(ctx.run_id, exc.step_id)
        await ctx.steps.finish(step_row.id, status="failed", error=payload)
    except NotFound:
        pass
    await ctx.cards.cancel_pending_for_run(ctx.run_id)
    await ctx.runs.set_status(ctx.run_id, "failed", current_step=exc.step_id,
                              error=payload)
    await ctx.sessions.set_status(ctx.session_id, "failed")
    await ctx.db.commit()

    await ctx.emitter.error(step_id=exc.step_id, message=exc.message,
                            detail=exc.detail, retryable=exc.retryable)
    await ctx.emitter.signal(EventType.STEP_FAILED, {"step_id": exc.step_id})
    await ctx.emitter.signal(EventType.RUN_FAILED,
                             {"run_id": str(ctx.run_id), **payload})


async def _finish_cancelled(ctx: RunContext) -> None:
    await ctx.cards.cancel_pending_for_run(ctx.run_id)
    await ctx.runs.set_status(ctx.run_id, "cancelled")
    await ctx.sessions.set_status(ctx.session_id, "idle")
    await ctx.db.commit()
    await ctx.emitter.signal(EventType.RUN_CANCELLED, {"run_id": str(ctx.run_id)})


# ══════════════════════════════════════════════════════════════════
# 上下文装配
# ══════════════════════════════════════════════════════════════════

async def build_context(db, *, run_id: uuid.UUID, user_id: uuid.UUID,
                        bus=None) -> RunContext:
    """从库里把一次执行需要的一切装配起来。

    这里也是 worker 重启后恢复现场的地方：卡片答案与已完成步骤的输出
    全部从库里读回来，不依赖任何进程内状态。
    """
    from bench.db.repo import CardRepo, RunRepo, StepRepo
    from bench.events.emitter import Emitter
    from bench.sandbox.base import get_runner
    from bench.skills.registry import get_registry
    from bench.storage.base import get_store

    run = await RunRepo(db, user_id).get(run_id)
    skill = get_registry().require(run.skill_id or "")

    workspace = session_workspace(run.session_id)
    stage_skill_files(skill, workspace)

    # 已作答的卡片 → answers
    answers: dict[str, Any] = {}
    for card in await _answered_cards(CardRepo(db, user_id), run_id):
        answers[card.step_id] = (card.response or {}).get("values", {})

    # 已成功的步骤 → outputs
    outputs = await StepRepo(db, user_id).outputs_so_far(run_id)

    ctx = RunContext(
        db=db, emitter=Emitter(db, run.session_id, user_id, bus=bus),
        runner=get_runner(), store=get_store(), skill=skill,
        run_id=run.id, session_id=run.session_id, user_id=user_id,
        workspace=workspace, answers=answers, outputs=outputs,
        inputs=(run.input_hashes or {}).get("_inputs", {}),
    )
    await ctx.emitter.open_message("assistant")
    return ctx


async def _answered_cards(cards_repo, run_id):
    from sqlalchemy import select

    from bench.db.models import Card
    stmt = (select(Card)
            .where(Card.run_id == run_id, Card.status == "answered")
            .order_by(Card.created_at))
    return list((await cards_repo.db.scalars(stmt)).all())


async def record_inputs(ctx: RunContext, inputs: dict[str, Any]) -> None:
    """记录输入与哈希（R0.6 复现记录）。"""
    hashes = hash_inputs(ctx.workspace)
    await ctx.runs.set_input_hashes(ctx.run_id, {**hashes, "_inputs": inputs})
    await ctx.db.commit()


def next_index_after(skill, step_id: str) -> int:
    return skill.manifest.step_index(step_id) + 1
