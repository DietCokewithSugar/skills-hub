"""闸门一：step 输出的 schema 校验（R0.2）。

「校验失败 = 该步骤失败，绝不允许把不合法的数据往下传。宁可报错，
不可产出一份看起来正常但数字错了的报告。」

错误信息必须指到具体字段 —— R0 验收标准第 3 条：「人为让 step 输出一个
不符合 schema 的字段，执行失败且错误信息指向具体字段」。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema import ValidationError as JSONSchemaValidationError


class SchemaError(Exception):
    """带字段定位的校验失败。"""

    def __init__(self, step_id: str, problems: list[str],
                 *, schema_path: str | None = None) -> None:
        self.step_id = step_id
        self.problems = problems
        self.schema_path = schema_path
        head = f"步骤 {step_id!r} 的输出未通过 schema 校验"
        super().__init__(head + "：\n" + "\n".join(f"  · {p}" for p in problems))

    def to_payload(self) -> dict[str, Any]:
        return {"kind": "schema", "step_id": self.step_id,
                "problems": self.problems, "schema": self.schema_path}


def _format_error(err: JSONSchemaValidationError) -> str:
    """把 jsonschema 的报错整理成「字段 — 原因」。"""
    where = ".".join(str(p) for p in err.absolute_path) or "(根)"
    msg = err.message
    if err.validator == "required":
        # required 的错落在父节点上，把缺失字段名拼进路径更有用
        missing = msg.split("'")[1] if "'" in msg else "?"
        where = f"{where}.{missing}" if where != "(根)" else missing
        msg = "缺少必填字段"
    return f"{where} — {msg}"


def load_schema(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SchemaError("(schema)", [f"schema 文件不是合法 JSON：{exc}"],
                          schema_path=str(path)) from exc


def validate_step_output(step_id: str, output: Any, schema: dict[str, Any],
                         *, schema_path: str | None = None) -> None:
    """校验一个 step 的输出。不通过就抛 —— 调用方必须让这一步失败。"""
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(output), key=lambda e: list(e.absolute_path))
    if errors:
        raise SchemaError(step_id, [_format_error(e) for e in errors],
                          schema_path=schema_path)


def validate_llm_json(payload: Any, schema: dict[str, Any]) -> list[str]:
    """校验 LLM 的结构化输出。

    R0.2：「LLM 生成的结构化内容同样走 schema 校验，失败则重试，
    重试两次仍失败则转人工确认卡片。」这里只报问题，重试策略在编排器。
    """
    validator = Draft202012Validator(schema)
    return [_format_error(e) for e in
            sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path))]
