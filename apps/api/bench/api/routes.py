"""API 路由（PRD 6.3 契约）。

    GET  /api/skills                    可用 skill 列表
    GET  /api/sessions                  会话列表，游标分页
    POST /api/sessions                  新建会话
    GET  /api/sessions/{id}             会话详情
    GET  /api/sessions/{id}/parts       历史回放，支持 after_seq
    POST /api/sessions/{id}/upload      上传输入文件到工作区
    POST /api/sessions/{id}/messages    发消息并触发 Run，返回 run_id
    GET  /api/sessions/{id}/events      SSE 流
    GET  /api/runs/{id}                 Run 详情（含步骤状态）
    POST /api/runs/{id}/resume          提交卡片答案
    POST /api/runs/{id}/cancel          取消
    POST /api/runs/{id}/retry           从失败步骤重试
    GET  /api/artifacts/{id}/download   302 到签名 URL
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import RedirectResponse, StreamingResponse

from bench.api.deps import CurrentUser, Db, Queue, Registry
from bench.api.schemas import (
    ArtifactOut,
    CardOut,
    MessageCreate,
    PartOut,
    PartsOut,
    ResumeIn,
    RunDetailOut,
    RunOut,
    SessionCreate,
    SessionListOut,
    SessionOut,
    SkillSummary,
    StepOut,
    UploadOut,
)
from bench.api.sse import event_stream, parse_last_event_id
from bench.config import get_settings
from bench.db.repo import ArtifactRepo, CardRepo, PartRepo, RunRepo, SessionRepo, StepRepo
from bench.db.repo.base import NotFound
from bench.db.session import get_session_factory
from bench.events.bus import get_bus
from bench.events.emitter import Emitter
from bench.events.types import EventType
from bench.orchestrator.cards import CardAnswerError, parse_card_spec, summarize, validate_answer
from bench.orchestrator.context import session_workspace

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

#: 单个上传文件上限。PRD 2.2 N6：单次执行按 GB 级以内设计。
MAX_UPLOAD_BYTES = 512 * 1024 * 1024


def _404(exc: NotFound) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc))


# ══════════════════════════════════════════════════════════════════
# skills
# ══════════════════════════════════════════════════════════════════

@router.get("/skills", response_model=list[SkillSummary], tags=["skills"])
async def list_skills(reg: Registry) -> list[SkillSummary]:
    """R3 验收：新增 skill 目录后无需重启即可出现 —— registry.list() 每次重扫。"""
    out = []
    for sk in reg.list():
        m = sk.manifest
        out.append(SkillSummary(
            id=m.id, name=m.name, version=m.version, description=m.description,
            inputs=[i.model_dump(mode="json") for i in m.inputs],
            steps=[{"id": s.id, "type": s.type, "label": s.label or s.id}
                   for s in m.steps],
            outputs=[o.model_dump(mode="json") for o in m.outputs],
        ))
    return out


@router.get("/skills/errors", tags=["skills"])
async def skill_errors(reg: Registry) -> dict[str, str]:
    """加载失败的 skill 及其行号级报错，给平台维护者看（3.1 角色三）。"""
    reg.refresh()
    return reg.errors


# ══════════════════════════════════════════════════════════════════
# sessions
# ══════════════════════════════════════════════════════════════════

@router.get("/sessions", response_model=SessionListOut, tags=["sessions"])
async def list_sessions(db: Db, user: CurrentUser,
                        limit: int = Query(50, ge=1, le=200),
                        cursor: dt.datetime | None = None) -> SessionListOut:
    rows = await SessionRepo(db, user).list(limit=limit, cursor=cursor)
    runs = RunRepo(db, user)
    items = []
    for s in rows:
        latest = await runs.latest_for_session(s.id)
        items.append(SessionOut(
            id=s.id, skill_id=s.skill_id, title=s.title, status=s.status,
            created_at=s.created_at, updated_at=s.updated_at,
            latest_run_id=latest.id if latest else None,
        ))
    return SessionListOut(items=items,
                          next_cursor=rows[-1].updated_at if len(rows) == limit else None)


@router.post("/sessions", response_model=SessionOut,
             status_code=status.HTTP_201_CREATED, tags=["sessions"])
async def create_session(body: SessionCreate, db: Db, user: CurrentUser,
                         reg: Registry) -> SessionOut:
    if body.skill_id:
        try:
            reg.require(body.skill_id)
        except KeyError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    s = await SessionRepo(db, user).create(skill_id=body.skill_id,
                                           title=body.title or "新会话")
    await db.commit()
    return SessionOut(id=s.id, skill_id=s.skill_id, title=s.title, status=s.status,
                      created_at=s.created_at, updated_at=s.updated_at)


@router.get("/sessions/{session_id}", response_model=SessionOut, tags=["sessions"])
async def get_session(session_id: uuid.UUID, db: Db, user: CurrentUser) -> SessionOut:
    try:
        s = await SessionRepo(db, user).get(session_id)
    except NotFound as exc:
        raise _404(exc) from exc
    latest = await RunRepo(db, user).latest_for_session(s.id)
    return SessionOut(id=s.id, skill_id=s.skill_id, title=s.title, status=s.status,
                      created_at=s.created_at, updated_at=s.updated_at,
                      latest_run_id=latest.id if latest else None)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT,
               tags=["sessions"])
async def delete_session(session_id: uuid.UUID, db: Db, user: CurrentUser) -> Response:
    """R1：软删除，30 天后由 worker 清理产物。"""
    try:
        await SessionRepo(db, user).soft_delete(session_id)
    except NotFound as exc:
        raise _404(exc) from exc
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/sessions/{session_id}/parts", response_model=PartsOut, tags=["sessions"])
async def list_parts(session_id: uuid.UUID, db: Db, user: CurrentUser,
                     after_seq: int = Query(0, ge=0)) -> PartsOut:
    """R2 历史回放。与 SSE 补发共用同一个查询，因此两边内容必然一致。"""
    repo = PartRepo(db, user)
    try:
        rows = await repo.list_after(session_id, after_seq=after_seq)
        top = await repo.max_seq(session_id)
    except NotFound as exc:
        raise _404(exc) from exc
    return PartsOut(
        items=[PartOut(id=p.id, seq=p.seq, type=p.type, payload=p.payload,
                       created_at=p.created_at) for p in rows],
        max_seq=top,
    )


@router.get("/sessions/{session_id}/artifacts", response_model=list[ArtifactOut],
            tags=["sessions"])
async def list_artifacts(session_id: uuid.UUID, db: Db,
                         user: CurrentUser) -> list[ArtifactOut]:
    """R9 验收：探索轨产物不出现在产物列表 —— 默认只列 track='trusted'。"""
    try:
        rows = await ArtifactRepo(db, user).list_for_session(session_id)
    except NotFound as exc:
        raise _404(exc) from exc
    return [ArtifactOut(id=a.id, filename=a.filename, mime=a.mime,
                        size_bytes=a.size_bytes, track=a.track,
                        has_preview=a.preview_key is not None,
                        created_at=a.created_at) for a in rows]


@router.post("/sessions/{session_id}/upload", response_model=UploadOut,
             tags=["sessions"])
async def upload_input(session_id: uuid.UUID, db: Db, user: CurrentUser,
                       file: Annotated[UploadFile, File()]) -> UploadOut:
    """把输入文件放进会话工作区。

    工作区按 session_id 分目录，所以文件不会串到别的会话（R1 验收）。
    """
    try:
        await SessionRepo(db, user).require_session(session_id)
    except NotFound as exc:
        raise _404(exc) from exc

    name = Path(file.filename or "upload.bin").name      # 去掉任何路径成分
    ws = session_workspace(session_id)
    dest = ws / name
    h = hashlib.sha256()
    size = 0
    with open(dest, "wb") as f:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                f.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                                    detail=f"文件超过 {MAX_UPLOAD_BYTES // (1<<20)}MB 上限")
            h.update(chunk)
            f.write(chunk)
    return UploadOut(filename=name, size_bytes=size, sha256=h.hexdigest())


@router.post("/sessions/{session_id}/messages", response_model=RunOut,
             status_code=status.HTTP_202_ACCEPTED, tags=["sessions"])
async def post_message(session_id: uuid.UUID, body: MessageCreate, db: Db,
                       user: CurrentUser, reg: Registry, queue: Queue) -> RunOut:
    """发消息并触发 Run。

    R7 验收：「提交后立即有明确的『已受理』反馈，不出现无响应的空白期」——
    所以这里 202 立刻返回 run_id，实际执行交给 worker。
    """
    sessions = SessionRepo(db, user)
    try:
        s = await sessions.get(session_id)
    except NotFound as exc:
        raise _404(exc) from exc
    if not s.skill_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="会话尚未绑定 skill")
    try:
        skill = reg.require(s.skill_id)
    except KeyError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    settings = get_settings()
    emitter = Emitter(db, session_id, user, bus=get_bus())
    await emitter.open_message("user")
    if body.text:
        await emitter.text(body.text)

    run = await RunRepo(db, user).create(
        session_id=session_id, skill_id=skill.id,
        skill_version=skill.manifest.version,
        model=settings.llm_model, prompt_version=settings.prompt_version,
        input_hashes={"_inputs": body.inputs},
    )
    await StepRepo(db, user).create_many(
        run_id=run.id, session_id=session_id,
        steps=[(st.id, st.type) for st in skill.manifest.steps])
    await sessions.set_status(session_id, "running")
    await sessions.touch(session_id)
    await db.commit()

    await queue.enqueue_job("execute_run", str(run.id), str(user), start_index=0)
    await emitter.signal(EventType.RUN_STARTED,
                         {"run_id": str(run.id), "status": "queued"})
    return _run_out(run)


@router.get("/sessions/{session_id}/events", tags=["sessions"])
async def session_events(session_id: uuid.UUID, request: Request, user: CurrentUser,
                         last_event_id: Annotated[str | None,
                                                  Header(alias="Last-Event-ID")] = None,
                         after_seq: int | None = None) -> StreamingResponse:
    """SSE 流（R2）。

    浏览器 EventSource 断线重连时会自动带 Last-Event-ID 头；
    首次连接可用 ?after_seq= 指定从哪里开始（比如 RSC 已经渲染了前 N 条）。
    """
    async with get_session_factory()() as db:
        if not await SessionRepo(db, user).owns_session(session_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="会话不存在")

    start = after_seq if after_seq is not None else parse_last_event_id(last_event_id)
    stream = event_stream(session_id=session_id, user_id=user,
                          db_factory=get_session_factory(), bus=get_bus(),
                          last_event_id=start)
    return StreamingResponse(stream, media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",     # 关掉 nginx 缓冲，否则事件会被攒住
    })


# ══════════════════════════════════════════════════════════════════
# runs
# ══════════════════════════════════════════════════════════════════

def _run_out(run) -> RunOut:
    return RunOut(id=run.id, session_id=run.session_id, skill_id=run.skill_id,
                  skill_version=run.skill_version, status=run.status,
                  current_step=run.current_step, error=run.error,
                  token_in=run.token_in, token_out=run.token_out,
                  started_at=run.started_at, ended_at=run.ended_at)


@router.get("/runs/{run_id}", response_model=RunDetailOut, tags=["runs"])
async def get_run(run_id: uuid.UUID, db: Db, user: CurrentUser) -> RunDetailOut:
    try:
        run = await RunRepo(db, user).get(run_id)
    except NotFound as exc:
        raise _404(exc) from exc
    steps = await StepRepo(db, user).list_for_run(run_id)
    card = await CardRepo(db, user).pending_for_run(run_id)
    return RunDetailOut(
        **_run_out(run).model_dump(),
        steps=[StepOut(step_id=s.step_id, idx=s.idx, type=s.type, track=s.track,
                       status=s.status, attempt=s.attempt, started_at=s.started_at,
                       ended_at=s.ended_at, error=s.error) for s in steps],
        pending_card_id=card.id if card else None,
    )


@router.get("/runs/{run_id}/card", response_model=CardOut, tags=["runs"])
async def get_pending_card(run_id: uuid.UUID, db: Db, user: CurrentUser) -> CardOut:
    """R5 验收：关掉浏览器隔天再打开，卡片仍在原位可作答 ——
    因为它在库里，不在某个协程的内存里。"""
    try:
        card = await CardRepo(db, user).pending_for_run(run_id)
    except NotFound as exc:
        raise _404(exc) from exc
    if card is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="该 Run 没有待作答的卡片")
    return CardOut(id=card.id, run_id=card.run_id, step_id=card.step_id,
                   spec=card.spec, status=card.status, response=card.response,
                   expires_at=card.expires_at)


@router.post("/runs/{run_id}/resume", response_model=RunOut, tags=["runs"])
async def resume_run(run_id: uuid.UUID, body: ResumeIn, db: Db, user: CurrentUser,
                     reg: Registry, queue: Queue) -> RunOut:
    """提交卡片答案并续跑（R5 协议第 5–6 步）。"""
    runs = RunRepo(db, user)
    cards = CardRepo(db, user)
    try:
        run = await runs.get(run_id)
        card = await cards.get(body.card_id)
    except NotFound as exc:
        raise _404(exc) from exc

    if card.run_id != run.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="卡片不属于该 Run")
    if card.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail=f"卡片已是 {card.status} 状态，不能重复作答")

    spec = parse_card_spec(card.spec)
    try:
        # 前端拦一道，后端再拦一道 —— 前端可以被绕过
        answer = validate_answer(spec, {"action": body.action, "values": body.values})
    except CardAnswerError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=exc.to_payload()) from exc

    await cards.answer(card.id, answer)
    emitter = Emitter(db, run.session_id, user, bus=get_bus())
    await emitter.open_message("user")
    await emitter.part("card_response", {
        "card_id": str(card.id), "step_id": card.step_id,
        "action": answer["action"], "values": answer["values"],
        "summary": summarize(spec, answer),      # 7.5：就地坍缩成一行摘要
        "cancelled": answer["cancelled"],
    })

    # 把原来的 card_request 标成已作答，回放时才是「不可编辑的摘要形态」
    if card.request_seq is not None:
        await _mark_card_request_answered(db, user, run.session_id, card.request_seq)

    if answer["cancelled"]:
        await runs.set_status(run.id, "cancelled")
        await SessionRepo(db, user).set_status(run.session_id, "idle")
        await cards.cancel_pending_for_run(run.id)
        await db.commit()
        await emitter.signal(EventType.RUN_CANCELLED, {"run_id": str(run.id)})
        return _run_out(await runs.get(run.id))

    await runs.set_status(run.id, "queued", current_step=card.step_id)
    await SessionRepo(db, user).set_status(run.session_id, "running")
    await db.commit()

    start_index = _next_index(reg, run.skill_id, card.step_id)
    await queue.enqueue_job("execute_run", str(run.id), str(user),
                            start_index=start_index)
    await emitter.signal(EventType.RUN_RESUMED,
                         {"run_id": str(run.id), "from_step": start_index})
    return _run_out(await runs.get(run.id))


@router.post("/runs/{run_id}/cancel", response_model=RunOut, tags=["runs"])
async def cancel_run(run_id: uuid.UUID, db: Db, user: CurrentUser) -> RunOut:
    """R8：执行中可取消，沙箱在 5 秒内被销毁。

    这里只置标志 —— worker 里的沙箱 runner 每秒轮询一次，命中即 kill。
    协作式取消比强杀 worker 干净：产物、日志、状态都能正常收尾。
    """
    runs = RunRepo(db, user)
    try:
        await runs.request_cancel(run_id)
        run = await runs.get(run_id)
    except NotFound as exc:
        raise _404(exc) from exc
    await CardRepo(db, user).cancel_pending_for_run(run_id)
    await db.commit()
    return _run_out(run)


@router.post("/runs/{run_id}/retry", response_model=RunOut, tags=["runs"])
async def retry_run(run_id: uuid.UUID, db: Db, user: CurrentUser,
                    reg: Registry, queue: Queue,
                    from_step: str | None = None) -> RunOut:
    """R8：从失败的 step 重试，而不是整个重跑。

    「重试保留原会话，追加新的 Run 记录」—— 所以这里建一个新 Run，
    把已成功步骤的输出继承过来，从失败那步开始。
    """
    runs = RunRepo(db, user)
    steps = StepRepo(db, user)
    try:
        old = await runs.get(run_id)
    except NotFound as exc:
        raise _404(exc) from exc

    if old.status not in ("failed", "cancelled", "expired"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail=f"Run 当前是 {old.status}，只有失败/取消/挂起的可重试")
    try:
        skill = reg.require(old.skill_id or "")
    except KeyError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    target = from_step
    if target is None:
        failed = await steps.first_failed(run_id)
        target = failed.step_id if failed else (old.current_step or skill.manifest.steps[0].id)
    try:
        start_index = skill.manifest.step_index(target)
    except KeyError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"步骤 {target!r} 不在该 skill 中") from exc

    settings = get_settings()
    new = await runs.create(
        session_id=old.session_id, skill_id=old.skill_id,
        skill_version=skill.manifest.version, model=settings.llm_model,
        prompt_version=settings.prompt_version, input_hashes=old.input_hashes or {})
    rows = await steps.create_many(
        run_id=new.id, session_id=old.session_id,
        steps=[(s.id, s.type) for s in skill.manifest.steps])

    # 继承已成功步骤的输出，这样 render 不必等 compute 重跑一遍
    previous = await steps.outputs_so_far(run_id)
    for row in rows:
        if row.idx < start_index and row.step_id in previous:
            await steps.finish(row.id, status="succeeded", output=previous[row.step_id])
    await SessionRepo(db, user).set_status(old.session_id, "running")
    await db.commit()

    await queue.enqueue_job("execute_run", str(new.id), str(user),
                            start_index=start_index)
    return _run_out(await runs.get(new.id))


# ══════════════════════════════════════════════════════════════════
# artifacts
# ══════════════════════════════════════════════════════════════════

@router.get("/artifacts/{artifact_id}/download", tags=["artifacts"])
async def download_artifact(artifact_id: uuid.UUID, db: Db, user: CurrentUser,
                            preview: bool = False) -> RedirectResponse:
    """R6：下载走签名 URL（有效期 1 小时），不暴露存储直链。"""
    from bench.storage.base import get_store

    try:
        art = await ArtifactRepo(db, user).get(artifact_id)
    except NotFound as exc:
        raise _404(exc) from exc

    key = art.preview_key if (preview and art.preview_key) else art.storage_key
    if preview and not art.preview_key:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            detail="该产物没有 PDF 预览（转换失败时降级为仅 Word）")
    try:
        url = await get_store().signed_url(
            key, expires_in=get_settings().signed_url_ttl,
            download_name=None if preview else art.filename)
    except Exception as exc:  # noqa: BLE001
        logger.exception("签名失败")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            detail=f"生成下载链接失败：{exc}") from exc
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


# ══════════════════════════════════════════════════════════════════

def _next_index(reg, skill_id: str | None, step_id: str) -> int:
    """作答后从下一步继续。ask_user 发的临时卡片没有对应 step，原地续跑。"""
    if not skill_id:
        return 0
    try:
        skill = reg.require(skill_id)
        return skill.manifest.step_index(step_id) + 1
    except (KeyError, Exception):
        return 0


async def _mark_card_request_answered(db, user, session_id: uuid.UUID,
                                      seq: int) -> None:
    from sqlalchemy import select

    from bench.db.models import Part
    part = await db.scalar(
        select(Part).where(Part.session_id == session_id, Part.seq == seq))
    if part is not None:
        await PartRepo(db, user).update_payload(
            part.id, {**part.payload, "status": "answered"})
