"""闸门四：数据前置检查（R0.4）。

「数据异常（空列、异常值比例超阈值、样本量低于 skill 声明的下限）时
中断执行并报告，而不是照算不误。」

验收标准第 2 条：「人为构造一份缺少必填列的输入，平台在计算前中断并
明确指出缺哪一列，不产出报告。」注意「在计算前」—— 所以这一关跑在
python step 之前，而不是让脚本自己去发现。

成功指标：异常输入拦截率 ≥ 95% 在计算前被拦下。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bench.skills.manifest import DataContract


class PreflightError(Exception):
    def __init__(self, problems: list[str], *, file: str = "") -> None:
        self.problems = problems
        self.file = file
        head = f"输入数据未通过前置检查（{file}）" if file else "输入数据未通过前置检查"
        super().__init__(head + "：\n" + "\n".join(f"  · {p}" for p in problems))

    def to_payload(self) -> dict[str, Any]:
        return {"kind": "preflight", "file": self.file, "problems": self.problems}


@dataclass(slots=True)
class PreflightReport:
    rows: int = 0
    columns: list[str] = field(default_factory=list)
    null_ratios: dict[str, float] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def inspect_table(path: Path) -> tuple[Any, PreflightReport]:
    """读表并给出基础体检报告。"""
    import pandas as pd  # 局部导入：pandas 启动慢，API 进程不该为它买单

    report = PreflightReport()
    suffix = path.suffix.lower()
    try:
        if suffix in (".csv", ".txt"):
            df = pd.read_csv(path, encoding="utf-8-sig")
        elif suffix == ".tsv":
            df = pd.read_csv(path, sep="\t", encoding="utf-8-sig")
        elif suffix in (".xlsx", ".xlsm", ".xls"):
            df = pd.read_excel(path)
        elif suffix == ".json":
            df = pd.read_json(path)
        else:
            report.problems.append(f"不支持的文件格式 {suffix}（支持 csv/tsv/xlsx/json）")
            return None, report
    except Exception as exc:  # noqa: BLE001
        report.problems.append(f"文件无法解析：{exc}")
        return None, report

    df.columns = [str(c).strip() for c in df.columns]
    report.rows = int(len(df))
    report.columns = list(df.columns)
    report.null_ratios = {
        c: round(float(df[c].isna().mean()), 4) for c in df.columns
    }
    return df, report


def run_preflight(path: Path, contract: DataContract | None) -> PreflightReport:
    """按 skill 声明的数据契约体检。不通过就抛，不进计算。"""
    df, report = inspect_table(path)
    if df is None:
        raise PreflightError(report.problems, file=path.name)
    if contract is None:
        return report

    # ── 必填列 ──「明确指出缺哪一列」是验收标准的原话 ──
    missing = [c for c in contract.required_columns if c not in report.columns]
    if missing:
        report.problems.append(
            f"缺少必填列 {missing}；文件实际的列是 {report.columns}"
        )

    # ── 样本量下限 ──
    if contract.min_rows and report.rows < contract.min_rows:
        report.problems.append(
            f"有效样本量 {report.rows} 低于该 skill 声明的下限 {contract.min_rows}"
        )

    # ── 空列与空值率 ──
    for col in contract.required_columns:
        if col not in report.columns:
            continue
        ratio = report.null_ratios.get(col, 0.0)
        if ratio >= 1.0:
            report.problems.append(f"必填列 {col!r} 整列为空")
        elif ratio > contract.max_null_ratio:
            report.problems.append(
                f"列 {col!r} 空值率 {ratio:.1%} 超过阈值 {contract.max_null_ratio:.1%}"
            )

    if report.problems:
        raise PreflightError(report.problems, file=path.name)
    return report
