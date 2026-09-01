"""LLM 步骤：叙述性生成 + 结构化输出校验。

R0.2：「LLM 生成的结构化内容同样走 schema 校验，失败则重试，
重试两次仍失败则转人工确认卡片。」

R0.1：生成的叙述必须只引用 result.json 里的数字。数字越界即重试；
仍然越界就不硬来 —— 转人工，让人来决定。这比「再试一次说不定就对了」
诚实，也符合 R0.4「不确定就问，不要猜」。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from bench.accuracy.number_guard import check_narrative
from bench.accuracy.provenance import allowed_numbers
from bench.accuracy.validation import load_schema, validate_llm_json
from bench.events.types import EventType
from bench.llm.client import Message
from bench.llm.deepseek import DeepSeekClient
from bench.orchestrator.context import RunContext, StepFailed
from bench.skills.manifest import Step
from bench.skills.refs import initial_reference_block

logger = logging.getLogger(__name__)

#: R0.2 的「重试两次」
MAX_ATTEMPTS = 3

SYSTEM_RULES = """你是 Bench 平台上的报告撰写助手。

铁律（违反即被系统拦截，不是建议）：
1. 你**不做任何计算**。所有数值都已由 Python 步骤算好，放在 result.json 里。
2. 你只能引用 result.json 中出现过的数字。禁止四舍五入以外的改写，
   禁止估算，禁止补全缺失数据，禁止推断趋势百分比。
3. 数据缺失或口径不明时，明确写出「该项数据缺失」，不要猜一个值填上。
4. 只写叙述性文字：现象描述、结论、建议。不要重复罗列已在表格中的数字。

系统会逐个比对你写下的每一个数字与 result.json。对不上就整段作废。
"""


async def generate_narrative(ctx: RunContext, step: Step, idx: int) -> dict[str, Any]:
    await ctx.emitter.signal(EventType.STEP_STARTED, {
        "step_id": step.id, "idx": idx, "type": "llm",
        "label": step.label or step.id,
    })

    client = DeepSeekClient()
    result_json = _merged_results(ctx)
    allowed = allowed_numbers(result_json)

    schema = None
    if step.response_schema:
        schema = load_schema(ctx.skill.file(step.response_schema))

    prompt = _load_prompt(ctx, step)
    messages = [
        Message(role="system", content=SYSTEM_RULES + "\n\n"
                + initial_reference_block(ctx.skill)),
        Message(role="user", content=(
            f"{prompt}\n\n"
            f"## 可引用的计算结果（result.json）\n"
            f"```json\n{json.dumps(result_json, ensure_ascii=False, indent=2)}\n```"
        )),
    ]

    last_problem = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        text, reasoning = await _stream_once(ctx, client, messages,
                                             json_mode=schema is not None)
        if reasoning:
            await ctx.emitter.reasoning(reasoning)

        # ── 结构化输出：schema 校验 ──
        if schema is not None:
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                last_problem = f"输出不是合法 JSON：{exc}"
                messages.append(Message(role="assistant", content=text))
                messages.append(Message(
                    role="user",
                    content=f"上一次输出不是合法 JSON（{exc}），请重新输出纯 JSON。"))
                continue
            problems = validate_llm_json(payload, schema)
            if problems:
                last_problem = "；".join(problems[:3])
                messages.append(Message(role="assistant", content=text))
                messages.append(Message(role="user", content=(
                    "上一次输出未通过 schema 校验：\n"
                    + "\n".join(f"- {p}" for p in problems)
                    + "\n请修正后重新输出。")))
                continue
            narrative_texts = _collect_strings(payload)
        else:
            payload = {"text": text}
            narrative_texts = [text]

        # ── 数字越界拦截（R0.1）──
        violations: list[str] = []
        for t in narrative_texts:
            r = check_narrative(t, allowed)
            violations += r.violations
        if violations:
            last_problem = (f"叙述中出现了 result.json 里没有的数字："
                            f"{'、'.join(dict.fromkeys(violations))}")
            logger.warning("第 %s 次生成越界：%s", attempt, last_problem)
            messages.append(Message(role="assistant", content=text))
            messages.append(Message(role="user", content=(
                f"{last_problem}。你只能引用 result.json 里已有的数值，"
                f"不能自行计算或估算。请重写。")))
            continue

        await ctx.emitter.text(text if schema is None else
                               json.dumps(payload, ensure_ascii=False))
        await ctx.emitter.signal(EventType.STEP_COMPLETED, {
            "step_id": step.id, "attempts": attempt})
        return payload

    # ── 重试两次仍失败 → 不硬来，转人工（R0.2 / R0.4）──
    raise StepFailed(
        step.id,
        f"模型连续 {MAX_ATTEMPTS} 次输出未通过校验：{last_problem}",
        detail={"kind": "llm_validation", "last_problem": last_problem,
                "hint": "可以改参数后重试，或人工撰写这一段"},
        retryable=True,
    )


async def _stream_once(ctx: RunContext, client, messages, *,
                       json_mode: bool) -> tuple[str, str]:
    """流式跑一轮，把推理内容与正文分开攒。

    推理内容边收边推给前端（R7「在想」状态的数据源），正文攒完再落 Part ——
    正文可能因为数字越界被整段作废，作废的东西不该出现在历史里。
    """
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    async for chunk in client.stream(messages, json_mode=json_mode):
        if chunk.kind == "reasoning":
            reasoning_parts.append(chunk.text)
            await ctx.emitter.signal(EventType.PART_DELTA, {
                "type": "reasoning", "text": chunk.text})
        elif chunk.kind == "text":
            text_parts.append(chunk.text)
        elif chunk.kind == "usage" and chunk.usage:
            await ctx.runs.add_usage(ctx.run_id, token_in=chunk.usage.token_in,
                                     token_out=chunk.usage.token_out)
            await ctx.db.commit()
            await ctx.emitter.signal(EventType.USAGE_UPDATED, {
                "token_in": chunk.usage.token_in, "token_out": chunk.usage.token_out})
    return "".join(text_parts).strip(), "".join(reasoning_parts).strip()


def _merged_results(ctx: RunContext) -> dict[str, Any]:
    """把前序 python step 的输出合成一份 —— LLM 能引用的全部数字。"""
    merged: dict[str, Any] = {"metrics": {}}
    for out in ctx.outputs.values():
        if not isinstance(out, dict):
            continue
        for k, v in out.items():
            if k == "metrics" and isinstance(v, dict):
                merged["metrics"].update(v)
            else:
                merged[k] = v
    return merged


def _collect_strings(obj: Any) -> list[str]:
    out: list[str] = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            out += _collect_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            out += _collect_strings(v)
    return out


def _load_prompt(ctx: RunContext, step: Step) -> str:
    """prompt 可以是内联文本，也可以是 skill 里的一个文件。"""
    raw = step.prompt or ""
    candidate = ctx.skill.root / raw
    if raw and candidate.exists() and candidate.is_file():
        return candidate.read_text(encoding="utf-8")
    return raw
