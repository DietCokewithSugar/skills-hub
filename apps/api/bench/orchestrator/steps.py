"""三种 step 的执行器。

python      —— 在沙箱里跑，输出过 R0 的 schema 与来源两道闸门
interaction —— 发卡片，挂起 Run（不阻塞 worker）
llm         —— 叙述性生成，输出过数字越界拦截

「Python 脚本负责所有计算、统计、聚合、筛选；LLM 负责理解意图、编排步骤、
生成叙述性文字，绝对不许计算、改写数字、估算、补全缺失数据。」（R0.1）
职责分离在代码里的落点就是这三个执行器互不越界。
"""

from __future__ import annotations

import json
import logging
import mimetypes
from pathlib import Path
from typing import Any

from bench.accuracy.preflight import PreflightError, run_preflight
from bench.accuracy.provenance import ProvenanceError, check_provenance
from bench.accuracy.validation import SchemaError, load_schema, validate_step_output
from bench.events.types import EventType
from bench.orchestrator.cards import (
    CardSpecError,
    FilePickCard,
    load_card_spec,
)
from bench.orchestrator.context import (
    SCRATCH,
    RunCancelled,
    RunContext,
    StepFailed,
    WaitingForInput,
)
from bench.sandbox.base import ExecRequest, Limits, SandboxError, UntrustedCodeRefused
from bench.skills.manifest import Step
from bench.storage.base import artifact_key

logger = logging.getLogger(__name__)

#: 日志尾行推送的节流：每这么多行推一次，避免刷屏把 SSE 压垮
LOG_FLUSH_EVERY = 1


# ══════════════════════════════════════════════════════════════════
# python step
# ══════════════════════════════════════════════════════════════════

async def run_python_step(ctx: RunContext, step: Step, idx: int) -> dict[str, Any]:
    await ctx.emitter.signal(EventType.STEP_STARTED, {
        "step_id": step.id, "idx": idx, "type": "python",
        "label": step.label or step.id,
    })

    # ── 闸门四：数据前置检查（R0.4，在计算前）────────────────────
    await _preflight_inputs(ctx, step)

    limits = _limits_for(ctx, step)
    params = ctx.step_params(step.id)

    async def on_stdout(line: str) -> None:
        from bench.sandbox.protocol import parse_stdout_line
        ev = parse_stdout_line(line)
        if ev.kind == "progress":
            await ctx.emitter.signal(EventType.STEP_PROGRESS, {
                "step_id": step.id, "pct": ev.pct, "msg": ev.msg})
        elif ev.kind == "log":
            await ctx.emitter.signal(EventType.STEP_LOG, {
                "step_id": step.id, "line": ev.msg, "stream": "stdout"})

    async def on_stderr(line: str) -> None:
        await ctx.emitter.signal(EventType.STEP_LOG, {
            "step_id": step.id, "line": line, "stream": "stderr"})

    try:
        result = await ctx.runner.run(
            ExecRequest(
                image=ctx.skill.manifest.runtime, workspace=ctx.workspace,
                entry=step.entry or "", params=params, limits=limits,
                track="trusted", run_id=str(ctx.run_id),
            ),
            on_stdout=on_stdout, on_stderr=on_stderr,
            should_cancel=ctx.cancel_requested,
        )
    except UntrustedCodeRefused:
        raise
    except SandboxError as exc:
        raise StepFailed(step.id, f"沙箱执行失败：{exc}", retryable=True) from exc

    await ctx.runs.set_sandbox_ref(ctx.run_id, result.sandbox_ref)

    if result.exit_code == 125:
        raise RunCancelled()

    if result.timed_out:
        # R4 验收：超时后强杀，状态置 failed 并显示超时原因
        raise StepFailed(
            step.id,
            f"执行超过 {limits.timeout_s} 秒被强制终止",
            detail={"kind": "timeout", "timeout_s": limits.timeout_s,
                    "stderr_tail": result.stderr[-2000:]},
            retryable=True,
        )

    if not result.ok:
        raise StepFailed(
            step.id,
            _explain_failure(result.stderr) or f"脚本以退出码 {result.exit_code} 结束",
            detail={"kind": "exit_code", "exit_code": result.exit_code,
                    "stderr_tail": result.stderr[-4000:]},
            retryable=True,
        )

    # ── 读 result.json 并过两道闸门 ──────────────────────────────
    output = _read_result(ctx, step, result.outputs)
    if output is not None:
        _gate_schema(ctx, step, output)
        _gate_provenance(step, output)

    # ── 收集产物 ────────────────────────────────────────────────
    await _collect_artifacts(ctx, step, result.outputs)

    await ctx.emitter.signal(EventType.STEP_COMPLETED, {
        "step_id": step.id, "duration_ms": result.duration_ms})
    return output or {}


async def _preflight_inputs(ctx: RunContext, step: Step) -> None:
    """对声明了 data_contract 的输入文件做前置检查。

    只在第一个 python step 之前跑一次 —— 后续步骤吃的是上一步的输出，
    再体检一遍原始输入没有意义。
    """
    contract = ctx.skill.manifest.data_contract
    if contract is None:
        return
    if any(s.type == "python" for s in ctx.skill.manifest.steps[
            :ctx.skill.manifest.step_index(step.id)]):
        return

    for inp in ctx.skill.manifest.inputs:
        if inp.type != "file":
            continue
        name = ctx.inputs.get(inp.key)
        if not name:
            if inp.required:
                raise StepFailed(step.id, f"缺少必填输入：{inp.label}（{inp.key}）",
                                 retryable=False)
            continue
        path = ctx.workspace / str(name)
        if not path.exists():
            raise StepFailed(step.id, f"输入文件不存在：{name}", retryable=False)
        try:
            run_preflight(path, contract)
        except PreflightError as exc:
            # 「在计算前中断并明确指出缺哪一列，不产出报告」——
            # message 必须是**具体原因**，不是「输入数据未通过前置检查」
            # 这种只说了「失败了」的话。7.7 要求说清什么原因。
            reason = "；".join(exc.problems) if exc.problems else str(exc)
            raise StepFailed(
                step.id, f"{path.name}：{reason}",
                detail=exc.to_payload(),
                # 同一份输入重试还是同样结果，所以不给「重试」，
                # 但下一步怎么办要说清楚（见 hint）
                retryable=False,
            ) from exc


def _read_result(ctx: RunContext, step: Step,
                 outputs: dict[str, Path]) -> dict[str, Any] | None:
    rj = outputs.get("result.json")
    if rj is None:
        if step.output_schema:
            raise StepFailed(
                step.id,
                "步骤声明了 output_schema，但没有写出 /output/result.json",
                detail={"hint": "在脚本里调用 bench.emit_result(result)"},
                retryable=False,
            )
        return None
    try:
        return json.loads(rj.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StepFailed(step.id, f"result.json 不是合法 JSON：{exc}",
                         retryable=False) from exc


def _gate_schema(ctx: RunContext, step: Step, output: dict[str, Any]) -> None:
    """闸门一。「绝不允许把不合法的数据往下传。」"""
    if not step.output_schema:
        return
    try:
        schema = load_schema(ctx.skill.file(step.output_schema))
        validate_step_output(step.id, output, schema, schema_path=step.output_schema)
    except SchemaError as exc:
        raise StepFailed(step.id, "；".join(exc.problems) or str(exc),
                         detail=exc.to_payload(), retryable=False) from exc


def _gate_provenance(step: Step, output: dict[str, Any]) -> None:
    """闸门二。没有来源标注的数字不许进报告。"""
    try:
        # require_metrics=False：这一步该不该有指标由它的 output_schema 决定；
        # 本闸门只负责「已有的指标必须能追溯」
        check_provenance(output, step_id=step.id, require_metrics=False)
    except ProvenanceError as exc:
        raise StepFailed(step.id, "；".join(exc.problems) or str(exc),
                         detail=exc.to_payload(), retryable=False) from exc


async def _collect_artifacts(ctx: RunContext, step: Step,
                             outputs: dict[str, Path]) -> None:
    """把 /output 下的文件上传并写成 artifact Part。

    R6 验收：/output 下的文件在执行结束后 100% 出现在消息流中。
    R9 验收：/workspace/scratch/ 下的文件不会出现在产物列表中 ——
    这里的 outputs 本来就只来自 /output，scratch 在 runner 层就没进来。
    """
    from bench.reports.pdf import convert_to_pdf

    declared = {o.path.removeprefix("output/"): o.label
                for o in ctx.skill.manifest.outputs}

    for rel, local in sorted(outputs.items()):
        if rel == "result.json":
            continue                       # 结构化输出，不是给人下载的产物
        if SCRATCH in Path(rel).parts:
            continue                       # 双保险：探索轨绝不进产物
        mime = mimetypes.guess_type(rel)[0] or "application/octet-stream"
        key = artifact_key(str(ctx.user_id), str(ctx.session_id),
                           str(ctx.run_id), rel)
        try:
            size = await ctx.store.upload(key, local, content_type=mime)
        except Exception as exc:  # noqa: BLE001
            raise StepFailed(step.id, f"产物上传失败：{exc}", retryable=True) from exc

        preview_key: str | None = None
        if rel.endswith(".docx"):
            # R6：转换失败降级为仅 Word，不阻塞执行 —— convert_to_pdf 返回 None
            pdf = await convert_to_pdf(local)
            if pdf is not None:
                preview_key = key.rsplit(".", 1)[0] + ".pdf"
                try:
                    await ctx.store.upload(preview_key, pdf,
                                           content_type="application/pdf")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("PDF 预览上传失败，降级为仅 Word：%s", exc)
                    preview_key = None

        art = await ctx.artifacts.create(
            session_id=ctx.session_id, run_id=ctx.run_id,
            filename=declared.get(rel, Path(rel).name), mime=mime,
            storage_key=key, size_bytes=size, track="trusted",
            preview_key=preview_key,
        )
        await ctx.db.commit()
        await ctx.emitter.artifact(
            artifact_id=art.id, filename=art.filename, mime=mime,
            size_bytes=size, track="trusted", has_preview=preview_key is not None)
        await ctx.emitter.signal(EventType.ARTIFACT_CREATED, {
            "artifact_id": str(art.id), "filename": art.filename})


def _limits_for(ctx: RunContext, step: Step) -> Limits:
    """skill 声明的限制，收敛到平台硬上限内。"""
    m = ctx.skill.manifest.limits
    lim = Limits.platform_defaults()
    if step.timeout_s:
        lim.timeout_s = step.timeout_s
    elif m.timeout_s:
        lim.timeout_s = m.timeout_s
    if m.memory_mb:
        lim.memory_mb = m.memory_mb
    if m.disk_mb:
        lim.disk_mb = m.disk_mb
    # 默认无出网；只有 skill 显式声明白名单才放行（6.4）
    lim.network = list(m.network_allowlist) if m.network_allowlist else "none"
    return lim.clamp_to_platform()


def _explain_failure(stderr: str) -> str:
    """从 stderr 里挑出最像「原因」的一行。

    7.7：「执行失败：直接说明哪一步、什么原因、下一步怎么办。
    错误文案不道歉、不含糊。」所以这里宁可给一行具体的 traceback 末行，
    也不要「执行出错，请重试」。
    """
    if not stderr.strip():
        return ""
    lines = [ln.strip() for ln in stderr.strip().splitlines() if ln.strip()]
    # bench.fail() 写的是一行 JSON，优先用它
    for ln in reversed(lines):
        if ln.startswith("{"):
            try:
                obj = json.loads(ln)
                if isinstance(obj, dict) and obj.get("error"):
                    return str(obj["error"])
            except json.JSONDecodeError:
                pass
    return lines[-1][:500]


# ══════════════════════════════════════════════════════════════════
# interaction step
# ══════════════════════════════════════════════════════════════════

async def run_interaction_step(ctx: RunContext, step: Step, idx: int) -> None:
    """发卡片并挂起 Run。

    注意这里**不等待** —— 抛 WaitingForInput 让 worker 任务干净结束。
    一张等 24 小时的卡片不该占着 worker 槽位（R5 的持久化设计）。
    """
    try:
        spec = load_card_spec(ctx.skill.file(step.card or ""))
    except (CardSpecError, ValueError, OSError) as exc:
        raise StepFailed(step.id, f"卡片定义有问题：{exc}", retryable=False) from exc

    # file_pick 的候选来自工作区实况
    if isinstance(spec, FilePickCard) and not spec.candidates:
        spec.candidates = sorted(
            p.name for p in ctx.workspace.iterdir()
            if p.is_file() and (not spec.accept
                                or p.suffix.lower() in {a.lower() for a in spec.accept})
        )

    payload = spec.model_dump(mode="json")
    _, seq = await ctx.emitter.part("card_request", {
        "step_id": step.id, "spec": payload, "status": "pending",
    })
    card = await ctx.cards.create(
        run_id=ctx.run_id, session_id=ctx.session_id, step_id=step.id,
        spec=payload, timeout_hours=spec.timeout_hours, request_seq=seq,
    )
    await ctx.runs.set_status(ctx.run_id, "waiting_for_input", current_step=step.id)
    await ctx.sessions.set_status(ctx.session_id, "waiting_for_input")
    await ctx.db.commit()

    await ctx.emitter.signal(EventType.CARD_REQUESTED, {
        "card_id": str(card.id), "step_id": step.id, "seq": seq,
        "expires_at": card.expires_at.isoformat(),
    })
    await ctx.emitter.signal(EventType.RUN_WAITING, {
        "run_id": str(ctx.run_id), "step_id": step.id})
    raise WaitingForInput(card.id, step.id)


# ══════════════════════════════════════════════════════════════════
# llm step
# ══════════════════════════════════════════════════════════════════

async def run_llm_step(ctx: RunContext, step: Step, idx: int) -> dict[str, Any]:
    """叙述性生成。

    R0.1：LLM 只能引用计算结果。所以生成的文本在这里就过数字越界拦截，
    不合格重试两次，仍不合格则按 R0.2 转人工确认卡片。
    """
    from bench.orchestrator.llm_step import generate_narrative
    return await generate_narrative(ctx, step, idx)
