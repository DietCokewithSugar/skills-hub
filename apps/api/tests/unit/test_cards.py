"""R5 验收：交互卡片。

    [ ] 卡片提交后立即变为不可编辑的「已作答」摘要形态，保留在历史中
    [ ] 非法输入（低于 min、必填为空）在前端拦截并给出字段级错误
    [ ] Given 卡片超过 timeout_hours 未作答，Then Run 置为 expired

以及 PRD 的硬约束：v1 收敛为 6 种卡片，**不允许 skill 自由定义结构**。
"""

from __future__ import annotations

import pytest

from bench.orchestrator.cards import (
    CARD_TYPES,
    CardAnswerError,
    CardSpecError,
    parse_card_spec,
    summarize,
    validate_answer,
)

FORM = {
    "type": "form", "title": "确认评测范围",
    "body": "以下参数将决定报告的统计口径。",
    "fields": [
        {"key": "scope", "label": "评测范围", "type": "select",
         "options": ["注册流程", "转账流程", "理财购买"], "default": "注册流程"},
        {"key": "sample", "label": "有效样本量", "type": "number",
         "default": 200, "min": 30},
        {"key": "note", "label": "备注", "type": "text", "required": False},
    ],
    "actions": [{"id": "confirm", "label": "确认并继续", "primary": True},
                {"id": "cancel", "label": "取消执行", "cancels": True}],
    "timeout_hours": 24,
}


# ── 类型收敛 ───────────────────────────────────────────────────────

def test_all_six_types_parse():
    assert set(CARD_TYPES) == {"confirm", "select", "multi_select",
                               "form", "file_pick", "table_review"}
    specs = [
        {"type": "confirm", "title": "继续吗"},
        {"type": "select", "title": "选一个", "options": ["A", "B"]},
        {"type": "multi_select", "title": "多选", "options": ["A", "B"]},
        FORM,
        {"type": "file_pick", "title": "选文件", "candidates": ["a.csv"]},
        {"type": "table_review", "title": "审阅", "columns": ["id", "值"],
         "rows": [{"_id": 1, "值": 3}]},
    ]
    assert [parse_card_spec(s).type for s in specs] == list(CARD_TYPES)


def test_seventh_card_type_is_refused():
    """skill 不允许自由定义卡片结构 —— 这是 v1 的硬约束。"""
    with pytest.raises(CardSpecError) as e:
        parse_card_spec({"type": "wizard", "title": "自创类型"})
    assert "wizard" in str(e.value) and "6 种" in str(e.value)


def test_select_default_must_be_one_of_options():
    with pytest.raises(CardSpecError):
        parse_card_spec({"type": "select", "title": "x",
                         "options": ["A"], "default": "Z"})


def test_form_with_duplicate_keys_is_refused():
    with pytest.raises(CardSpecError) as e:
        parse_card_spec({"type": "form", "title": "x", "fields": [
            {"key": "a", "label": "A"}, {"key": "a", "label": "又一个 A"}]})
    assert "重复" in str(e.value)


def test_select_field_without_options_is_refused():
    with pytest.raises(CardSpecError):
        parse_card_spec({"type": "form", "title": "x", "fields": [
            {"key": "a", "label": "A", "type": "select"}]})


# ── 答案校验：字段级错误 ────────────────────────────────────────────

def test_valid_answer_is_normalized():
    spec = parse_card_spec(FORM)
    got = validate_answer(spec, {"action": "confirm",
                                 "values": {"scope": "转账流程", "sample": 250}})
    assert got == {"action": "confirm", "cancelled": False,
                   "values": {"scope": "转账流程", "sample": 250}}


def test_below_min_gives_field_level_error():
    """验收：低于 min 要给字段级错误。"""
    spec = parse_card_spec(FORM)
    with pytest.raises(CardAnswerError) as e:
        validate_answer(spec, {"action": "confirm",
                               "values": {"scope": "注册流程", "sample": 10}})
    assert e.value.field_errors == {"sample": "不能小于 30"}


def test_required_empty_gives_field_level_error():
    """验收：必填为空要给字段级错误。"""
    spec = parse_card_spec({"type": "form", "title": "x", "fields": [
        {"key": "scope", "label": "范围", "type": "text", "required": True}]})
    with pytest.raises(CardAnswerError) as e:
        validate_answer(spec, {"action": "confirm", "values": {"scope": "  "}})
    assert e.value.field_errors == {"scope": "必填"}


def test_optional_field_may_be_omitted():
    spec = parse_card_spec(FORM)
    got = validate_answer(spec, {"action": "confirm", "values": {"sample": 50}})
    assert "note" not in got["values"]
    assert got["values"]["scope"] == "注册流程", "有 default 的字段回落到 default"


def test_non_numeric_in_number_field_is_caught():
    spec = parse_card_spec(FORM)
    with pytest.raises(CardAnswerError) as e:
        validate_answer(spec, {"action": "confirm", "values": {"sample": "两百"}})
    assert e.value.field_errors == {"sample": "必须是数字"}


def test_value_outside_options_is_caught():
    spec = parse_card_spec(FORM)
    with pytest.raises(CardAnswerError) as e:
        validate_answer(spec, {"action": "confirm", "values": {"scope": "海外汇款"}})
    assert "scope" in e.value.field_errors


def test_all_field_errors_reported_together():
    """一次说清所有字段的问题，别让用户改一个提交一次。"""
    spec = parse_card_spec(FORM)
    with pytest.raises(CardAnswerError) as e:
        validate_answer(spec, {"action": "confirm",
                               "values": {"scope": "海外汇款", "sample": 1}})
    assert set(e.value.field_errors) == {"scope", "sample"}


def test_unknown_action_is_refused():
    spec = parse_card_spec(FORM)
    with pytest.raises(CardAnswerError):
        validate_answer(spec, {"action": "delete_everything", "values": {}})


def test_cancel_action_skips_field_validation():
    """用户要放弃执行，不该被必填项拦住。"""
    spec = parse_card_spec(FORM)
    got = validate_answer(spec, {"action": "cancel", "values": {}})
    assert got["cancelled"] is True


# ── 各类型的答案 ───────────────────────────────────────────────────

def test_multi_select_bounds():
    spec = parse_card_spec({"type": "multi_select", "title": "x",
                            "options": ["A", "B", "C"],
                            "min_selected": 2, "max_selected": 2})
    got = validate_answer(spec, {"action": "confirm",
                                 "values": {"choices": ["A", "B"]}})
    assert got["values"]["choices"] == ["A", "B"]
    with pytest.raises(CardAnswerError) as e:
        validate_answer(spec, {"action": "confirm", "values": {"choices": ["A"]}})
    assert "至少选择 2 项" in e.value.field_errors["choices"]


def test_file_pick_rejects_files_not_in_workspace():
    """只能从工作区已有文件里选 —— 不能凭空指一个路径。"""
    spec = parse_card_spec({"type": "file_pick", "title": "x",
                            "candidates": ["raw.csv", "extra.xlsx"]})
    with pytest.raises(CardAnswerError) as e:
        validate_answer(spec, {"action": "confirm", "values": {"file": "/etc/passwd"}})
    assert "不存在" in e.value.field_errors["file"]


def test_table_review_defaults_to_accept_all():
    spec = parse_card_spec({"type": "table_review", "title": "审阅",
                            "columns": ["_id", "值"],
                            "rows": [{"_id": 1, "值": 3}, {"_id": 2, "值": 9}]})
    got = validate_answer(spec, {"action": "confirm", "values": {}})
    assert got["values"]["rows"] == [1, 2]


def test_table_review_rejects_unknown_row_ids():
    spec = parse_card_spec({"type": "table_review", "title": "审阅",
                            "columns": ["_id"], "rows": [{"_id": 1}]})
    with pytest.raises(CardAnswerError) as e:
        validate_answer(spec, {"action": "confirm", "values": {"rows": [1, 99]}})
    assert "99" in e.value.field_errors["rows"]


# ── 摘要（7.5 状态转换）───────────────────────────────────────────

def test_summary_is_specific_not_generic():
    """「已确认：注册流程 · 200 份」而不是「已提交」。"""
    spec = parse_card_spec(FORM)
    ans = validate_answer(spec, {"action": "confirm",
                                 "values": {"scope": "注册流程", "sample": 200}})
    s = summarize(spec, ans)
    assert "注册流程" in s and "200" in s


def test_cancel_summary():
    spec = parse_card_spec(FORM)
    assert summarize(spec, validate_answer(spec, {"action": "cancel"})) == "已取消执行"
