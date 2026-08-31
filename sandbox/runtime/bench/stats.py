"""受信任的统计库（R0.7）。

每个函数都返回**数值 + 参与计算的行号**，因为 R0.3 要求指标能追溯到
「哪些行」。让调用方事后再去补行号，多半就补不上了。

口径都写在文档字符串里 —— 「NPS 怎么算」这种事不同人有不同习惯，
写死在这里，配合 fixtures 回归，才是 R0.5 说的那种长期保证。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(slots=True)
class Computed:
    """一个计算结果：值 + 参与计算的行号（0-based，对应原始 DataFrame 索引）。"""

    value: float
    rows: list[int]
    n: int

    def as_metric(self, *, file: str, step: str, unit: str = "",
                  label: str = "") -> dict[str, Any]:
        from bench.provenance import Metric, source
        return Metric(self.value, unit=unit, label=label,
                      source=source(file, rows=self.rows, step=step,
                                    note=f"n={self.n}"))


def _clean(series: pd.Series) -> tuple[pd.Series, list[int]]:
    s = pd.to_numeric(series, errors="coerce").dropna()
    return s, [int(i) for i in s.index]


def mean(series: pd.Series, *, ndigits: int = 4) -> Computed:
    s, rows = _clean(series)
    if s.empty:
        raise ValueError("没有有效数值可用于计算均值")
    return Computed(round(float(s.mean()), ndigits), rows, len(s))


def median(series: pd.Series, *, ndigits: int = 4) -> Computed:
    s, rows = _clean(series)
    if s.empty:
        raise ValueError("没有有效数值可用于计算中位数")
    return Computed(round(float(s.median()), ndigits), rows, len(s))


def ratio(numerator_mask: pd.Series, *, ndigits: int = 4) -> Computed:
    """布尔序列的占比，返回 0–1。"""
    mask = numerator_mask.fillna(False).astype(bool)
    n = int(len(mask))
    if n == 0:
        raise ValueError("空数据，无法计算占比")
    hit = [int(i) for i in mask[mask].index]
    return Computed(round(len(hit) / n, ndigits), hit, n)


def nps(series: pd.Series, *, ndigits: int = 4) -> Computed:
    """净推荐值。

    口径（固定，不随调用方变）：0–10 分量表；9–10 推荐者，7–8 中立者，
    0–6 贬损者。NPS = (推荐者数 − 贬损者数) / 有效样本数 × 100。
    超出 0–10 的值视为无效并剔除。
    """
    s, _ = _clean(series)
    s = s[(s >= 0) & (s <= 10)]
    n = int(len(s))
    if n == 0:
        raise ValueError("没有落在 0–10 区间的有效评分，无法计算 NPS")
    promoters = int((s >= 9).sum())
    detractors = int((s <= 6).sum())
    value = round((promoters - detractors) / n * 100, ndigits)
    return Computed(value, [int(i) for i in s.index], n)


def csat(series: pd.Series, *, satisfied_min: float = 4.0,
         ndigits: int = 4) -> Computed:
    """满意度：5 分量表中打 satisfied_min 分及以上的占比（0–1）。"""
    s, _ = _clean(series)
    n = int(len(s))
    if n == 0:
        raise ValueError("没有有效评分，无法计算满意度")
    hit = s[s >= satisfied_min]
    return Computed(round(len(hit) / n, ndigits),
                    [int(i) for i in hit.index], n)


def null_ratio(series: pd.Series, *, ndigits: int = 4) -> float:
    if len(series) == 0:
        return 0.0
    return round(float(series.isna().mean()), ndigits)


def outlier_ratio(series: pd.Series, *, iqr_factor: float = 1.5,
                  ndigits: int = 4) -> float:
    """IQR 法异常值比例。给 R0.4 的「异常值比例超阈值就中断」用。"""
    s, _ = _clean(series)
    if len(s) < 4:
        return 0.0
    q1, q3 = float(s.quantile(0.25)), float(s.quantile(0.75))
    iqr = q3 - q1
    if math.isclose(iqr, 0.0):
        return 0.0
    lo, hi = q1 - iqr_factor * iqr, q3 + iqr_factor * iqr
    return round(float(((s < lo) | (s > hi)).mean()), ndigits)


def describe(series: pd.Series, *, ndigits: int = 4) -> dict[str, float]:
    s, _ = _clean(series)
    if s.empty:
        return {"n": 0}
    return {
        "n": int(len(s)),
        "mean": round(float(s.mean()), ndigits),
        "std": round(float(s.std(ddof=1)), ndigits) if len(s) > 1 else 0.0,
        "min": round(float(s.min()), ndigits),
        "p25": round(float(s.quantile(0.25)), ndigits),
        "median": round(float(s.median()), ndigits),
        "p75": round(float(s.quantile(0.75)), ndigits),
        "max": round(float(s.max()), ndigits),
    }
