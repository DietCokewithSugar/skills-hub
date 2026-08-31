"""R0.5 回归测试集。

「每个 skill 附带至少一组 fixtures/：固定输入 + 期望输出。skill 变更或
平台升级时跑一遍，数值型输出必须完全一致。**这是唯一能长期保证结果准确的
机制，其他都是一次性的。**」

目录约定：

    skills/<id>/fixtures/<case>/
        input/            输入文件，执行前拷进工作区
        params.json       该 case 的参数与卡片答案（可选）
        expected/result.json   期望输出（首次可用 --update 生成）

跑法：

    python -m bench.accuracy.fixtures run --skill ux-report --repeat 5

只跑 python step —— 回归测试要验的是计算的确定性。llm 与 interaction step
被跳过，它们的输出本来就不该是数值（叙述型输出人工抽查，见 R0.5）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bench.accuracy.preflight import PreflightError
from bench.accuracy.provenance import ProvenanceError, check_provenance, iter_metrics
from bench.accuracy.validation import SchemaError, load_schema, validate_step_output
from bench.sandbox.base import ExecRequest, Limits, get_runner
from bench.skills.registry import LoadedSkill, SkillRegistry


class FixtureFailure(Exception):
    pass


@dataclass
class CaseResult:
    case: str
    ok: bool
    result: dict[str, Any] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def numeric_leaves(obj: Any, path: str = "") -> dict[str, float]:
    """摊平出所有数值叶子。回归比对只看这些 —— 「数值型输出必须完全一致」。"""
    out: dict[str, float] = {}
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out[path or "(根)"] = float(obj)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            # source 里有哈希与行号，不是「输出」，不参与数值比对
            if k == "source":
                continue
            out.update(numeric_leaves(v, f"{path}.{k}" if path else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(numeric_leaves(v, f"{path}[{i}]"))
    return out


def diff_numeric(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    """两份 result.json 的数值差异。完全一致才算通过，不设容差。"""
    la, lb = numeric_leaves(a), numeric_leaves(b)
    problems = []
    for k in sorted(set(la) | set(lb)):
        if k not in la:
            problems.append(f"{k}：期望有值 {lb[k]}，实际缺失")
        elif k not in lb:
            problems.append(f"{k}：实际多出 {la[k]}，期望没有")
        elif la[k] != lb[k]:
            problems.append(f"{k}：期望 {lb[k]}，实际 {la[k]}")
    return problems


def _preflight_case(skill: LoadedSkill, workspace: Path,
                    params: dict[str, Any]) -> None:
    """在第一个 python step 之前跑数据契约检查（R0.4）。"""
    from bench.accuracy.preflight import run_preflight

    contract = skill.manifest.data_contract
    if contract is None:
        return
    for inp in skill.manifest.inputs:
        if inp.type != "file":
            continue
        name = params.get(inp.key)
        if not name:
            continue
        path = workspace / str(name)
        if path.exists():
            run_preflight(path, contract)


def case_config(case_dir: Path) -> dict[str, Any]:
    """case.json：声明这个 case 的期望。

    默认期望成功。负向 case 写 {"expect": "reject", "reason_contains": "scope"}，
    表示这份输入**必须**被拦下 —— 「异常输入拦截率 ≥ 95%」这个成功指标
    需要负向 case 才量得出来，跑通的 case 是量不出拦截率的。
    """
    f = case_dir / "case.json"
    if not f.exists():
        return {"expect": "succeed"}
    return json.loads(f.read_text(encoding="utf-8"))


def list_cases(skill: LoadedSkill) -> list[Path]:
    root = skill.root / "fixtures"
    if not root.exists():
        return []
    return sorted(d for d in root.iterdir() if d.is_dir() and (d / "input").exists())


async def run_case(skill: LoadedSkill, case_dir: Path) -> dict[str, Any]:
    """跑一个 case 的全部 python step，返回合并后的 result.json。"""
    runner = get_runner()
    workspace = Path(tempfile.mkdtemp(prefix=f"bench-fx-{skill.id}-"))
    try:
        shutil.copytree(case_dir / "input", workspace, dirs_exist_ok=True)
        # skill 的脚本目录也要进工作区，入口才找得到
        for sub in ("steps", "templates", "schemas", "assets"):
            src = skill.root / sub
            if src.exists():
                shutil.copytree(src, workspace / sub, dirs_exist_ok=True)

        params_file = case_dir / "params.json"
        base_params: dict[str, Any] = (
            json.loads(params_file.read_text(encoding="utf-8"))
            if params_file.exists() else {}
        )

        merged: dict[str, Any] = {}
        # 按 step_id 归键 —— 必须与 RunContext.step_params 的形状一致，
        # 否则 fixtures 跑通了线上却拿不到前序结果（这里踩过一次）
        outputs: dict[str, Any] = {}
        limits = Limits.platform_defaults()
        if skill.manifest.limits.timeout_s:
            limits.timeout_s = min(limits.timeout_s, skill.manifest.limits.timeout_s)

        first_python = True
        for step in skill.manifest.steps:
            if step.type != "python":
                continue
            # 生产路径在第一个 python step 前跑 preflight（R0.4），
            # 回归也必须跑，否则「计算前拦截」这条根本没被测到
            if first_python:
                _preflight_case(skill, workspace, base_params)
                first_python = False
            params = {**base_params, "_steps": dict(outputs)}
            res = await runner.run(ExecRequest(
                image=skill.manifest.runtime, workspace=workspace,
                entry=step.entry or "", params=params, limits=limits,
                track="trusted",
            ))
            if not res.ok:
                raise FixtureFailure(
                    f"步骤 {step.id!r} 执行失败（exit={res.exit_code}）：\n"
                    f"{res.stderr[-2000:]}"
                )
            rj = res.outputs.get("result.json")
            if rj is None:
                continue
            out = json.loads(rj.read_text(encoding="utf-8"))

            # 回归跑的也是正式轨，同样要过 schema 与来源两道闸门
            if step.output_schema:
                validate_step_output(
                    step.id, out, load_schema(skill.file(step.output_schema)),
                    schema_path=step.output_schema)
            check_provenance(out, step_id=step.id, require_metrics=False)

            outputs[step.id] = out
            merged = _deep_merge(merged, out)
            # 产物回灌工作区，供后续 step 使用
            for rel, local in res.outputs.items():
                dest = workspace / "_prev_output" / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(local, dest)
        return merged
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _deep_merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


async def run_skill_fixtures(skill: LoadedSkill, *, repeat: int = 1,
                             update: bool = False) -> list[CaseResult]:
    cases = list_cases(skill)
    if not cases:
        return [CaseResult(case="(无)", ok=False,
                           problems=[f"skill {skill.id!r} 没有 fixtures/ —— "
                                     "R0.5 要求每个 skill 至少附带一组"])]

    results: list[CaseResult] = []
    for case_dir in cases:
        cfg = case_config(case_dir)
        expect_reject = cfg.get("expect") == "reject"
        problems: list[str] = []
        runs: list[dict[str, Any]] = []
        try:
            for _ in range(max(1, repeat)):
                runs.append(await run_case(skill, case_dir))
        except (FixtureFailure, PreflightError, SchemaError, ProvenanceError) as exc:
            if expect_reject:
                # 负向 case：被拦下就是通过。还要确认拦截理由说到了点上 ——
                # 「因为别的原因崩了」不算成功拦截。
                needle = cfg.get("reason_contains", "")
                if needle and needle not in str(exc):
                    results.append(CaseResult(
                        case=case_dir.name, ok=False,
                        problems=[f"被拦下了，但理由里没有出现 {needle!r}：{exc}"]))
                else:
                    results.append(CaseResult(case=case_dir.name, ok=True,
                                              result={"_rejected": str(exc)}))
            else:
                results.append(CaseResult(case=case_dir.name, ok=False,
                                          problems=[str(exc)]))
            continue

        if expect_reject:
            results.append(CaseResult(
                case=case_dir.name, ok=False,
                problems=["这份输入本应被拦截，却跑通了 —— "
                          "静默错误率必须为 0，这正是红线所在"]))
            continue

        # ① 自洽：连续多次执行，数值必须完全一致（R0 验收第 1 条）
        for i, r in enumerate(runs[1:], start=2):
            d = diff_numeric(r, runs[0])
            if d:
                problems += [f"第 {i} 次与第 1 次不一致 — {p}" for p in d]

        # ② 与期望一致
        expected_file = case_dir / "expected" / "result.json"
        if update:
            expected_file.parent.mkdir(parents=True, exist_ok=True)
            expected_file.write_text(
                json.dumps(runs[0], ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8")
        elif expected_file.exists():
            expected = json.loads(expected_file.read_text(encoding="utf-8"))
            problems += diff_numeric(runs[0], expected)
        else:
            problems.append(
                f"缺少 expected/result.json（首次可用 --update 生成）")

        # ③ 每个数值都能追溯
        coverage = check_provenance(runs[0], require_full_coverage=False,
                                    require_metrics=False)
        if coverage < 1.0:
            problems.append(f"来源可追溯率 {coverage:.0%}，未达 100%")

        results.append(CaseResult(case=case_dir.name, ok=not problems,
                                  result=runs[0], problems=problems))
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench.accuracy.fixtures",
                                 description="跑 skill 的回归测试集（R0.5）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="执行 fixtures")
    run.add_argument("--skill", help="只跑这个 skill；不给则全跑")
    run.add_argument("--repeat", type=int, default=1,
                     help="重复次数，用于验证确定性（验收标准建议 5）")
    run.add_argument("--update", action="store_true",
                     help="用本次结果覆写 expected/（谨慎）")
    run.add_argument("--skills-dir", default=None)
    args = ap.parse_args(argv)

    from bench.config import get_settings
    reg = SkillRegistry(skills_dir=Path(args.skills_dir) if args.skills_dir
                        else get_settings().skills_dir)
    skills = [reg.require(args.skill)] if args.skill else reg.list()
    if not skills:
        print("没有找到任何 skill", file=sys.stderr)
        return 2

    failed = 0
    for sk in skills:
        print(f"\n=== {sk.id}@{sk.manifest.version} "
              f"（重复 {args.repeat} 次）")
        for r in asyncio.run(run_skill_fixtures(sk, repeat=args.repeat,
                                                update=args.update)):
            if r.ok and "_rejected" in r.result:
                first = str(r.result["_rejected"]).splitlines()[0]
                print(f"  ✓ {r.case}：按预期在计算前被拦下 — {first}")
            elif r.ok:
                n = len(numeric_leaves(r.result))
                print(f"  ✓ {r.case}：{n} 个数值一致，"
                      f"{len(list(iter_metrics(r.result)))} 个指标全部可追溯")
            else:
                failed += 1
                print(f"  ✗ {r.case}")
                for p in r.problems:
                    print(f"      {p}")
    print()
    if failed:
        print(f"{failed} 个 case 未通过 —— "
              f"回归测试集通过率必须 100%，不达标不允许上线")
    else:
        print("全部通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
