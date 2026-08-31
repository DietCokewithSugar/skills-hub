"""skill.yaml 的模型与校验。

R3 验收：「`skill.yaml` 字段错误时给出明确的行号级报错，而不是运行时崩溃」。

行号是怎么来的：PyYAML 的 compose() 保留节点的位置信息，我们先把文档
compose 成节点树留一份「路径 → 行号」的地图，再用 Pydantic 校验普通 dict。
Pydantic 报错带 loc 元组（如 ('steps', 1, 'entry')），拿它去地图里查行号。
这样既有 Pydantic 的表达力，又有 YAML 的定位能力。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


class ManifestError(Exception):
    """带行号的 manifest 错误。str() 出来就是可以直接给作者看的报错。"""

    def __init__(self, path: Path, problems: list[str]) -> None:
        self.path = path
        self.problems = problems
        super().__init__(f"{path}:\n" + "\n".join(f"  {p}" for p in problems))


# ── 位置地图 ────────────────────────────────────────────────────────

def _build_line_map(text: str) -> dict[tuple, int]:
    """把 YAML 文档的每个路径映射到 1-based 行号。"""
    loc: dict[tuple, int] = {}

    def walk(node: yaml.Node, path: tuple) -> None:
        loc[path] = node.start_mark.line + 1
        if isinstance(node, yaml.MappingNode):
            for k, v in node.value:
                walk(v, path + (k.value,))
                loc[path + (k.value,)] = k.start_mark.line + 1
        elif isinstance(node, yaml.SequenceNode):
            for i, item in enumerate(node.value):
                walk(item, path + (i,))

    try:
        root = yaml.compose(text)
    except yaml.YAMLError:
        return loc
    if root is not None:
        walk(root, ())
    return loc


def _line_for(loc: dict[tuple, int], path: tuple) -> int | None:
    """从最具体的路径向上回退，直到找到有位置的祖先。"""
    p = tuple(path)
    while p:
        if p in loc:
            return loc[p]
        p = p[:-1]
    return loc.get(())


# ── 模型 ────────────────────────────────────────────────────────────

class SkillInput(BaseModel):
    key: str
    label: str
    type: Literal["file", "text", "number", "select"] = "file"
    accept: list[str] = Field(default_factory=list)
    required: bool = True
    default: Any = None
    options: list[str] = Field(default_factory=list)


class CardRef(BaseModel):
    """interaction step 引用的卡片定义文件。"""

    path: str


class Step(BaseModel):
    id: str
    type: Literal["python", "interaction", "llm"]

    # type: python
    entry: str | None = None
    #: R0.2：该步骤输出必须通过的 JSON Schema（相对 skill 根目录）。
    #: 没有它，这一步的输出就是不受校验的，不允许进入报告。
    output_schema: str | None = None

    # type: interaction
    card: str | None = None

    # type: llm
    prompt: str | None = None
    #: LLM 结构化输出同样走 schema 校验（R0.2）
    response_schema: str | None = None

    label: str | None = None
    timeout_s: int | None = None

    @model_validator(mode="after")
    def _check_required_by_type(self) -> Step:
        if self.type == "python" and not self.entry:
            raise ValueError("python 步骤必须声明 entry")
        if self.type == "interaction" and not self.card:
            raise ValueError("interaction 步骤必须声明 card")
        if self.type == "llm" and not self.prompt:
            raise ValueError("llm 步骤必须声明 prompt")
        return self


class SkillOutput(BaseModel):
    path: str
    label: str

    @field_validator("path")
    @classmethod
    def _must_be_under_output(cls, v: str) -> str:
        if not v.startswith("output/"):
            raise ValueError(f"产物路径必须在 output/ 下，收到 {v!r}")
        if "scratch" in Path(v).parts:
            # R9：探索轨目录不能被声明为正式产物
            raise ValueError("output/ 下不得引用 scratch 路径（那是探索轨目录）")
        return v


class ReferenceSpec(BaseModel):
    """渐进式披露（R3 / 用户故事 8）。

    load: always    —— 全文进初始 system prompt
    load: on_demand —— 只进一行 description，模型用 read_reference 工具按需读
    """

    path: str
    load: Literal["always", "on_demand"] = "on_demand"
    description: str | None = None


class DataContract(BaseModel):
    """R0.4：数据前置检查的声明。

    「数据异常时中断执行并报告，而不是照算不误」需要 skill 事先说清楚
    什么算异常 —— 否则平台无从判断。
    """

    required_columns: list[str] = Field(default_factory=list)
    min_rows: int = 0
    #: 单列空值率上限，超过即中断（0.0–1.0）
    max_null_ratio: float = 1.0

    @field_validator("max_null_ratio")
    @classmethod
    def _ratio(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("max_null_ratio 必须在 0.0–1.0 之间")
        return v


class Limits(BaseModel):
    """skill 可在平台硬上限内下调资源，不可上调（6.4：有硬上限）。"""

    timeout_s: int | None = None
    memory_mb: int | None = None
    disk_mb: int | None = None
    #: 需要出网的 skill 在这里声明域名白名单（默认完全无出网）
    network_allowlist: list[str] = Field(default_factory=list)


class Manifest(BaseModel):
    id: str
    name: str
    version: str
    description: str = ""
    runtime: str = "python-3.12"
    requirements: str | None = None

    inputs: list[SkillInput] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    outputs: list[SkillOutput] = Field(default_factory=list)
    references: list[ReferenceSpec] = Field(default_factory=list)
    data_contract: DataContract | None = None
    limits: Limits = Field(default_factory=Limits)

    @field_validator("id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        if not v or not all(c.isalnum() or c in "-_" for c in v):
            raise ValueError("id 只能包含字母、数字、连字符和下划线")
        return v

    @model_validator(mode="after")
    def _cross_field(self) -> Manifest:
        if not self.steps:
            raise ValueError("至少要声明一个 step")
        seen: set[str] = set()
        for s in self.steps:
            if s.id in seen:
                raise ValueError(f"步骤 id 重复：{s.id!r}")
            seen.add(s.id)
        keys: set[str] = set()
        for i in self.inputs:
            if i.key in keys:
                raise ValueError(f"输入 key 重复：{i.key!r}")
            keys.add(i.key)
        return self

    # ── 便捷访问 ────────────────────────────────────────────────
    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    def step(self, step_id: str) -> Step | None:
        return next((s for s in self.steps if s.id == step_id), None)

    def step_index(self, step_id: str) -> int:
        for i, s in enumerate(self.steps):
            if s.id == step_id:
                return i
        raise KeyError(step_id)

    def always_references(self) -> list[ReferenceSpec]:
        return [r for r in self.references if r.load == "always"]

    def on_demand_references(self) -> list[ReferenceSpec]:
        return [r for r in self.references if r.load == "on_demand"]


# ── 加载 ────────────────────────────────────────────────────────────

def load_manifest(skill_dir: Path) -> Manifest:
    """读取并校验 <skill_dir>/skill.yaml，错误一律带行号。"""
    path = skill_dir / "skill.yaml"
    if not path.exists():
        raise ManifestError(path, ["缺少 skill.yaml"])

    text = path.read_text(encoding="utf-8")
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = f"第 {mark.line + 1} 行: " if mark else ""
        raise ManifestError(path, [f"{line}YAML 语法错误 — {getattr(exc, 'problem', exc)}"]) from exc

    if not isinstance(raw, dict):
        raise ManifestError(path, ["第 1 行: skill.yaml 顶层必须是一个映射（key: value）"])

    line_map = _build_line_map(text)
    try:
        manifest = Manifest.model_validate(raw)
    except ValidationError as exc:
        problems = []
        for err in exc.errors():
            loc = err["loc"]
            line = _line_for(line_map, loc)
            where = ".".join(str(x) for x in loc) or "(根)"
            prefix = f"第 {line} 行: " if line else ""
            problems.append(f"{prefix}{where} — {err['msg']}")
        raise ManifestError(path, problems) from exc

    # ── 引用的文件必须真实存在，否则等到运行时才炸 ──
    missing: list[str] = []
    for s in manifest.steps:
        for attr, label in (("entry", "entry"), ("card", "card"),
                            ("output_schema", "output_schema"),
                            ("response_schema", "response_schema")):
            rel = getattr(s, attr, None)
            if rel and not (skill_dir / rel).exists():
                line = _line_for(line_map, ("steps", manifest.step_index(s.id), attr))
                prefix = f"第 {line} 行: " if line else ""
                missing.append(f"{prefix}步骤 {s.id!r} 的 {label} 指向不存在的文件：{rel}")
    for i, r in enumerate(manifest.references):
        if not (skill_dir / r.path).exists():
            line = _line_for(line_map, ("references", i, "path"))
            prefix = f"第 {line} 行: " if line else ""
            missing.append(f"{prefix}reference 指向不存在的文件：{r.path}")
    if missing:
        raise ManifestError(path, missing)

    return manifest
