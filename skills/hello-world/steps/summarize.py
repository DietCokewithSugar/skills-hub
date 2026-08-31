"""最小 skill：数行列，写摘要。

演示 R4 契约的全部要素：读 params、报进度、写产物、emit_result（带来源标注）。
"""
import bench

params = bench.params()
bench.progress(10, "读取输入")

name = params.get("raw_data") or "raw.csv"
path = bench.workspace() / name
df = bench.io.read_table(path)

bench.progress(60, "统计中")
rows, cols = len(df), len(df.columns)

# 每个数值都带来源 —— R0.3 要求 metrics 下的数值 100% 可追溯
src = bench.source(path, rows=[0, max(rows - 1, 0)], step="summarize")
result = {
    "metrics": {
        "row_count": bench.Metric(rows, source=src, label="行数"),
        "column_count": bench.Metric(cols, source=src, label="列数"),
    },
    "meta": {"columns": list(df.columns), "file": name},
}

(bench.output() / "summary.txt").write_text(
    f"文件：{name}\n行数：{rows}\n列数：{cols}\n列名：{'、'.join(map(str, df.columns))}\n",
    encoding="utf-8",
)
bench.emit_result(result)
bench.progress(100, "完成")
