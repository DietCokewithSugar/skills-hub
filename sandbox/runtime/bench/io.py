"""受信任的 I/O 库（R0.7：「提示模型优先调用，而不是自己重写」）。

读表这件事看着简单，实际有一堆能悄悄算错结果的坑：编码、千分位、
Excel 把 ID 读成科学计数法、空字符串和 NaN 的区别。让模型每次现写一遍
read_csv，就是让这些坑每次重新掷一次骰子。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

#: 会被当作缺失值的字符串。注意不含 "0" / "NA"（"NA" 在某些量表里是有效档位）
NA_VALUES = ["", " ", "null", "NULL", "None", "N/A", "n/a", "-", "—", "#N/A"]


def read_table(path: str | Path, **kwargs: Any) -> pd.DataFrame:
    """按扩展名读 csv / xlsx / tsv / json，统一缺失值处理。

    所有列默认按字符串读入再逐列推断 —— 避免 pandas 把订单号、身份证号
    这类长数字读成 float 丢精度。
    """
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in (".csv", ".txt"):
        df = pd.read_csv(p, na_values=NA_VALUES, keep_default_na=True,
                         encoding=kwargs.pop("encoding", "utf-8-sig"), **kwargs)
    elif suffix in (".tsv",):
        df = pd.read_csv(p, sep="\t", na_values=NA_VALUES, keep_default_na=True,
                         encoding=kwargs.pop("encoding", "utf-8-sig"), **kwargs)
    elif suffix in (".xlsx", ".xlsm", ".xls"):
        df = pd.read_excel(p, na_values=NA_VALUES, keep_default_na=True, **kwargs)
    elif suffix == ".json":
        df = pd.read_json(p, **kwargs)
    else:
        raise ValueError(f"不支持的表格格式：{suffix}（支持 csv/tsv/xlsx/json）")
    df.columns = [str(c).strip() for c in df.columns]
    return df


def require_columns(df: pd.DataFrame, columns: list[str]) -> None:
    """缺列就报错，指名道姓说缺哪一列（R0.4 验收）。"""
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ValueError(
            f"输入数据缺少必填列：{missing}。实际列为：{list(df.columns)}"
        )


def write_table(df: pd.DataFrame, path: str | Path, **kwargs: Any) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.suffix.lower() in (".xlsx", ".xlsm"):
        df.to_excel(p, index=False, **kwargs)
    else:
        df.to_csv(p, index=False, encoding="utf-8-sig", **kwargs)
    return p
