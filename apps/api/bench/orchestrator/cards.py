"""交互卡片（R5 —— 核心差异化能力）。

「执行中的确认发生在编排层，Python 步骤保持纯函数。」

v1 收敛为 6 种类型，**不允许 skill 自由定义结构**（PRD 原文）。这条约束
是有代价的（作者少了灵活性），换来的是前端组件注册表封闭可控、
answer 能被平台严格校验、移动端也能作答。所以这里用 Pydantic 判别联合
把 6 种形状钉死：skill.yaml 里写了第 7 种，注册时就报错。

协议（PRD R5）：
    1. 遇到 interaction step，或 LLM 调用 ask_user 工具
    2. 后端写入 card_request Part，Run 置 waiting_for_input 并落库持久化
    3. SSE 推送 card.requested
    4. 前端按 card.type 从组件注册表取组件渲染
    5. 用户提交 → POST /runs/{id}/resume
    6. 后端写入 card_response Part，恢复执行，答案注入下一步 params.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, ValidationError, model_validator


class CardSpecError(Exception):
    """卡片定义本身不合法（skill 作者的问题，注册/执行时报）。"""


class CardAnswerError(Exception):
    """用户提交的答案不合法。

    field_errors 是「字段 → 错误」，对应 R5 验收：
    「非法输入（低于 min、必填为空）在前端拦截并给出字段级错误」。
    前端拦一道，后端再拦一道 —— 前端可以被绕过，后端不能。
    """

    def __init__(self, message: str,
                 field_errors: dict[str, str] | None = None) -> None:
        self.field_errors = field_errors or {}
        super().__init__(message)

    def to_payload(self) -> dict[str, Any]:
        return {"message": str(self), "field_errors": self.field_errors}


# ── 动作 ────────────────────────────────────────────────────────────

class CardAction(BaseModel):
    id: str
    label: str
    primary: bool = False
    #: 取消类动作：提交后不继续执行，而是把 Run 置为 cancelled
    cancels: bool = False


DEFAULT_ACTIONS = [
    CardAction(id="confirm", label="确认并继续", primary=True),
    CardAction(id="cancel", label="取消执行", cancels=True),
]


class _Base(BaseModel):
    title: str
    body: str = ""
    actions: list[CardAction] = Field(default_factory=lambda: list(DEFAULT_ACTIONS))
    #: R5 验收：卡片超过 timeout_hours 未作答则 Run 置为 expired
    timeout_hours: int = 24

    @model_validator(mode="after")
    def _needs_an_action(self):
        if not self.actions:
            raise ValueError("卡片至少要有一个 action")
        return self


# ── 表单字段（form 卡片用）────────────────────────────────────────

class FormField(BaseModel):
    key: str
    label: str
    type: Literal["text", "number", "select", "date", "textarea", "checkbox"] = "text"
    required: bool = True
    default: Any = None
    options: list[str] = Field(default_factory=list)
    min: float | None = None
    max: float | None = None
    placeholder: str = ""
    help: str = ""

    @model_validator(mode="after")
    def _select_needs_options(self):
        if self.type == "select" and not self.options:
            raise ValueError(f"字段 {self.key!r} 是 select，必须给 options")
        return self


# ── 6 种卡片 ────────────────────────────────────────────────────────

class ConfirmCard(_Base):
    type: Literal["confirm"] = "confirm"


class SelectCard(_Base):
    type: Literal["select"] = "select"
    key: str = "choice"
    options: list[str]
    default: str | None = None

    @model_validator(mode="after")
    def _default_in_options(self):
        if not self.options:
            raise ValueError("select 卡片必须给 options")
        if self.default is not None and self.default not in self.options:
            raise ValueError(f"default {self.default!r} 不在 options 里")
        return self


class MultiSelectCard(_Base):
    type: Literal["multi_select"] = "multi_select"
    key: str = "choices"
    options: list[str]
    default: list[str] = Field(default_factory=list)
    min_selected: int = 0
    max_selected: int | None = None

    @model_validator(mode="after")
    def _check(self):
        if not self.options:
            raise ValueError("multi_select 卡片必须给 options")
        bad = [d for d in self.default if d not in self.options]
        if bad:
            raise ValueError(f"default 里有不在 options 中的值：{bad}")
        return self


class FormCard(_Base):
    type: Literal["form"] = "form"
    fields: list[FormField]

    @model_validator(mode="after")
    def _unique_keys(self):
        if not self.fields:
            raise ValueError("form 卡片必须至少有一个字段")
        keys = [f.key for f in self.fields]
        dup = {k for k in keys if keys.count(k) > 1}
        if dup:
            raise ValueError(f"字段 key 重复：{sorted(dup)}")
        return self


class FilePickCard(_Base):
    type: Literal["file_pick"] = "file_pick"
    key: str = "file"
    #: 从工作区已有文件中选择；候选由平台在发卡时填入
    candidates: list[str] = Field(default_factory=list)
    accept: list[str] = Field(default_factory=list)
    multiple: bool = False


class TableReviewCard(_Base):
    """表格数据审阅，支持逐行接受/剔除。"""

    type: Literal["table_review"] = "table_review"
    key: str = "rows"
    columns: list[str]
    rows: list[dict[str, Any]] = Field(default_factory=list)
    #: 默认全选（接受所有行）
    default_accepted: bool = True
    row_id_key: str = "_id"

    @model_validator(mode="after")
    def _columns_required(self):
        if not self.columns:
            raise ValueError("table_review 卡片必须声明 columns")
        return self


CardSpec = Annotated[
    Union[ConfirmCard, SelectCard, MultiSelectCard, FormCard,
          FilePickCard, TableReviewCard],
    Field(discriminator="type"),
]


class _SpecWrapper(BaseModel):
    spec: CardSpec


CARD_TYPES = ("confirm", "select", "multi_select", "form", "file_pick", "table_review")


def parse_card_spec(raw: dict[str, Any]) -> CardSpec:
    """解析卡片定义。类型不在 6 种之内直接报错（不允许自由定义结构）。"""
    if not isinstance(raw, dict):
        raise CardSpecError("卡片定义必须是一个 JSON 对象")
    t = raw.get("type")
    if t not in CARD_TYPES:
        raise CardSpecError(
            f"不支持的卡片类型 {t!r}。v1 只支持这 6 种：{', '.join(CARD_TYPES)}"
        )
    try:
        return _SpecWrapper(spec=raw).spec
    except ValidationError as exc:
        problems = [
            f"{'.'.join(str(x) for x in e['loc'][1:]) or '(根)'} — {e['msg']}"
            for e in exc.errors()
        ]
        raise CardSpecError(f"卡片定义不合法：\n" + "\n".join(f"  · {p}" for p in problems)) from exc


def load_card_spec(path: Path) -> CardSpec:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CardSpecError(f"{path} 不是合法 JSON：{exc}") from exc
    return parse_card_spec(raw)


# ── 答案校验 ────────────────────────────────────────────────────────

def _missing(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def validate_answer(spec: CardSpec, answer: dict[str, Any]) -> dict[str, Any]:
    """校验并归一化用户提交的答案。

    返回的 values 会被注入下一步的 params.json。
    """
    if not isinstance(answer, dict):
        raise CardAnswerError("答案必须是一个对象")

    action_id = answer.get("action")
    valid_actions = {a.id for a in spec.actions}
    if action_id not in valid_actions:
        raise CardAnswerError(
            f"未知的 action {action_id!r}，可选：{sorted(valid_actions)}")
    action = next(a for a in spec.actions if a.id == action_id)

    # 取消动作不校验字段 —— 用户要放弃，不该被必填项拦住
    if action.cancels:
        return {"action": action_id, "cancelled": True, "values": {}}

    values = answer.get("values") or {}
    if not isinstance(values, dict):
        raise CardAnswerError("values 必须是一个对象")

    errors: dict[str, str] = {}
    out: dict[str, Any] = {}

    if isinstance(spec, ConfirmCard):
        pass

    elif isinstance(spec, SelectCard):
        v = values.get(spec.key, spec.default)
        if _missing(v):
            errors[spec.key] = "必选"
        elif v not in spec.options:
            errors[spec.key] = f"{v!r} 不在可选项中"
        else:
            out[spec.key] = v

    elif isinstance(spec, MultiSelectCard):
        v = values.get(spec.key, spec.default)
        if not isinstance(v, list):
            errors[spec.key] = "必须是一个数组"
        else:
            bad = [x for x in v if x not in spec.options]
            if bad:
                errors[spec.key] = f"包含不可选的值：{bad}"
            elif len(v) < spec.min_selected:
                errors[spec.key] = f"至少选择 {spec.min_selected} 项"
            elif spec.max_selected is not None and len(v) > spec.max_selected:
                errors[spec.key] = f"最多选择 {spec.max_selected} 项"
            else:
                out[spec.key] = v

    elif isinstance(spec, FormCard):
        for f in spec.fields:
            raw = values.get(f.key, f.default)
            if _missing(raw):
                if f.required:
                    errors[f.key] = "必填"
                continue
            if f.type == "number":
                try:
                    num = float(raw)
                except (TypeError, ValueError):
                    errors[f.key] = "必须是数字"
                    continue
                if f.min is not None and num < f.min:
                    errors[f.key] = f"不能小于 {f.min:g}"
                    continue
                if f.max is not None and num > f.max:
                    errors[f.key] = f"不能大于 {f.max:g}"
                    continue
                out[f.key] = int(num) if float(num).is_integer() else num
            elif f.type == "select":
                if raw not in f.options:
                    errors[f.key] = f"{raw!r} 不在可选项中"
                else:
                    out[f.key] = raw
            elif f.type == "checkbox":
                out[f.key] = bool(raw)
            else:
                out[f.key] = raw

    elif isinstance(spec, FilePickCard):
        v = values.get(spec.key)
        picked = v if isinstance(v, list) else ([v] if v is not None else [])
        if not picked:
            errors[spec.key] = "请选择文件"
        elif not spec.multiple and len(picked) > 1:
            errors[spec.key] = "只能选择一个文件"
        else:
            unknown = [p for p in picked if spec.candidates and p not in spec.candidates]
            if unknown:
                errors[spec.key] = f"工作区中不存在：{unknown}"
            else:
                out[spec.key] = picked if spec.multiple else picked[0]

    elif isinstance(spec, TableReviewCard):
        v = values.get(spec.key)
        if v is None:
            # 没提交就按默认：全接受或全剔除
            ids = [r.get(spec.row_id_key, i) for i, r in enumerate(spec.rows)]
            out[spec.key] = ids if spec.default_accepted else []
        elif not isinstance(v, list):
            errors[spec.key] = "必须是被接受行的 id 数组"
        else:
            known = {r.get(spec.row_id_key, i) for i, r in enumerate(spec.rows)}
            bad = [x for x in v if x not in known]
            if bad:
                errors[spec.key] = f"包含不存在的行：{bad}"
            else:
                out[spec.key] = v

    if errors:
        raise CardAnswerError("表单校验未通过", field_errors=errors)
    return {"action": action_id, "cancelled": False, "values": out}


def summarize(spec: CardSpec, answered: dict[str, Any]) -> str:
    """作答后就地坍缩成的一行摘要（7.5 状态转换）。

    「卡片作答后，就地坍缩为一行『已确认：注册流程 · 200 份』的摘要」——
    所以摘要要短、要具体，不是「已提交」这种废话。
    """
    if answered.get("cancelled"):
        return "已取消执行"
    values = answered.get("values") or {}
    if not values:
        return "已确认"
    bits: list[str] = []
    for k, v in values.items():
        if isinstance(v, list):
            bits.append(f"{len(v)} 项" if len(v) > 3 else "、".join(str(x) for x in v))
        elif isinstance(v, bool):
            bits.append("是" if v else "否")
        else:
            bits.append(str(v))
    return "已确认：" + " · ".join(bits)
