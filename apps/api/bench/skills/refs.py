"""渐进式披露（PRD 6.5 / 用户故事 8）。

「1M 上下文不是放任的理由」—— 长 reference 默认不进初始 prompt，模型用
read_reference 工具按需读。这里提供两个东西：初始 prompt 里那份「有哪些
资料可读」的目录，以及 read_reference 的实现。
"""

from __future__ import annotations

from bench.skills.registry import LoadedSkill

#: 单次 read_reference 返回的上限，防止一份超长文档把上下文顶爆
MAX_REFERENCE_CHARS = 60_000


def initial_reference_block(skill: LoadedSkill) -> str:
    """拼进 system prompt 的 reference 段。

    load: always 的进全文；on_demand 的只进一行描述 + 路径，
    模型想看再调 read_reference。
    """
    parts: list[str] = []

    always = skill.manifest.always_references()
    if always:
        parts.append("## 参考资料（全文）")
        for r in always:
            try:
                text = skill.file(r.path).read_text(encoding="utf-8")
            except (OSError, ValueError):
                continue
            parts.append(f"### {r.path}\n{text}")

    on_demand = skill.manifest.on_demand_references()
    if on_demand:
        lines = ["## 可按需读取的参考资料",
                 "以下资料未载入上下文。需要时用 read_reference 工具读取全文。"]
        for r in on_demand:
            desc = r.description or "（无描述）"
            lines.append(f"- `{r.path}` — {desc}")
        parts.append("\n".join(lines))

    return "\n\n".join(parts)


def read_reference(skill: LoadedSkill, path: str) -> str:
    """read_reference 工具的实现。只允许读 manifest 里声明过的 reference。"""
    declared = {r.path for r in skill.manifest.references}
    if path not in declared:
        raise ValueError(
            f"{path!r} 不在该 skill 声明的 references 中。可读的有：{sorted(declared)}"
        )
    text = skill.file(path).read_text(encoding="utf-8")
    if len(text) > MAX_REFERENCE_CHARS:
        return text[:MAX_REFERENCE_CHARS] + (
            f"\n\n…（已截断，全文 {len(text)} 字符）"
        )
    return text
