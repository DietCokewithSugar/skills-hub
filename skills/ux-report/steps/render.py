"""渲染 Word 报告。

R0.1：「报告模板负责把校验过的结构化数据填进版式，绝对不许二次加工数据。」
所以这一步只做三件事：画图、把 result.json 的值原样塞进模板、保存。
没有任何一处重新计算 —— 需要的数早在 compute 步骤算完并通过校验了。
"""
import json

import bench
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from docx.shared import Mm
from docxtpl import DocxTemplate, InlineImage

# 中文字体：容器里通常没有，退回 DejaVu 并关掉负号的 unicode 形式，
# 至少保证图不出方框乱码
matplotlib.rcParams["font.sans-serif"] = [
    "Noto Sans CJK SC", "WenQuanYi Zen Hei", "SimHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

params = bench.params()
bench.progress(10, "读取计算结果")

# 前序步骤的输出通过 params._steps 注入（见 RunContext.step_params）
steps = params.get("_steps") or {}
computed = steps.get("compute") or {}
if not computed:
    bench.fail("拿不到 compute 步骤的结果，无法渲染报告",
               detail={"hint": "确认 compute 步骤已成功并写出了 result.json"})

metrics = computed["metrics"]
meta = computed.get("meta", {})
narrative = steps.get("narrate") or {}


def val(node):
    """从 {"value":…, "source":…} 里取值。只取，不算。"""
    return node["value"] if isinstance(node, dict) else node


bench.progress(35, "绘制图表")
chart_path = bench.output() / "chart.png"
by_scope = metrics.get("by_scope") or {}
fig, ax = plt.subplots(figsize=(7.2, 3.4), dpi=150)
if by_scope:
    names = list(by_scope)
    values = [val(by_scope[n]) for n in names]
    bars = ax.bar(names, values, color="#1F5FA8", width=0.5)
    ax.set_ylabel("NPS")
    ax.axhline(0, color="#5C6266", linewidth=0.8)
    for b, v in zip(bars, values):
        ax.text(b.get_x() + b.get_width() / 2, v, f"{v:g}",
                ha="center", va="bottom" if v >= 0 else "top", fontsize=9)
else:
    ax.text(0.5, 0.5, "无满足样本量要求的分组", ha="center", va="center",
            transform=ax.transAxes, color="#5C6266")
    ax.set_axis_off()
ax.spines[["top", "right"]].set_visible(False)
fig.tight_layout()
fig.savefig(chart_path)
plt.close(fig)

bench.progress(60, "渲染文档")
template = bench.workspace() / "templates" / "report.docx"
if not template.exists():
    bench.fail(f"报告模板不存在：{template}")

doc = DocxTemplate(str(template))
context = {
    "scope": meta.get("scope", ""),
    "sample_size": val(metrics["sample_size"]),
    "nps": val(metrics["nps"]),
    # 百分比在这里只做展示格式化，不改变数值本身
    "csat_pct": round(val(metrics["csat"]) * 100, 1),
    "task_success_pct": round(val(metrics["task_success"]) * 100, 1),
    "by_scope": [{"name": k, "nps": val(v)} for k, v in by_scope.items()],
    "summary": narrative.get("summary", ""),
    "findings": narrative.get("findings", []),
    "recommendations": narrative.get("recommendations", []),
    "chart": InlineImage(doc, str(chart_path), width=Mm(160)),
    "total_rows": meta.get("total_rows", 0),
    "file": meta.get("file", ""),
}
doc.render(context)
out = bench.output() / "report.docx"
doc.save(str(out))

bench.progress(90, "记录产出")
# render 步骤不产生新指标，但要把用到的值原样回传，便于成品复核
# render 不产生新指标，只记录它用了哪些 —— 便于成品复核时对账
bench.emit_result({
    "meta": {"rendered": "output/report.docx",
             "used_metrics": sorted(k for k in metrics if k != "by_scope")},
})
bench.progress(100, "报告已生成")
