"""计算体验评测指标。

R0.1 的落点：这个文件里发生**所有**计算。模型不碰数字，只在下一步
根据这里算出的 result.json 写叙述。

每个指标都用 bench.stats 里已审核的函数算，并带上来源标注（哪个文件、
哪些行、哪一步）—— R0.3 要求 100% 可追溯。
"""
import bench
from bench import asserts

params = bench.params()
bench.progress(5, "读取输入")

filename = params.get("raw_data") or "raw.csv"
path = bench.workspace() / filename
df = bench.io.read_table(path)

# 卡片答案通过 params.json 注入（R5 协议第 6 步）
scope = params.get("scope", "注册流程")
min_sample = int(params.get("sample", 200))
csat_threshold = float(params.get("csat_threshold", 4))

bench.io.require_columns(df, ["scope", "nps", "csat", "task_success"])
total_rows = len(df)

bench.progress(25, f"筛选评测范围：{scope}")
subset = df[df["scope"].astype(str).str.strip() == scope]
asserts.non_empty(subset, name=f"范围 {scope!r} 的样本")

# R0.4：样本量不足不硬算，中断并说明。宁可报错，不可产出一份数字没意义的报告。
if len(subset) < min_sample:
    bench.fail(
        f"范围 {scope!r} 的有效样本量为 {len(subset)}，低于设定下限 {min_sample}",
        detail={"scope": scope, "actual": len(subset), "required": min_sample,
                "hint": "可以调低确认卡片中的样本量下限，或更换评测范围"},
    )

bench.progress(50, "计算指标")
src = lambda rows: bench.source(path, rows=rows, step="compute")   # noqa: E731

nps = bench.stats.nps(subset["nps"], ndigits=1)          # 报告里 NPS 惯例保留一位小数
csat = bench.stats.csat(subset["csat"], satisfied_min=csat_threshold)
success = bench.stats.ratio(
    subset["task_success"].astype(str).str.strip().isin(["1", "是", "true", "True", "yes"])
)

# 断言：分项之和必须等于有效样本数（R0.7 的总和校验）
asserts.in_range(nps.value, -100, 100, name="NPS")
asserts.in_range(csat.value, 0, 1, name="CSAT")
asserts.in_range(success.value, 0, 1, name="任务完成率")

bench.progress(75, "分组统计")
by_scope = {}
for name, group in df.groupby(df["scope"].astype(str).str.strip()):
    # 小样本分组不单独出结论（refs/methodology.md 第三节，硬性要求）
    if len(group) < min_sample:
        continue
    try:
        g = bench.stats.nps(group["nps"], ndigits=1)
    except ValueError:
        continue
    by_scope[str(name)] = bench.Metric(
        g.value, source=src([int(i) for i in group.index[:2]]),
        label=f"{name} NPS")

result = {
    "metrics": {
        "nps": bench.Metric(nps.value, source=src(nps.rows), label="净推荐值"),
        "csat": bench.Metric(csat.value, source=src(csat.rows), label="满意度"),
        "task_success": bench.Metric(success.value, source=src(success.rows),
                                     label="任务完成率"),
        "sample_size": bench.Metric(nps.n, source=src(nps.rows), label="有效样本量"),
        "by_scope": by_scope,
    },
    "meta": {
        "scope": scope,
        "min_sample": min_sample,
        "csat_threshold": csat_threshold,
        "total_rows": total_rows,
        "file": filename,
        "columns": list(df.columns),
    },
}

bench.emit_result(result)
bench.progress(100, "计算完成")
