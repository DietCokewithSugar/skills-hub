"""闸门二：来源覆盖校验（R0.3 数据留痕）。

「result.json 中每个指标附带 source：来自哪个输入文件、哪些行、
经过哪个计算步骤。」

成功指标里有一条是「报告数字可追溯率 100%」。要让这个数字为真，
不能靠 skill 作者自觉 —— 得有一道闸门在这里数：metrics 下每一个数值叶子
是不是都带了 source。少一个就让这一步失败。

约定的形状：

    {"metrics": {
        "nps": {"value": 41.5, "source": {"file": "raw.csv", "rows": [2, 201],
                                          "step": "compute",
                                          "file_sha256": "…"}},
        "by_scope": {"注册流程": {"value": 0.82, "source": {...}}}
    }}

嵌套任意层，只要叶子是 {"value": ..., "source": {...}} 就算合规。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

#: source 里至少要有这两项，否则「来自哪个文件、经过哪一步」就答不上来
REQUIRED_SOURCE_KEYS = ("file", "step")


class ProvenanceError(Exception):
    def __init__(self, problems: list[str], *, coverage: float = 0.0) -> None:
        self.problems = problems
        self.coverage = coverage
        super().__init__(
            f"来源标注不完整（覆盖率 {coverage:.0%}）：\n"
            + "\n".join(f"  · {p}" for p in problems)
        )

    def to_payload(self) -> dict[str, Any]:
        return {"kind": "provenance", "problems": self.problems,
                "coverage": self.coverage}


def _is_metric(node: Any) -> bool:
    return isinstance(node, dict) and "value" in node


def iter_metrics(result: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """遍历 metrics 下所有指标叶子，产出 (路径, 节点)。"""
    metrics = result.get("metrics")
    if not isinstance(metrics, dict):
        return

    def walk(node: Any, path: str) -> Iterator[tuple[str, dict[str, Any]]]:
        if _is_metric(node):
            yield path, node
            return
        if isinstance(node, dict):
            for k, v in node.items():
                yield from walk(v, f"{path}.{k}" if path else str(k))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                yield from walk(v, f"{path}[{i}]")

    yield from walk(metrics, "")


def check_provenance(result: dict[str, Any], *, step_id: str = "",
                     require_full_coverage: bool = True,
                     require_metrics: bool = True) -> float:
    """检查来源覆盖。返回覆盖率；不达标时抛 ProvenanceError。

    职责分工：**schema 管「什么必须存在」，本函数管「存在的东西是否有来源」。**
    所以 require_metrics 默认 True 只服务于显式检查报告结果的场景；
    step 执行路径传 False —— 一个 render 步骤本来就不产生指标，
    它该不该有 metrics 由它自己的 output_schema 说了算，不该由这里越权判断。
    """
    items = list(iter_metrics(result))
    if not items:
        if require_metrics and require_full_coverage:
            raise ProvenanceError(
                ["result.json 里没有 metrics —— 报告要用的数值必须放在 metrics 下"
                 "并带来源标注，否则无法追溯"],
                coverage=0.0,
            )
        return 1.0

    problems: list[str] = []
    ok = 0
    for path, node in items:
        src = node.get("source")
        if not isinstance(src, dict):
            problems.append(f"metrics.{path} 缺少 source 标注")
            continue
        missing = [k for k in REQUIRED_SOURCE_KEYS if not src.get(k)]
        if missing:
            problems.append(f"metrics.{path} 的 source 缺少 {missing}")
            continue
        ok += 1

    coverage = ok / len(items)
    if problems and require_full_coverage:
        raise ProvenanceError(problems, coverage=coverage)
    return coverage


#: 这些 key 下的数字是内部记账，不是可以被引用的数字。
#: 尤其是 source.rows —— 它可能包含 0..N 的全部行号，一旦并入允许集合，
#: 几乎任何小整数都会被判为「合法引用」，数字越界拦截就形同虚设。
#: （这个洞在端到端测试里真实出现过：240 行数据让 0–241 全部成了合法数字。）
_NON_QUOTABLE_KEYS = frozenset({"source", "rows", "file_sha256", "columns"})


def allowed_numbers(result: dict[str, Any]) -> set[float]:
    """可以被叙述引用的数值集合 —— 数字越界拦截的比对基准。

    只收两类：
      1. metrics 下每个指标的 value（这是计算结果本身）；
      2. meta 下的标量配置与统计（总行数、样本下限、阈值 —— 「共回收 540 份」
         这类表述需要它们）。

    **不收** source 里的行号与哈希：那是追溯用的记账数据，不是结论。
    """
    out: set[float] = set()

    def add(v: Any) -> None:
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, (int, float)):
            out.add(float(v))
        elif isinstance(v, str):
            try:
                out.add(float(v))
            except ValueError:
                pass

    # ① 指标值
    for _, node in iter_metrics(result):
        add(node.get("value"))

    # ② meta 里的标量（跳过不可引用的 key 与嵌套容器）
    def walk_meta(node: Any, key: str = "") -> None:
        if key in _NON_QUOTABLE_KEYS:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                walk_meta(v, k)
        elif isinstance(node, list):
            return          # meta 里的列表是列名之类，不是可引用的数字
        else:
            add(node)

    walk_meta(result.get("meta") or {})
    return out


def trace(result: dict[str, Any], metric_path: str) -> dict[str, Any] | None:
    """按路径取某个指标的来源 —— 支撑「点击查看来源」（v1 先只存不展示）。"""
    for path, node in iter_metrics(result):
        if path == metric_path:
            return node.get("source")
    return None
