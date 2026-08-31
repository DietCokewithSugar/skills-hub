"""R0 准确性契约的验收标准，逐条对应 PRD。

    [ ] 给定一份 fixture 输入，连续执行 5 次，所有数值型输出完全一致
    [ ] 人为构造一份缺少必填列的输入，平台在计算前中断并明确指出缺哪一列
    [ ] 人为让 step 输出一个不符合 schema 的字段，执行失败且错误信息指向具体字段
    [ ] 报告中随机抽取 10 个数字，都能在 result.json 中找到对应字段与来源标注
    [ ] 模型在没有拿到某个参数时，弹出卡片询问而不是使用默认值（见 test_cards.py）
"""

from __future__ import annotations

import json

import pytest

from bench.accuracy.number_guard import check_narrative, extract_numbers
from bench.accuracy.preflight import PreflightError, run_preflight
from bench.accuracy.provenance import (
    ProvenanceError,
    allowed_numbers,
    check_provenance,
    trace,
)
from bench.accuracy.validation import SchemaError, validate_step_output
from bench.skills.manifest import DataContract

# ══════════════════════════════════════════════════════════════════
# 闸门一：schema 校验
# ══════════════════════════════════════════════════════════════════

SCHEMA = {
    "type": "object",
    "required": ["metrics"],
    "properties": {
        "metrics": {
            "type": "object",
            "required": ["nps"],
            "properties": {
                "nps": {
                    "type": "object",
                    "required": ["value", "source"],
                    "properties": {"value": {"type": "number"}},
                }
            },
        }
    },
}


def test_valid_output_passes():
    validate_step_output("compute", {
        "metrics": {"nps": {"value": 41.5, "source": {"file": "a.csv", "step": "compute"}}}
    }, SCHEMA)


def test_wrong_type_error_points_at_the_field():
    """验收：错误信息指向具体字段。"""
    with pytest.raises(SchemaError) as e:
        validate_step_output("compute", {
            "metrics": {"nps": {"value": "四十一点五",
                                "source": {"file": "a.csv", "step": "compute"}}}
        }, SCHEMA)
    msg = str(e.value)
    assert "metrics.nps.value" in msg, f"必须指到字段，实际：{msg}"
    assert "compute" in msg


def test_missing_field_error_names_the_missing_key():
    with pytest.raises(SchemaError) as e:
        validate_step_output("compute", {"metrics": {}}, SCHEMA)
    assert "nps" in str(e.value)


def test_schema_error_payload_is_structured_for_the_ui():
    """7.7：失败要说清哪一步、什么原因 —— 所以错误得是结构化的。"""
    with pytest.raises(SchemaError) as e:
        validate_step_output("compute", {}, SCHEMA)
    p = e.value.to_payload()
    assert p["kind"] == "schema" and p["step_id"] == "compute" and p["problems"]


# ══════════════════════════════════════════════════════════════════
# 闸门二：来源覆盖（R0.3）
# ══════════════════════════════════════════════════════════════════

GOOD_RESULT = {
    "metrics": {
        "nps": {"value": 41.5,
                "source": {"file": "raw.csv", "rows": [2, 201], "step": "compute",
                           "file_sha256": "abc123"}},
        "csat": {"value": 0.82,
                 "source": {"file": "raw.csv", "rows": [2, 201], "step": "compute"}},
        "by_scope": {
            "注册流程": {"value": 0.91,
                         "source": {"file": "raw.csv", "step": "compute"}},
            "转账流程": {"value": 0.76,
                         "source": {"file": "raw.csv", "step": "compute"}},
        },
    }
}


def test_full_provenance_passes():
    assert check_provenance(GOOD_RESULT) == 1.0


def test_metric_without_source_is_rejected():
    bad = {"metrics": {"nps": {"value": 41.5}}}
    with pytest.raises(ProvenanceError) as e:
        check_provenance(bad)
    assert "nps" in str(e.value) and "source" in str(e.value)


def test_nested_metric_without_source_is_caught():
    """嵌套深处漏一个也要抓出来 —— 100% 就是 100%。"""
    bad = json.loads(json.dumps(GOOD_RESULT))
    del bad["metrics"]["by_scope"]["转账流程"]["source"]
    with pytest.raises(ProvenanceError) as e:
        check_provenance(bad)
    assert "by_scope.转账流程" in str(e.value)


def test_source_missing_required_keys_is_rejected():
    bad = json.loads(json.dumps(GOOD_RESULT))
    bad["metrics"]["nps"]["source"] = {"note": "拍脑袋"}
    with pytest.raises(ProvenanceError) as e:
        check_provenance(bad)
    assert "file" in str(e.value) and "step" in str(e.value)


def test_result_without_metrics_is_rejected():
    with pytest.raises(ProvenanceError):
        check_provenance({"summary": "还行"})


def test_every_number_can_be_traced_to_a_field_and_source():
    """验收：报告中随机抽取 10 个数字，都能在 result.json 中找到字段与来源。"""
    from bench.accuracy.provenance import iter_metrics
    paths = [p for p, _ in iter_metrics(GOOD_RESULT)]
    assert set(paths) == {"nps", "csat", "by_scope.注册流程", "by_scope.转账流程"}
    for p in paths:
        src = trace(GOOD_RESULT, p)
        assert src and src["file"] and src["step"], f"{p} 追溯不到来源"


# ══════════════════════════════════════════════════════════════════
# 闸门三：数字越界拦截（R0.1）—— 「模型越界改写数字的发生次数 = 0」
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def allowed():
    return allowed_numbers(GOOD_RESULT)


def test_narrative_quoting_computed_numbers_passes(allowed):
    r = check_narrative("本次评测 NPS 为 41.5，整体满意度 82%，"
                        "其中注册流程 91%，转账流程 76%。", allowed)
    assert r.ok, f"合法引用被误判：{r.violations}"


def test_model_inventing_a_number_is_caught(allowed):
    """模型把 41.5 写成 45 —— 这正是「静默错误」，必须拦下。"""
    r = check_narrative("本次评测 NPS 为 45，表现良好。", allowed)
    assert not r.ok
    assert "45" in r.violations


def test_model_estimating_a_missing_number_is_caught(allowed):
    """R0.1 禁止「估算」。凭空出现的 63% 没有来源。"""
    r = check_narrative("综合来看，用户留存约为 63%。", allowed)
    assert not r.ok and "63%" in r.violations


def test_percent_and_decimal_forms_are_equivalent(allowed):
    """0.82 与 82% 是同一个数，不该误报。"""
    assert check_narrative("满意度 0.82。", allowed).ok
    assert check_narrative("满意度 82%。", allowed).ok


def test_rounding_in_narrative_is_allowed(allowed):
    """叙述里写 42（41.5 四舍五入）是合理的行文，不是篡改。"""
    assert check_narrative("NPS 接近 42。", allowed).ok


def test_years_and_small_ordinals_are_not_flagged(allowed):
    r = check_narrative("2026 年第 3 季度共 2 个流程纳入评测。", allowed)
    assert r.ok, f"年份与序号被误判：{r.violations}"


def test_thousand_separators_are_parsed(allowed):
    r = check_narrative("样本量 1,234 份。", allowed)
    assert not r.ok and "1,234" in r.violations


def test_extract_numbers_finds_all_forms():
    got = extract_numbers("NPS 41.5，满意度 82%，样本 1,234，降幅 -3.2")
    assert "41.5" in got and "82%" in got and "1,234" in got and "-3.2" in got


def test_violations_are_deduped_in_order(allowed):
    r = check_narrative("错的 99，又是 99，还有 77。", allowed)
    assert r.violations == ["99", "77"]


# ══════════════════════════════════════════════════════════════════
# 闸门四：数据前置检查（R0.4）
# ══════════════════════════════════════════════════════════════════

def _csv(tmp_path, text: str, name="raw.csv"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


CONTRACT = DataContract(required_columns=["nps", "csat", "scope"],
                        min_rows=30, max_null_ratio=0.2)


def test_missing_column_is_caught_before_compute_and_named(tmp_path):
    """验收：人为构造一份缺少必填列的输入，平台在计算前中断并明确指出缺哪一列。"""
    rows = "\n".join("8,4,注册流程" for _ in range(50))
    p = _csv(tmp_path, "nps,csat,region\n" + rows.replace(",注册流程", ",华东"))
    with pytest.raises(PreflightError) as e:
        run_preflight(p, CONTRACT)
    msg = str(e.value)
    assert "scope" in msg, f"必须点名缺失的列，实际：{msg}"
    assert "region" in msg, "还要告诉作者文件里实际有哪些列"


def test_sample_below_declared_minimum_is_rejected(tmp_path):
    rows = "\n".join("8,4,注册流程" for _ in range(5))
    p = _csv(tmp_path, "nps,csat,scope\n" + rows)
    with pytest.raises(PreflightError) as e:
        run_preflight(p, CONTRACT)
    assert "5" in str(e.value) and "30" in str(e.value)


def test_entirely_empty_required_column_is_rejected(tmp_path):
    rows = "\n".join("8,,注册流程" for _ in range(50))
    p = _csv(tmp_path, "nps,csat,scope\n" + rows)
    with pytest.raises(PreflightError) as e:
        run_preflight(p, CONTRACT)
    assert "csat" in str(e.value) and "整列为空" in str(e.value)


def test_null_ratio_above_threshold_is_rejected(tmp_path):
    rows = ["8,4,注册流程"] * 30 + ["8,,注册流程"] * 20   # 40% 空
    p = _csv(tmp_path, "nps,csat,scope\n" + "\n".join(rows))
    with pytest.raises(PreflightError) as e:
        run_preflight(p, CONTRACT)
    assert "空值率" in str(e.value)


def test_clean_input_passes_and_reports_shape(tmp_path):
    rows = "\n".join("8,4,注册流程" for _ in range(50))
    p = _csv(tmp_path, "nps,csat,scope\n" + rows)
    rep = run_preflight(p, CONTRACT)
    assert rep.ok and rep.rows == 50
    assert rep.columns == ["nps", "csat", "scope"]


def test_unparseable_file_is_rejected_not_crashed(tmp_path):
    p = tmp_path / "raw.pdf"
    p.write_bytes(b"%PDF-1.4 not a table")
    with pytest.raises(PreflightError) as e:
        run_preflight(p, CONTRACT)
    assert "不支持的文件格式" in str(e.value)


def test_all_problems_are_reported_at_once(tmp_path):
    """一次说清所有问题，别让作者改一个跑一次。"""
    p = _csv(tmp_path, "nps\n8\n8\n")
    with pytest.raises(PreflightError) as e:
        run_preflight(p, CONTRACT)
    assert len(e.value.problems) >= 2


# ══════════════════════════════════════════════════════════════════
# 回归：允许集合不得被来源标注污染
# ══════════════════════════════════════════════════════════════════

def test_allowed_set_excludes_provenance_row_indices():
    """来源里的行号不是「可以引用的数字」。

    这是一个真实踩过的洞：allowed_numbers 早期版本遍历整份 result.json，
    把 source.rows 里的 0..N 全部并进允许集合。一份 240 行的数据于是让
    0–241 之间任何整数都被判为合法引用 —— 数字越界拦截形同虚设，
    而且**没有任何测试会失败**，正是 PRD 说的静默错误。
    """
    result = {
        "metrics": {
            "nps": {"value": 1.25,
                    "source": {"file": "raw.csv", "step": "compute",
                               "rows": list(range(0, 242)),
                               "file_sha256": "9f8e7d6c5b4a"}},
        },
        "meta": {"total_rows": 540, "min_sample": 200,
                 "columns": ["nps", "csat"]},
    }
    allowed = allowed_numbers(result)

    assert allowed == {1.25, 540.0, 200.0}, f"允许集合被污染了：{sorted(allowed)}"
    for n in (15, 42, 99, 137, 241):
        assert float(n) not in allowed, f"行号 {n} 不该进入允许集合"

    r = check_narrative("NPS 为 1.25，预计下季度将提升至 15。", allowed)
    assert not r.ok and "15" in r.violations


def test_meta_scalars_are_quotable_but_lists_are_not():
    """meta 里的标量（总行数、样本下限）可以引用；列名这类列表不参与。"""
    result = {
        "metrics": {"x": {"value": 7.0,
                          "source": {"file": "a.csv", "step": "s"}}},
        "meta": {"total_rows": 540, "columns": ["a", "b"], "note": "无"},
    }
    allowed = allowed_numbers(result)
    assert 540.0 in allowed and 7.0 in allowed
    assert check_narrative("共 540 份，指标 7。", allowed).ok


def test_file_hash_digits_are_not_quotable():
    """哈希里的数字串不能被当成可引用的数值。"""
    result = {
        "metrics": {"x": {"value": 3.0,
                          "source": {"file": "a.csv", "step": "s",
                                     "file_sha256": "846efa99500c9bb0"}}},
    }
    allowed = allowed_numbers(result)
    assert allowed == {3.0}
