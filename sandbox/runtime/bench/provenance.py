"""来源标注（R0.3：数据留痕）。

「result.json 中每个指标附带 source：来自哪个输入文件、哪些行、
经过哪个计算步骤。」

用法：

    result = {"metrics": {
        "nps": bench.Metric(41.5, source=bench.source("raw.csv", rows=[2, 201],
                                                      step="compute")),
    }}

Metric 是个 dict 子类，json.dumps 直接就能序列化成平台期望的形状。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _logical_name(file: str | Path) -> str:
    """记逻辑文件名，不记临时工作区的绝对路径。

    工作区目录每次执行都不同（/tmp/bench-fx-xxxx/raw.csv），把它写进
    result.json 会让来源标注每次都变 —— 既没法比对，展示给用户也没有意义。
    在工作区内的文件记相对路径，其余记原样。
    """
    p = Path(file)
    for env in ("BENCH_WORKSPACE", "BENCH_OUTPUT"):
        root = os.environ.get(env)
        if not root:
            continue
        try:
            return p.resolve().relative_to(Path(root).resolve()).as_posix()
        except (ValueError, OSError):
            continue
    return p.name if p.is_absolute() else str(file)


def source(file: str | Path, *, rows: list[int] | None = None,
           step: str | None = None, note: str = "",
           hash_file: bool = True) -> dict[str, Any]:
    """构造一条来源标注。

    file 若是存在的真实路径，默认连哈希一起记 —— 「同一份数据 + 同一版
    skill = 同一份报告」这句话需要哈希才能验证。记录的路径是工作区相对
    路径，因此同样的输入在不同机器上跑出的来源标注是一致的。
    """
    src: dict[str, Any] = {"file": _logical_name(file)}
    if rows is not None:
        src["rows"] = list(rows)
    if step:
        src["step"] = step
    if note:
        src["note"] = note
    p = Path(file)
    if hash_file and p.exists() and p.is_file():
        src["file_sha256"] = file_sha256(p)
    return src


class Metric(dict):
    """一个带来源的数值。"""

    def __init__(self, value: Any, *, source: dict[str, Any],
                 unit: str = "", label: str = "") -> None:
        payload: dict[str, Any] = {"value": value, "source": source}
        if unit:
            payload["unit"] = unit
        if label:
            payload["label"] = label
        super().__init__(payload)

    @property
    def value(self) -> Any:
        return self["value"]
