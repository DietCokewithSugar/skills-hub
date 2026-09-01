"""Part 写入与事件发布的唯一入口。

编排器不直接碰 PartRepo 或 EventBus，只用 Emitter。这样「先落库拿 seq，
再发 Redis」这条顺序只在一个地方实现，不会在某个调用点被写反 ——
写反了就会出现客户端收到 seq=5 但库里还没有 5 的窗口，断线重连时那条
就永远丢了（R2 验收：不重不漏）。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from bench.db.repo.parts import PartRepo
from bench.events.bus import EventBus, get_bus
from bench.events.types import Event, EventType


class Emitter:
    def __init__(self, db: AsyncSession, session_id: uuid.UUID | str,
                 user_id: uuid.UUID | str, *, bus: EventBus | None = None) -> None:
        self.db = db
        self.session_id = str(session_id)
        self.parts = PartRepo(db, user_id)
        self.bus = bus or get_bus()
        self._message_id: uuid.UUID | None = None
        #: 当前正在执行的 step。落 Part 时盖进 payload —— 这样刷新页面重放时
        #: 仍然知道每段内容属于哪一道工序（7.4 的 spine 是按工序组织的）。
        #: step 事件本身是瞬时的、不落库，光靠它们回放不出时间轴。
        self.current_step: str | None = None

    async def open_message(self, role: str = "assistant") -> uuid.UUID:
        msg = await self.parts.create_message(self.session_id, role)
        self._message_id = msg.id
        return msg.id

    async def _ensure_message(self) -> uuid.UUID:
        if self._message_id is None:
            await self.open_message()
        assert self._message_id is not None
        return self._message_id

    async def part(self, type: str, payload: dict[str, Any], *,
                   event: EventType = EventType.PART_CREATED) -> tuple[uuid.UUID, int]:
        """落一个 Part 并广播。返回 (part_id, seq)。

        commit 在 publish 之前：订阅端收到 seq 时，库里一定已经能读到它。
        """
        message_id = await self._ensure_message()
        if self.current_step and "step_id" not in payload:
            payload = {**payload, "step_id": self.current_step}
        part = await self.parts.append(
            session_id=self.session_id, message_id=message_id,
            type=type, payload=payload,
        )
        part_id, seq = part.id, part.seq
        await self.db.commit()
        await self.bus.publish(
            self.session_id,
            Event(type=event, seq=seq,
                  data={"part_id": str(part_id), "type": type, "payload": payload}),
        )
        return part_id, seq

    async def finalize_part(self, part_id: uuid.UUID, seq: int,
                            payload: dict[str, Any], part_type: str) -> None:
        """流式片段收尾：就地定稿 payload，不新增 seq。"""
        await self.parts.update_payload(part_id, payload)
        await self.db.commit()
        await self.bus.publish(
            self.session_id,
            Event(type=EventType.PART_COMPLETED, seq=seq,
                  data={"part_id": str(part_id), "type": part_type, "payload": payload}),
        )

    async def signal(self, type: EventType, data: dict[str, Any] | None = None) -> None:
        """发一个不落库的瞬时事件（进度、日志尾行、心跳、run 状态变化）。"""
        await self.bus.publish(self.session_id, Event(type=type, data=data or {}))

    # ── 便捷方法：编排器高频使用的几种 Part ─────────────────────

    async def text(self, text: str) -> tuple[uuid.UUID, int]:
        return await self.part("text", {"text": text})

    async def reasoning(self, text: str) -> tuple[uuid.UUID, int]:
        return await self.part("reasoning", {"text": text})

    async def error(self, *, step_id: str | None, message: str,
                    detail: dict[str, Any] | None = None,
                    retryable: bool = True) -> tuple[uuid.UUID, int]:
        """失败也是历史的一部分（R2），所以错误落 Part 而不只是日志。

        payload 直接对应 7.7 的三段式错误展示：哪一步、什么原因、下一步怎么办。
        """
        return await self.part("error", {
            "step_id": step_id, "message": message,
            "detail": detail or {}, "retryable": retryable,
        })

    async def artifact(self, *, artifact_id: uuid.UUID, filename: str, mime: str,
                       size_bytes: int, track: str = "trusted",
                       has_preview: bool = False) -> tuple[uuid.UUID, int]:
        """产物是 Part，不是执行的副作用（PRD 3.2 关键设计约束）。

        因此任何时候回看历史都能重新下载 —— 下载入口就长在消息流里。
        """
        return await self.part("artifact", {
            "artifact_id": str(artifact_id), "filename": filename, "mime": mime,
            "size_bytes": size_bytes, "track": track, "has_preview": has_preview,
        })

    async def tool_call(self, *, name: str, args: dict[str, Any],
                        code: str | None = None,
                        track: str = "trusted") -> tuple[uuid.UUID, int]:
        """R9：模型生成的代码全文写入历史，永久留痕，随时可复查。"""
        return await self.part("tool_call", {
            "name": name, "args": args, "code": code, "track": track,
        })

    async def tool_result(self, *, name: str, ok: bool, result: Any,
                          track: str = "trusted",
                          stdout: str = "", stderr: str = "") -> tuple[uuid.UUID, int]:
        return await self.part("tool_result", {
            "name": name, "ok": ok, "result": result, "track": track,
            "stdout": stdout, "stderr": stderr,
        })
