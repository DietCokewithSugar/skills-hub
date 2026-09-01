# 准确性契约（R0）

> 报告里的每一个数字，都必须是 Python 算出来的，不是模型说出来的。

成功指标里只有一条红线：**静默错误率 = 0**（产出了报告但数字是错的）。
下面四道闸门都是为这条线服务的。它们是**机器闸门**，不是 prompt 里的叮嘱 ——
prompt 说服不了一个正在幻觉的模型。

---

## 职责分离（R0.1）

| 谁 | 负责什么 | 绝对不许做 |
|---|---|---|
| Python 脚本 | 所有计算、统计、聚合、筛选 | —— |
| LLM | 理解意图、编排步骤、生成叙述性文字 | 计算、改写数字、估算、补全缺失数据 |
| 报告模板 | 把校验过的结构化数据填进版式 | 二次加工数据 |

代码里的落点：`orchestrator/steps.py` 的三个执行器互不越界。
`reports/docx.py::build_context` 的渲染上下文**只**来自校验通过的
`result.json`。

---

## 闸门一：step 输出 schema 校验（R0.2）

`accuracy/validation.py`

skill 的每个 python step 在 `skill.yaml` 里声明 `output_schema`。
输出不合法 = 该步骤失败，**绝不允许把不合法的数据往下传**。

错误信息指到具体字段：

```
步骤 'compute' 的输出未通过 schema 校验：
  · metrics.nps.value — 'four' is not of type 'number'
```

LLM 的结构化输出走同一道校验，失败重试两次，仍失败则转人工确认卡片
（`orchestrator/llm_step.py`）。

---

## 闸门二：来源覆盖校验（R0.3）

`accuracy/provenance.py`

`result.json` 中 `metrics` 下**每一个**数值叶子必须带 `source`：

```json
{"metrics": {"nps": {"value": 41.5, "source": {
  "file": "raw.csv", "rows": [2, 201], "step": "compute",
  "file_sha256": "…"}}}}
```

少一个就让这一步失败。这是「报告数字可追溯率 100%」唯一守得住的方式 ——
靠 skill 作者自觉是守不住的。

嵌套任意层都会被遍历到，漏一个深处的也抓得出来。

---

## 闸门三：数字越界拦截（R0.1）

`accuracy/number_guard.py`

把 LLM 生成的叙述里的数字全部抠出来，逐个比对 `result.json` 的允许集合。
出现集合外的数字 → 判越界 → 重试；连续三次不过则转人工。

允许的等价形式（**只有这些**）：

- 百分比与比率互换：`82%` ↔ `0.82`
- 常规四舍五入：`0.7042` ↔ `70.4%` ↔ `70%`（R0.1 禁止的是「改写」，
  四舍五入明确排除在外）
- 年份与 0–10 的小整数（行文里的序数、列表编号）

> **这里踩过一个坑，值得记下来。**
> `allowed_numbers()` 最初遍历整份 `result.json`，把 `source.rows` 里的
> 行号也并进了允许集合。一份 240 行的数据于是让 0–241 之间任何整数都
> 被判为「合法引用」—— 拦截形同虚设，**而且没有任何单元测试会失败**。
> 它是在审计一份成品报告时才暴露的。
>
> 现在只收「指标 value + meta 标量」，允许集合从 247 个降到 8 个。
> 回归测试：`test_allowed_set_excludes_provenance_row_indices`。

成品还有一道复核：`reports/docx.py::audit_report_numbers` 抽出成文全部文字，
再比对一次 —— 模板里如果有人手写了硬编码数字，只有这一步能发现。

---

## 闸门四：数据前置检查（R0.4）

`accuracy/preflight.py`

在**第一个 python step 之前**跑，按 `skill.yaml` 的 `data_contract`：

```yaml
data_contract:
  required_columns: [scope, nps, csat, task_success]
  min_rows: 30
  max_null_ratio: 0.2
```

拦下的情况：缺必填列、样本量低于下限、整列为空、空值率超阈值、文件无法解析。

报错必须**指名道姓**：

```
步骤 compute
raw.csv：缺少必填列 ['scope']；文件实际的列是
['respondent_id', 'region', 'nps', 'csat', 'task_success']
换一份包含所有必填列的数据，或在确认卡片里调整口径后重新开始。
```

「不确定就问，不要猜」的另一半在 `orchestrator/tools.py::ask_user`：
参数缺失时模型**必须**弹卡片，不允许自行取默认值。

---

## 回归测试集（R0.5）

> 这是唯一能长期保证「结果准确」的机制，其他都是一次性的。

```
skills/<id>/fixtures/<case>/
    input/                  输入文件
    params.json             参数与卡片答案
    expected/result.json    期望输出
    case.json               可选：声明这个 case 期望被拦截
```

```bash
python -m bench.accuracy.fixtures run --skill ux-report --repeat 5
```

断言三件事：

1. **连续 5 次执行，所有数值型输出完全一致**（不设容差）；
2. 与 `expected/` 一致；
3. 来源可追溯率 100%。

**负向 case** 同样重要 ——「异常输入拦截率 ≥ 95%」这个指标，
跑通的 case 是量不出来的：

```json
{"expect": "reject", "reason_contains": "scope"}
```

期望被拦截的 case 如果跑通了，算失败：静默错误率必须为 0，这正是红线所在。

---

## 复现记录（R0.6）

每次 Run 都记下 `model` / `prompt_version` / `skill_version` /
`input_hashes`（每个输入文件的 sha256）。

叙述性生成用低 temperature，结构化输出用 JSON mode，两者都固化在
`llm/deepseek.py` 的默认值里，而不是散落在各个调用点靠自觉。

依赖版本在沙箱镜像里钉死，运行时禁止 `pip install` —— 依赖版本漂移会让
同样的输入算出不同的数。
