# 怎么写一个 skill

skill 是一个目录，根目录放 `skill.yaml`。放进 `skills/` 就会被注册 ——
**不用重启服务**（注册表按 mtime 重扫）。

拿 `skills/ux-report/` 当模板抄最快。

---

## 目录结构

```
skills/my-skill/
├── skill.yaml
├── steps/            python 步骤的入口脚本
├── schemas/          每个 python 步骤的 output_schema
├── cards/            interaction 步骤的卡片定义
├── templates/        docxtpl 报告模板（真正的 .docx）
├── refs/             参考资料
├── prompts/          llm 步骤的 prompt
├── fixtures/         回归测试集（**必须有**，见 accuracy.md）
└── requirements.txt  依赖，版本钉死
```

---

## skill.yaml

```yaml
id: ux-report
name: 体验评测报告
version: 0.3.0
description: 输入问卷导出数据，产出结构化体验评测 Word 报告
runtime: python-3.12

inputs:
  - key: raw_data
    label: 问卷导出文件
    type: file
    accept: [.csv, .xlsx]
    required: true

steps:
  - id: confirm_scope
    type: interaction
    label: 确认评测范围
    card: cards/scope.json
  - id: compute
    type: python
    label: 计算指标
    entry: steps/compute.py
    output_schema: schemas/compute.json      # 强烈建议：这是闸门一
  - id: narrate
    type: llm
    label: 撰写结论
    prompt: prompts/narrate.md
    response_schema: schemas/narrate.json
  - id: render
    type: python
    label: 生成报告
    entry: steps/render.py

outputs:
  - path: output/report.docx                  # 必须在 output/ 下
    label: 体验评测报告

references:
  - path: refs/methodology.md
    load: on_demand        # 渐进式披露：只进 description，模型按需读全文
    description: NPS/CSAT 的口径定义与评级标准
  - path: refs/glossary.md
    load: always           # 全文进初始 prompt

data_contract:             # 闸门四：计算前拦截异常输入
  required_columns: [scope, nps, csat, task_success]
  min_rows: 30
  max_null_ratio: 0.2

limits:
  timeout_s: 180
  memory_mb: 1024
  network_allowlist: []    # 默认无出网。要出网必须在这里显式写域名。
```

字段写错会在注册时报**带行号**的错，不会等到运行时崩：

```
skills/my-skill/skill.yaml:
  第 11 行: steps.1.type — Input should be 'python', 'interaction' or 'llm'
  第 15 行: outputs.0.path — 产物路径必须在 output/ 下，收到 'workspace/report.docx'
```

---

## 沙箱契约

脚本与平台之间只通过这些交互（PRD R4）：

| 位置 | 用途 |
|---|---|
| `/workspace` | 输入文件与中间产物，跨 step 保留 |
| `/meta/params.json` | 本步骤的输入参数与前序卡片答案 |
| `/output` | 产物目录，执行结束平台自动收集上传 |
| stdout | JSONL 进度协议 |
| stderr | 日志，实时流到前端 |
| exit code | 0 成功，非 0 失败 |

不用记这些路径 —— `import bench` 就有：

```python
import bench
from bench import asserts

params = bench.params()                    # /meta/params.json
df = bench.io.read_table(bench.workspace() / params["raw_data"])

bench.progress(40, "正在聚合")              # stdout JSONL
bench.log("调试信息")                       # stderr

nps = bench.stats.nps(df["nps"], ndigits=1)   # 受信任的统计库
asserts.in_range(nps.value, -100, 100, name="NPS")

bench.emit_result({                        # /output/result.json
    "metrics": {
        "nps": bench.Metric(nps.value, label="净推荐值",
                            source=bench.source(path, rows=nps.rows,
                                                step="compute")),
    },
    "meta": {"scope": params["scope"]},
})
```

### 为什么优先用 `bench.stats` / `bench.io`

读一张表看着简单，实际有一堆能悄悄算错结果的坑：编码、千分位、
Excel 把长数字读成科学计数法、空字符串和 NaN 的区别。每次现写一遍
`read_csv` 就是让这些坑每次重新掷一次骰子。

`bench.stats` 里的口径（NPS 怎么分档、CSAT 阈值）写死在文档字符串里，
配合 fixtures 回归，才是长期保证。

### 数据异常就中断，不要照算

```python
if len(subset) < min_sample:
    bench.fail(
        f"范围 {scope!r} 的有效样本量为 {len(subset)}，低于设定下限 {min_sample}",
        detail={"hint": "可以调低确认卡片中的样本量下限，或更换评测范围"},
    )
```

宁可报错，不可产出一份看起来正常但数字错了的报告。

---

## 交互卡片

v1 收敛为 6 种，**不允许自由定义结构**：
`confirm` / `select` / `multi_select` / `form` / `file_pick` / `table_review`。

```json
{
  "type": "form",
  "title": "确认评测范围",
  "body": "以下参数将决定报告的统计口径。",
  "fields": [
    { "key": "scope", "label": "评测范围", "type": "select",
      "options": ["注册流程", "转账流程"], "default": "注册流程" },
    { "key": "sample", "label": "有效样本量下限", "type": "number",
      "default": 200, "min": 30 }
  ],
  "actions": [
    { "id": "confirm", "label": "确认并继续", "primary": true },
    { "id": "cancel", "label": "取消执行", "cancels": true }
  ],
  "timeout_hours": 24
}
```

答案通过 `params.json` 注入后续步骤：`params["scope"]`、`params["sample"]`。

写第 7 种类型会在注册时报错。收敛的代价是作者少了灵活性，
换来的是前端组件表封闭、答案能被平台严格校验、移动端也能作答。

---

## 报告模板

模板是**真正的 .docx**，Jinja 变量写在文档里，设计师可以直接改版式：

```
NPS：{{ nps }}
满意度：{{ csat_pct }}%
{% for row in by_scope %}{{ row.name }}：{{ row.nps }}
{% endfor %}
{{ chart }}
```

渲染上下文只来自校验过的 `result.json`。模板里**不要**做二次计算 ——
展示格式化（比率 → 百分比）可以，重新算一个数不行。

---

## fixtures（必须有）

见 [accuracy.md](accuracy.md#回归测试集r05)。至少一组正向 case，
建议再加一组负向 case 验证异常输入被拦下。

```bash
python -m bench.accuracy.fixtures run --skill my-skill --repeat 5 --update
```

首次用 `--update` 生成 `expected/`，之后去掉它。

---

## 上线前自查

- [ ] `skill.yaml` 能注册（`bench skills` 不报错）
- [ ] 每个 python step 都有 `output_schema`
- [ ] `result.json` 里每个指标都带 `source`
- [ ] 有 `data_contract`，且负向 fixture 能被拦下
- [ ] `fixtures run --repeat 5` 全绿
- [ ] `requirements.txt` 版本全部钉死
- [ ] 需要出网的话，域名写进了 `limits.network_allowlist`
