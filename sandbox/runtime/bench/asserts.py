"""断言库（R0.7：「生成的代码必须带断言，断言失败即中止并回报」）。

探索轨的代码模板会强制带上这些。它们检查的是那种「跑完了但结果是错的」
的情况 —— 行数对不上、空值突然变多、分项加起来不等于总和。
这类错误没有断言就发现不了。
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd


class AssertionFailed(Exception):
    pass


def rows_conserved(before: pd.DataFrame | int, after: pd.DataFrame | int,
                   *, label: str = "行数") -> None:
    """行数守恒：筛选/聚合之后行数应当符合预期。"""
    a = before if isinstance(before, int) else len(before)
    b = after if isinstance(after, int) else len(after)
    if a != b:
        raise AssertionFailed(f"{label}不守恒：处理前 {a} 行，处理后 {b} 行")


def no_rows_lost(before: pd.DataFrame, after: pd.DataFrame,
                 *, max_drop_ratio: float = 0.0) -> None:
    a, b = len(before), len(after)
    if a == 0:
        return
    drop = (a - b) / a
    if drop > max_drop_ratio:
        raise AssertionFailed(
            f"丢行比例 {drop:.2%} 超过允许的 {max_drop_ratio:.2%}（{a} → {b}）"
        )


def null_ratio_below(series: pd.Series, threshold: float, *, name: str = "") -> None:
    r = float(series.isna().mean()) if len(series) else 0.0
    if r > threshold:
        raise AssertionFailed(
            f"列 {name or series.name!r} 空值率 {r:.2%} 超过阈值 {threshold:.2%}"
        )


def sums_to(parts: list[float] | pd.Series, total: float, *,
            tolerance: float = 1e-6, label: str = "分项") -> None:
    """总和校验：分项之和必须等于总数。"""
    s = float(sum(parts))
    if not math.isclose(s, float(total), abs_tol=tolerance):
        raise AssertionFailed(f"{label}之和 {s} 不等于总数 {total}（容差 {tolerance}）")


def in_range(value: float, lo: float, hi: float, *, name: str = "值") -> None:
    if not (lo <= value <= hi):
        raise AssertionFailed(f"{name} = {value} 超出合理区间 [{lo}, {hi}]")


def non_empty(obj: Any, *, name: str = "结果") -> None:
    if obj is None or (hasattr(obj, "__len__") and len(obj) == 0):
        raise AssertionFailed(f"{name}为空")
