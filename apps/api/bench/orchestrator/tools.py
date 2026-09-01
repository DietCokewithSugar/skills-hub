"""LLM 可调用的工具。

    read_reference —— 渐进式披露：按需读长文档（R3 / 6.5）
    ask_user       —— 主动发卡片询问（R0.4「不确定就问，不要猜」）
    run_python     —— 探索轨：现场生成代码并在硬隔离沙箱里跑（R9）

关于 run_python，PRD 的态度很明确（R0.7 双轨制）：

  「探索轨的结果**不能**进入正式报告。用于探索性分析、数据摸底、
    临时图表，结果在会话中呈现并标注『探索性结果，未经校验』。」

所以这里做四件事把这条线守住：
  1. 只允许在提供硬隔离的 runner 上执行，否则结构性拒绝；
  2. 强制写入 /workspace/scratch/，产物收集器按目录排除；
  3. 代码全文落 tool_call Part，执行前对用户完整可见（R9 验收）；
  4. 结果 Part 标 track=exploratory，前端据此做视觉区隔。
"""

from __future__ import annotations

import logging
import textwrap
from typing import Any

from bench.events.types import EventType
from bench.llm.client import ToolSpec
from bench.orchestrator.cards import parse_card_spec
from bench.orchestrator.context import SCRATCH, RunContext
from bench.sandbox.base import ExecRequest, Limits, UntrustedCodeRefused
from bench.skills.refs import read_reference

logger = logging.getLogger(__name__)

#: 探索轨代码前置的守卫。R0.7：「生成的代码必须带断言（行数守恒、空值率、
#: 总和校验），断言失败即中止并回报。」这段 preamble 把断言库摆在手边，
#: 并把工作目录钉死在 scratch。
EXPLORATORY_PREAMBLE = '''\
# ── Bench 探索轨（自动注入）────────────────────────────────
# 本段代码由模型生成，结果标记为「探索性结果，未经校验」，不会进入正式报告。
import os, sys, json
import bench
from bench import asserts, io, stats
os.chdir(str(bench.scratch()))          # 产物只落 scratch，不会被当作报告产物
# ───────────────────────────────────────────────────────────
'''


def tool_specs(*, allow_python: bool) -> list[ToolSpec]:
    specs = [
        ToolSpec(
            name="read_reference",
            description=(
                "读取本 skill 声明的参考资料全文。初始上下文里只给了这些资料的"
                "简介，需要细节时用这个工具读全文。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string",
                             "description": "reference 的 path，如 refs/methodology.md"},
                },
                "required": ["path"],
            },
        ),
        ToolSpec(
            name="ask_user",
            description=(
                "向用户提问并等待作答。参数缺失、口径模糊、样本量不足时**必须**"
                "使用本工具，不允许自行取默认值或估算。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "type": {"type": "string",
                             "enum": ["confirm", "select", "multi_select", "form"]},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"},
                                "description": "select / multi_select 用"},
                    "fields": {"type": "array", "items": {"type": "object"},
                               "description": "form 用"},
                },
                "required": ["type", "title"],
            },
        ),
    ]
    if allow_python:
        specs.append(ToolSpec(
            name="run_python",
            description=(
                "在隔离沙箱中执行 Python 做探索性分析（数据摸底、临时图表）。"
                "结果标注为探索性，**不能**用于正式报告中的任何数字。"
                "优先调用 bench.stats / bench.io 里已审核的函数，不要自己重写统计逻辑。"
                "代码必须带断言（行数守恒、空值率、总和校验）。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "要执行的 Python 代码"},
                    "purpose": {"type": "string", "description": "一句话说明这段代码要看什么"},
                },
                "required": ["code", "purpose"],
            },
        ))
    return specs


# ══════════════════════════════════════════════════════════════════

async def dispatch(ctx: RunContext, name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "read_reference":
        return await _read_reference(ctx, args)
    if name == "ask_user":
        return await _ask_user(ctx, args)
    if name == "run_python":
        return await _run_python(ctx, args)
    return {"ok": False, "error": f"未知工具 {name!r}"}


async def _read_reference(ctx: RunContext, args: dict[str, Any]) -> dict[str, Any]:
    path = str(args.get("path", ""))
    await ctx.emitter.tool_call(name="read_reference", args={"path": path})
    try:
        text = read_reference(ctx.skill, path)
    except (ValueError, OSError) as exc:
        await ctx.emitter.tool_result(name="read_reference", ok=False,
                                      result=str(exc))
        return {"ok": False, "error": str(exc)}
    await ctx.emitter.tool_result(name="read_reference", ok=True,
                                  result=f"已读取 {path}（{len(text)} 字符）")
    return {"ok": True, "content": text}


async def _ask_user(ctx: RunContext, args: dict[str, Any]) -> dict[str, Any]:
    """模型主动发卡片。

    R0 验收最后一条：「模型在没有拿到某个参数时，弹出卡片询问而不是使用
    默认值」——这条工具就是那个「而不是」的实现。
    """
    from bench.orchestrator.context import WaitingForInput

    spec_raw: dict[str, Any] = {
        "type": args.get("type", "confirm"),
        "title": args.get("title", "需要你确认"),
        "body": args.get("body", ""),
    }
    if args.get("options"):
        spec_raw["options"] = args["options"]
    if args.get("fields"):
        spec_raw["fields"] = args["fields"]

    try:
        spec = parse_card_spec(spec_raw)
    except Exception as exc:  # noqa: BLE001
        # 卡片参数不合法是模型的问题，回给它让它改，不中断整个 Run
        return {"ok": False, "error": f"卡片定义不合法：{exc}"}

    payload = spec.model_dump(mode="json")
    step_id = f"ask_user:{ctx.run_id.hex[:8]}"
    _, seq = await ctx.emitter.part("card_request", {
        "step_id": step_id, "spec": payload, "status": "pending"})
    card = await ctx.cards.create(
        run_id=ctx.run_id, session_id=ctx.session_id, step_id=step_id,
        spec=payload, timeout_hours=spec.timeout_hours, request_seq=seq)
    await ctx.runs.set_status(ctx.run_id, "waiting_for_input", current_step=step_id)
    await ctx.sessions.set_status(ctx.session_id, "waiting_for_input")
    await ctx.db.commit()
    await ctx.emitter.signal(EventType.CARD_REQUESTED, {
        "card_id": str(card.id), "step_id": step_id, "seq": seq,
        "expires_at": card.expires_at.isoformat()})
    raise WaitingForInput(card.id, step_id)


async def _run_python(ctx: RunContext, args: dict[str, Any]) -> dict[str, Any]:
    """探索轨执行。"""
    code = str(args.get("code", ""))
    purpose = str(args.get("purpose", ""))

    # ── 1. 代码全文落 Part，执行前对用户完整可见（R9 验收）──
    await ctx.emitter.tool_call(name="run_python", args={"purpose": purpose},
                                code=code, track="exploratory")

    # ── 2. 没有硬隔离就不跑。这不是可配置的。──
    if not getattr(ctx.runner, "provides_hard_isolation", False):
        msg = ("当前沙箱实现不提供内核级隔离，拒绝执行模型生成的代码"
               "（PRD 6.4）。请配置 BENCH_SANDBOX=e2b。")
        await ctx.emitter.tool_result(name="run_python", ok=False, result=msg,
                                      track="exploratory")
        raise UntrustedCodeRefused(msg)

    scratch = ctx.workspace / SCRATCH
    scratch.mkdir(parents=True, exist_ok=True)
    script = scratch / f"explore_{ctx.run_id.hex[:8]}.py"
    script.write_text(EXPLORATORY_PREAMBLE + textwrap.dedent(code), encoding="utf-8")

    limits = Limits.platform_defaults()
    limits.network = "none"          # 探索轨永远无出网，不看 skill 白名单
    limits.timeout_s = min(limits.timeout_s, 120)

    stdout_lines: list[str] = []

    async def on_out(line: str) -> None:
        stdout_lines.append(line)
        await ctx.emitter.signal(EventType.STEP_LOG, {
            "step_id": "explore", "line": line, "stream": "stdout",
            "track": "exploratory"})

    result = await ctx.runner.run(
        ExecRequest(image=ctx.skill.manifest.runtime, workspace=ctx.workspace,
                    entry=f"{SCRATCH}/{script.name}", params=ctx.step_params("explore"),
                    limits=limits, track="exploratory", run_id=str(ctx.run_id)),
        on_stdout=on_out,
        should_cancel=ctx.cancel_requested,
    )

    # ── 3. 结果标 exploratory，前端据此做视觉区隔 ──
    payload = {
        "exit_code": result.exit_code,
        "stdout": result.stdout[-8000:],
        "stderr": result.stderr[-4000:],
        "timed_out": result.timed_out,
        "note": "探索性结果，未经校验；不得用于正式报告中的数字",
    }
    await ctx.emitter.tool_result(
        name="run_python", ok=result.ok, result=payload, track="exploratory",
        stdout=result.stdout[-8000:], stderr=result.stderr[-4000:])

    if not result.ok:
        return {"ok": False, "error": (
            f"执行失败（exit={result.exit_code}）：{result.stderr[-1500:]}"
        )}
    return {"ok": True, "stdout": result.stdout[-8000:],
            "note": "这是探索性结果，不能作为报告中的正式数值"}


def exploratory_consent_card() -> dict[str, Any]:
    """R9：「首次在某会话中执行生成代码时，弹一次确认卡片；
    用户可选择『本会话内不再询问』。」"""
    return {
        "type": "form",
        "title": "允许在本会话中执行模型生成的代码？",
        "body": ("代码将在隔离沙箱中运行：默认无出网、无平台凭据、"
                 "产物写入 scratch 目录且不会进入正式报告。"
                 "生成的代码在执行前会完整展示给你。"),
        "fields": [{"key": "remember", "label": "本会话内不再询问",
                    "type": "checkbox", "required": False, "default": False}],
        "actions": [{"id": "confirm", "label": "允许执行", "primary": True},
                    {"id": "cancel", "label": "拒绝", "cancels": True}],
        "timeout_hours": 24,
    }
