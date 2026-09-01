"""R3 验收：skill.yaml 字段错误时给出明确的行号级报错，而不是运行时崩溃。"""

from __future__ import annotations

import pytest

from bench.skills.manifest import ManifestError, load_manifest
from bench.skills.registry import SkillRegistry

GOOD = """\
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
    card: cards/scope.json
  - id: compute
    type: python
    entry: steps/compute.py
    output_schema: schemas/compute.json

outputs:
  - path: output/report.docx
    label: 体验评测报告

references:
  - path: refs/methodology.md
    load: on_demand
    description: 评测方法论
  - path: refs/glossary.md
    load: always
"""


def _skill(target_dir, yaml_text: str, *, files=("cards/scope.json", "steps/compute.py",
                                               "schemas/compute.json",
                                               "refs/methodology.md", "refs/glossary.md")):
    d = target_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / "skill.yaml").write_text(yaml_text, encoding="utf-8")
    for f in files:
        p = d / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}" if f.endswith(".json") else "x", encoding="utf-8")
    return d


def test_valid_manifest_loads(tmp_path):
    m = load_manifest(_skill(tmp_path / "sk", GOOD))
    assert m.id == "ux-report"
    assert m.ref == "ux-report@0.3.0"
    assert [s.id for s in m.steps] == ["confirm_scope", "compute"]
    assert [r.path for r in m.always_references()] == ["refs/glossary.md"]
    assert [r.path for r in m.on_demand_references()] == ["refs/methodology.md"]


def test_missing_required_field_reports_line(tmp_path):
    """删掉 version，报错要指到位置而不是抛 KeyError。"""
    bad = GOOD.replace("version: 0.3.0\n", "")
    with pytest.raises(ManifestError) as e:
        load_manifest(_skill(tmp_path / "sk", bad))
    msg = str(e.value)
    assert "version" in msg
    assert "行" in msg, f"报错必须带行号，实际：{msg}"


def test_wrong_step_type_reports_line_and_field(tmp_path):
    bad = GOOD.replace("    type: python\n", "    type: pyhton\n")   # 拼错
    with pytest.raises(ManifestError) as e:
        load_manifest(_skill(tmp_path / "sk", bad))
    msg = str(e.value)
    assert "steps.1.type" in msg
    line = bad.splitlines().index("    type: pyhton") + 1
    assert f"第 {line} 行" in msg, f"应指向第 {line} 行，实际：{msg}"


def test_python_step_without_entry_is_rejected(tmp_path):
    bad = GOOD.replace("    entry: steps/compute.py\n", "")
    with pytest.raises(ManifestError) as e:
        load_manifest(_skill(tmp_path / "sk", bad))
    assert "python 步骤必须声明 entry" in str(e.value)


def test_interaction_step_without_card_is_rejected(tmp_path):
    bad = GOOD.replace("    card: cards/scope.json\n", "")
    with pytest.raises(ManifestError) as e:
        load_manifest(_skill(tmp_path / "sk", bad))
    assert "interaction 步骤必须声明 card" in str(e.value)


def test_output_outside_output_dir_is_rejected(tmp_path):
    bad = GOOD.replace("  - path: output/report.docx", "  - path: workspace/report.docx")
    with pytest.raises(ManifestError) as e:
        load_manifest(_skill(tmp_path / "sk", bad))
    assert "output/" in str(e.value)


def test_scratch_cannot_be_declared_as_output(tmp_path):
    """R9：探索轨目录不得被声明为正式产物。"""
    bad = GOOD.replace("  - path: output/report.docx", "  - path: output/scratch/x.docx")
    with pytest.raises(ManifestError) as e:
        load_manifest(_skill(tmp_path / "sk", bad))
    assert "scratch" in str(e.value)


def test_duplicate_step_id_is_rejected(tmp_path):
    bad = GOOD.replace("  - id: compute\n", "  - id: confirm_scope\n")
    with pytest.raises(ManifestError) as e:
        load_manifest(_skill(tmp_path / "sk", bad))
    assert "重复" in str(e.value)


def test_missing_referenced_file_is_caught_at_load_not_runtime(tmp_path):
    """entry 指向不存在的文件 —— 注册时就要报，不能等跑起来才崩。"""
    d = _skill(tmp_path / "sk", GOOD, files=("cards/scope.json", "schemas/compute.json",
                                      "refs/methodology.md", "refs/glossary.md"))
    with pytest.raises(ManifestError) as e:
        load_manifest(d)
    msg = str(e.value)
    assert "steps/compute.py" in msg and "不存在" in msg
    assert "行" in msg


def test_yaml_syntax_error_reports_line(tmp_path):
    with pytest.raises(ManifestError) as e:
        load_manifest(_skill(tmp_path / "sk", "id: x\n  bad indent: [\n"))
    assert "YAML 语法错误" in str(e.value)


def test_bad_max_null_ratio_is_rejected(tmp_path):
    bad = GOOD + "\ndata_contract:\n  min_rows: 30\n  max_null_ratio: 1.5\n"
    with pytest.raises(ManifestError) as e:
        load_manifest(_skill(tmp_path / "sk", bad))
    assert "max_null_ratio" in str(e.value)


# ── 注册表 ─────────────────────────────────────────────────────────

def test_new_skill_appears_without_restart(tmp_path):
    """R3 验收：新增 skill 目录后无需重启即可出现在列表中。"""
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    reg = SkillRegistry(skills_dir=skills_root)
    assert reg.list() == [], "初始应为空"

    # 运行中往 skills 根目录里放一个新 skill，不重建注册表实例
    _skill(skills_root / "ux-report", GOOD)

    got = reg.list()
    assert [s.id for s in got] == ["ux-report"], "同一个注册表实例应看到新 skill"


def test_edited_skill_is_reloaded_without_restart(tmp_path):
    """改了 skill.yaml 也要生效，不是只认第一次加载的快照。"""
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    d = _skill(skills_root / "ux-report", GOOD)
    reg = SkillRegistry(skills_dir=skills_root)
    assert reg.require("ux-report").manifest.version == "0.3.0"

    import os
    manifest = d / "skill.yaml"
    manifest.write_text(GOOD.replace("version: 0.3.0", "version: 0.4.0"), encoding="utf-8")
    os.utime(manifest, (0, 0))   # 强制 mtime 变化，避免同秒写入被判为未变

    assert reg.require("ux-report").manifest.version == "0.4.0"


def test_broken_skill_does_not_break_the_whole_list(tmp_path):
    """一个 skill 写错了，不该让整个 skill 列表打不开。"""
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    _skill(skills_root / "ux-report", GOOD)

    broken = skills_root / "broken"
    broken.mkdir()
    (broken / "skill.yaml").write_text("id: broken\nname: 坏的\nversion: 0.1.0\nsteps: []\n",
                                       encoding="utf-8")

    reg = SkillRegistry(skills_dir=skills_root)
    got = reg.list()
    assert [s.id for s in got] == ["ux-report"], "好的 skill 仍要能列出来"
    assert "broken" in reg.errors
    assert "至少要声明一个 step" in reg.errors["broken"]


def test_reference_path_cannot_escape_skill_dir(tmp_path):
    """路径穿越：../../etc/passwd 这类必须挡住。"""
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    _skill(skills_root / "ux-report", GOOD)
    reg = SkillRegistry(skills_dir=skills_root)
    sk = reg.require("ux-report")
    with pytest.raises(ValueError):
        sk.file("../../../etc/passwd")
