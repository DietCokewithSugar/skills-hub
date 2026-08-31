"""LLM 抽象层。

PRD 6.5：「代码里只出现一个 LLMClient，便于将来替换成行内网关」。
编排器只依赖这个接口，不 import openai —— 换成行内网关时改这一个文件。

R0.6：叙述性生成用低 temperature；结构化输出用 JSON mode。两者都在这里
固化成默认值，而不是散落在各个调用点靠自觉。
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

logger = logging.getLogger(__name__)


class LLMError(Exception):
    pass


@dataclass(slots=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None

    def to_wire(self) -> dict[str, Any]:
        m: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            m["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.name, "arguments": json.dumps(tc.args, ensure_ascii=False)}}
                for tc in self.tool_calls
            ]
        if self.tool_call_id:
            m["tool_call_id"] = self.tool_call_id
        if self.name:
            m["name"] = self.name
        return m


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]

    def to_wire(self) -> dict[str, Any]:
        return {"type": "function",
                "function": {"name": self.name, "description": self.description,
                             "parameters": self.parameters}}


@dataclass(slots=True)
class Usage:
    token_in: int = 0
    token_out: int = 0


@dataclass(slots=True)
class Chunk:
    """流式片段。

    reasoning 与 text 分开，是因为 R7 要求它们在界面上是两种东西：
    推理内容折叠成「在想」，正文是正式输出。混成一路就没法分开渲染了。
    """

    kind: Literal["reasoning", "text", "tool_calls", "usage", "done"]
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage | None = None


class LLMClient(Protocol):
    async def stream(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
        thinking: bool | None = None,
        temperature: float | None = None,
        json_mode: bool = False,
        max_tokens: int | None = None,
    ) -> AsyncIterator[Chunk]: ...

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
        thinking: bool | None = None,
        temperature: float | None = None,
        json_mode: bool = False,
        max_tokens: int | None = None,
    ) -> tuple[str, list[ToolCall], Usage]: ...


def get_client() -> LLMClient:
    from bench.llm.deepseek import DeepSeekClient
    return DeepSeekClient()
