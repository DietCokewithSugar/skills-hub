"""闸门三：数字越界拦截（R0.1）。

「模型只能引用计算结果，不能生成计算结果。所有进入报告的数值，
来源必须是 result.json 中的字段。」

成功指标里「模型越界改写数字的发生次数 = 0（通过校验强制拦截）」——
「强制拦截」四个字意味着这不能是 prompt 里的一句叮嘱。做法是：
把 LLM 生成的叙述文本里的数字全部抠出来，逐个比对 result.json 的允许集合，
出现集合外的数字就判越界。

**关于误报**：叙述里合理出现的数字不止指标值本身，还有年份、序号、
百分比的另一种写法（0.82 ↔ 82%）、四舍五入后的形式（41.5 ↔ 41.5%、42）。
下面的 `_candidates` 就是在处理这些等价形式。宁可漏判个别边缘情况，
也不能让「把 41.5 写成 45」这种真正的篡改溜过去 —— 所以等价形式是
显式枚举的，不是模糊匹配。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: 匹配数字：支持千分位、小数、负号、百分号
_NUMBER_RE = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?|-?\d+(?:\.\d+)?%?")

#: 叙述里天然合法、不必来自 result.json 的数字。
#: 年份和小整数（一、二、三这类序数与列表编号）属于行文需要。
_FREE_INTEGERS = set(range(0, 11))
_YEAR_RANGE = range(1900, 2200)


@dataclass(slots=True)
class NumberGuardResult:
    ok: bool
    #: 越界的数字原文
    violations: list[str] = field(default_factory=list)
    #: 检查过的数字总数
    checked: int = 0

    def message(self) -> str:
        return (
            "叙述中出现了 result.json 里没有的数字："
            + "、".join(self.violations)
            + "。模型只能引用计算结果，不能生成计算结果。"
        )


def extract_numbers(text: str) -> list[str]:
    """抠出文本里所有数字（保留原文形式，便于报错时指名道姓）。"""
    return _NUMBER_RE.findall(text or "")


def _to_float(token: str) -> float | None:
    t = token.replace(",", "").rstrip("%")
    try:
        return float(t)
    except ValueError:
        return None


def _candidates(token: str) -> set[float]:
    """一个数字 token 在 result.json 里可能对应的所有等价形式。

    例：叙述写 "82%"，result.json 里可能存的是 0.82 或 82。
        叙述写 "41.5"，可能对应 41.5 或（四舍五入前的）41.52。
    """
    v = _to_float(token)
    if v is None:
        return set()
    out = {v}
    if token.endswith("%"):
        out.add(v / 100.0)          # 82% ↔ 0.82
    else:
        out.add(v * 100.0)          # 0.82 ↔ 82
    return out


#: 允许的小数位数。R0.1 禁止的是「改写」，四舍五入是排除在外的
#: （「禁止四舍五入以外的改写」），所以比对时要把常规舍入形式算作等价。
_ROUNDINGS = (0, 1, 2, 3, 4)


def _equivalent_forms(a: float) -> set[float]:
    """一个允许值在叙述里可能合法出现的所有形式。

    比率 0.7042 在报告里通常写成 70.4% —— 那是「乘 100 再保留一位小数」，
    是排版而非改写。这里把这些形式一并展开，否则每个百分比都会被误判。
    真正的篡改（把 0.7042 写成 0.85）依然穿不过去，因为它不是任何一种
    舍入形式。
    """
    forms: set[float] = {a}
    scaled = a * 100.0
    forms.add(scaled)
    for nd in _ROUNDINGS:
        forms.add(round(a, nd))
        forms.add(round(scaled, nd))
    return forms


def _matches_allowed(token: str, allowed: set[float], *, tolerance: float) -> bool:
    cands = _candidates(token)
    if not cands:
        return True
    for a in allowed:
        forms = _equivalent_forms(a)
        for c in cands:
            if any(abs(f - c) <= tolerance for f in forms):
                return True
    return False


def check_narrative(text: str, allowed: set[float], *,
                    tolerance: float = 1e-6,
                    allow_free_integers: bool = True) -> NumberGuardResult:
    """检查一段叙述文本里的数字是否都来自 result.json。

    allowed 来自 provenance.allowed_numbers(result)。
    """
    tokens = extract_numbers(text)
    violations: list[str] = []
    checked = 0

    for tok in tokens:
        v = _to_float(tok)
        if v is None:
            continue
        checked += 1
        if allow_free_integers and not tok.endswith("%"):
            if v.is_integer():
                iv = int(v)
                if iv in _FREE_INTEGERS or iv in _YEAR_RANGE:
                    continue
        if not _matches_allowed(tok, allowed, tolerance=tolerance):
            violations.append(tok)

    # 去重但保留出现顺序，报错读起来更像人写的
    seen: set[str] = set()
    uniq = [v for v in violations if not (v in seen or seen.add(v))]
    return NumberGuardResult(ok=not uniq, violations=uniq, checked=checked)
