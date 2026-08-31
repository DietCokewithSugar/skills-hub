"""DeepSeek 接入（OpenAI 兼容接口）。

PRD 6.5：模型固定 deepseek-v4-flash，base_url https://api.deepseek.com。
「思考模式返回的推理内容正是 R7 思考态展示的数据源」—— 所以流式解析里
reasoning_content 是一等公民，不是可选的附加项。

换行内网关时只需要改这个文件（或写一个同签名的新实现），
编排器不受影响。
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

from openai import AsyncOpenAI
from openai import OpenAIError

from bench.config import get_settings
from bench.llm.client import Chunk, LLMError, Message, ToolCall, ToolSpec, Usage

logger = logging.getLogger(__name__)


class DeepSeekClient:
    def __init__(self, *, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None) -> None:
        s = get_settings()
        self.model = model or s.llm_model
        self._default_thinking = s.llm_thinking
        self._default_temperature = s.llm_temperature
        self._client = AsyncOpenAI(
            base_url=base_url or s.llm_base_url,
            api_key=api_key or s.llm_api_key or "unset",
            max_retries=2,
        )

    def _build_kwargs(self, messages: Sequence[Message], *,
                      tools: Sequence[ToolSpec] | None, thinking: bool | None,
                      temperature: float | None, json_mode: bool,
                      max_tokens: int | None) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_wire() for m in messages],
            # R0.6：默认低 temperature，调用方要更低可以，别指望更高
            "temperature": self._default_temperature if temperature is None else temperature,
        }
        if tools:
            kwargs["tools"] = [t.to_wire() for t in tools]
            kwargs["tool_choice"] = "auto"
        if json_mode:
            # R0.2：结构化输出走 JSON mode
            kwargs["response_format"] = {"type": "json_object"}
        if max_tokens:
            kwargs["max_tokens"] = max_tokens

        want_thinking = self._default_thinking if thinking is None else thinking
        if want_thinking:
            # DeepSeek 的思考模式开关。不同网关命名可能不同，集中在这里。
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        return kwargs

    async def stream(self, messages: Sequence[Message], *,
                     tools: Sequence[ToolSpec] | None = None,
                     thinking: bool | None = None, temperature: float | None = None,
                     json_mode: bool = False,
                     max_tokens: int | None = None) -> AsyncIterator[Chunk]:
        kwargs = self._build_kwargs(messages, tools=tools, thinking=thinking,
                                    temperature=temperature, json_mode=json_mode,
                                    max_tokens=max_tokens)
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}

        # tool_call 在流里是按 index 分片到达的，要边收边拼
        pending: dict[int, dict[str, Any]] = {}
        try:
            stream = await self._client.chat.completions.create(**kwargs)
            async for event in stream:
                if getattr(event, "usage", None):
                    u = event.usage
                    yield Chunk(kind="usage", usage=Usage(
                        token_in=getattr(u, "prompt_tokens", 0) or 0,
                        token_out=getattr(u, "completion_tokens", 0) or 0,
                    ))
                if not event.choices:
                    continue
                delta = event.choices[0].delta
                if delta is None:
                    continue

                # 思考模式的推理内容 —— R7「在想」状态的真实数据源
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    yield Chunk(kind="reasoning", text=reasoning)

                if delta.content:
                    yield Chunk(kind="text", text=delta.content)

                for tc in (delta.tool_calls or []):
                    slot = pending.setdefault(
                        tc.index, {"id": "", "name": "", "arguments": ""})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function and tc.function.name:
                        slot["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        slot["arguments"] += tc.function.arguments

            if pending:
                yield Chunk(kind="tool_calls",
                            tool_calls=_finish_tool_calls(pending))
            yield Chunk(kind="done")
        except OpenAIError as exc:
            raise LLMError(f"模型调用失败：{exc}") from exc

    async def complete(self, messages: Sequence[Message], *,
                       tools: Sequence[ToolSpec] | None = None,
                       thinking: bool | None = None, temperature: float | None = None,
                       json_mode: bool = False,
                       max_tokens: int | None = None) -> tuple[str, list[ToolCall], Usage]:
        """非流式。用于标题生成这类不需要展示过程的调用。"""
        kwargs = self._build_kwargs(messages, tools=tools, thinking=thinking,
                                    temperature=temperature, json_mode=json_mode,
                                    max_tokens=max_tokens)
        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except OpenAIError as exc:
            raise LLMError(f"模型调用失败：{exc}") from exc

        choice = resp.choices[0].message
        calls: list[ToolCall] = []
        for tc in (choice.tool_calls or []):
            calls.append(ToolCall(id=tc.id, name=tc.function.name,
                                  args=_safe_json(tc.function.arguments)))
        usage = Usage(
            token_in=getattr(resp.usage, "prompt_tokens", 0) or 0,
            token_out=getattr(resp.usage, "completion_tokens", 0) or 0,
        ) if resp.usage else Usage()
        return (choice.content or ""), calls, usage


def _safe_json(raw: str | None) -> dict[str, Any]:
    """工具参数解析。

    模型偶尔会吐出不合法的 JSON。这里不抛异常 —— 抛了会中断整个 Run；
    返回带 _parse_error 的字典，让编排器把错误作为 tool_result 回给模型，
    它通常下一轮就自己修好了。
    """
    if not raw:
        return {}
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {"_value": v}
    except json.JSONDecodeError as exc:
        logger.warning("工具参数不是合法 JSON: %s", raw[:200])
        return {"_parse_error": str(exc), "_raw": raw}


def _finish_tool_calls(pending: dict[int, dict[str, Any]]) -> list[ToolCall]:
    out = []
    for idx in sorted(pending):
        slot = pending[idx]
        if not slot["name"]:
            continue
        out.append(ToolCall(id=slot["id"] or f"call_{idx}", name=slot["name"],
                            args=_safe_json(slot["arguments"])))
    return out
