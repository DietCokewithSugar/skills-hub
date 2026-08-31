"""stdout JSONL 进度协议（PRD R4）。

    {"type":"progress","pct":40,"msg":"正在聚合"}

脚本的 stdout 里混着 print 调试输出很正常，所以解析必须宽容：
不是合法 JSON 的行当普通日志，不报错、不中断执行。反过来，
一行合法的进度事件也不该被当成日志吞掉 —— R7 要求「有进度百分比时
显示细进度条，无则不显示假进度」，进度是真数据，不能猜。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(slots=True)
class ProgressEvent:
    kind: Literal["progress", "log", "result"]
    pct: float | None = None
    msg: str = ""
    data: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind, "msg": self.msg}
        if self.pct is not None:
            d["pct"] = self.pct
        if self.data is not None:
            d["data"] = self.data
        return d


def parse_stdout_line(line: str) -> ProgressEvent:
    """把一行 stdout 解析成事件。不是 JSON 就当日志。"""
    raw = line.rstrip("\n")
    stripped = raw.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return ProgressEvent(kind="log", msg=raw)
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return ProgressEvent(kind="log", msg=raw)
    if not isinstance(obj, dict):
        return ProgressEvent(kind="log", msg=raw)

    t = obj.get("type")
    if t == "progress":
        pct = obj.get("pct")
        try:
            pct = None if pct is None else max(0.0, min(100.0, float(pct)))
        except (TypeError, ValueError):
            pct = None
        return ProgressEvent(kind="progress", pct=pct, msg=str(obj.get("msg", "")))
    if t == "result":
        return ProgressEvent(kind="result", data=obj.get("data"),
                             msg=str(obj.get("msg", "")))
    return ProgressEvent(kind="log", msg=raw)
