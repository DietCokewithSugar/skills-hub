"""SSE 事件类型（PRD 6.3）。

事件在线上的形状是：
    id: <seq>
    event: <type>
    data: <json>

`id` 用 Part 的 seq —— 因此浏览器 EventSource 断线重连时自动带上的
Last-Event-ID 就是「我已经收到第几号」，服务端据此补发。没有 seq 的
纯瞬时事件（step.progress、heartbeat）不带 id，不参与补发：它们描述的是
「此刻」，补发一条 30 秒前的进度百分比没有意义，也不该顶掉 Last-Event-ID。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    # Run 生命周期
    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"
    RUN_WAITING = "run.waiting"
    RUN_RESUMED = "run.resumed"
    RUN_EXPIRED = "run.expired"

    # Part 流
    PART_CREATED = "part.created"
    PART_DELTA = "part.delta"
    PART_COMPLETED = "part.completed"

    # Step
    STEP_STARTED = "step.started"
    STEP_PROGRESS = "step.progress"
    STEP_COMPLETED = "step.completed"
    STEP_FAILED = "step.failed"
    STEP_LOG = "step.log"

    # 卡片
    CARD_REQUESTED = "card.requested"
    CARD_ANSWERED = "card.answered"
    CARD_EXPIRED = "card.expired"

    # 其他
    ARTIFACT_CREATED = "artifact.created"
    USAGE_UPDATED = "usage.updated"
    HEARTBEAT = "heartbeat"


#: 这些事件描述瞬时状态，不落库、不带 seq、不参与断线补发。
TRANSIENT: frozenset[EventType] = frozenset({
    EventType.STEP_PROGRESS,
    EventType.STEP_LOG,
    EventType.PART_DELTA,
    EventType.HEARTBEAT,
    EventType.USAGE_UPDATED,
})


@dataclass(slots=True)
class Event:
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)
    seq: int | None = None
    session_id: str | None = None

    @property
    def is_transient(self) -> bool:
        return self.type in TRANSIENT

    def to_sse(self) -> str:
        """序列化为 SSE 线格式。"""
        lines: list[str] = []
        if self.seq is not None and not self.is_transient:
            lines.append(f"id: {self.seq}")
        lines.append(f"event: {self.type.value}")
        lines.append(f"data: {json.dumps(self.data, ensure_ascii=False, default=str)}")
        return "\n".join(lines) + "\n\n"

    def to_json(self) -> str:
        return json.dumps(
            {"type": self.type.value, "data": self.data,
             "seq": self.seq, "session_id": self.session_id},
            ensure_ascii=False, default=str,
        )

    @classmethod
    def from_json(cls, raw: str | bytes) -> Event:
        obj = json.loads(raw)
        return cls(type=EventType(obj["type"]), data=obj.get("data") or {},
                   seq=obj.get("seq"), session_id=obj.get("session_id"))
