"""R1 验收：会话隔离。

    - 用户 A 无法通过任何 API 路径读取用户 B 的 session / message / artifact
      （含直接构造 id）
    - 会话删除为软删除

v1 不做认证，后端以 service role 连库，RLS 被绕过 —— 因此这一层的隔离
由 repo 强制。这些测试直接构造别人的 id 去打每一个 repo 方法，确认
一个都读不到。等接上 Auth，RLS 会成为第二道闸门，这些测试仍然成立。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from bench.db.repo.artifacts import ArtifactRepo
from bench.db.repo.base import NotFound
from bench.db.repo.cards import CardRepo
from bench.db.repo.parts import PartRepo
from bench.db.repo.runs import RunRepo
from bench.db.repo.sessions import SessionRepo
from bench.db.repo.steps import StepRepo
from tests.conftest import requires_pg

pytestmark = [requires_pg, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def b_owned(db, user_b):
    """用户 B 的一整套数据：会话、Part、Run、Step、Card、Artifact。"""
    sessions = SessionRepo(db, user_b)
    s = await sessions.create(skill_id="ux-report", title="B 的机密会话")
    parts = PartRepo(db, user_b)
    msg = await parts.create_message(s.id, "user")
    p = await parts.append(session_id=s.id, message_id=msg.id,
                           type="text", payload={"text": "B 的机密内容"})
    run = await RunRepo(db, user_b).create(
        session_id=s.id, skill_id="ux-report", skill_version="0.3.0",
        model="m", prompt_version="v1")
    steps = await StepRepo(db, user_b).create_many(
        run_id=run.id, session_id=s.id, steps=[("compute", "python")])
    card = await CardRepo(db, user_b).create(
        run_id=run.id, session_id=s.id, step_id="confirm",
        spec={"type": "confirm", "title": "B 的卡片"}, timeout_hours=24)
    art = await ArtifactRepo(db, user_b).create(
        session_id=s.id, run_id=run.id, filename="b-report.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        storage_key="b/secret.docx", size_bytes=123)
    await db.commit()
    return {"session": s, "part": p, "run": run, "step": steps[0],
            "card": card, "artifact": art}


async def test_a_cannot_read_b_session_by_constructed_id(db, user_a, b_owned):
    with pytest.raises(NotFound):
        await SessionRepo(db, user_a).get(b_owned["session"].id)


async def test_a_cannot_list_b_sessions(db, user_a, b_owned):
    got = await SessionRepo(db, user_a).list()
    assert all(s.id != b_owned["session"].id for s in got)


async def test_a_cannot_read_b_parts(db, user_a, b_owned):
    with pytest.raises(NotFound):
        await PartRepo(db, user_a).list_after(b_owned["session"].id, after_seq=0)


async def test_a_cannot_read_b_run(db, user_a, b_owned):
    with pytest.raises(NotFound):
        await RunRepo(db, user_a).get(b_owned["run"].id)


async def test_a_cannot_read_b_artifact(db, user_a, b_owned):
    """产物泄露最要命：签名 URL 一旦发出去就收不回来。"""
    with pytest.raises(NotFound):
        await ArtifactRepo(db, user_a).get(b_owned["artifact"].id)


async def test_a_cannot_read_b_card(db, user_a, b_owned):
    with pytest.raises(NotFound):
        await CardRepo(db, user_a).get(b_owned["card"].id)


async def test_a_cannot_answer_b_card(db, user_a, b_owned):
    """作答别人的卡片 = 篡改别人的执行参数，必须挡住。"""
    with pytest.raises(NotFound):
        await CardRepo(db, user_a).answer(b_owned["card"].id, {"action": "confirm"})


async def test_a_cannot_read_b_steps(db, user_a, b_owned):
    got = await StepRepo(db, user_a).list_for_run(b_owned["run"].id)
    assert got == []


async def test_a_cannot_cancel_b_run(db, user_a, b_owned):
    with pytest.raises(NotFound):
        await RunRepo(db, user_a).request_cancel(b_owned["run"].id)


async def test_a_cannot_mutate_b_session(db, user_a, b_owned):
    for op in (
        lambda r: r.set_title(b_owned["session"].id, "被改了"),
        lambda r: r.set_status(b_owned["session"].id, "failed"),
        lambda r: r.soft_delete(b_owned["session"].id),
    ):
        with pytest.raises(NotFound):
            await op(SessionRepo(db, user_a))


async def test_nonexistent_id_is_also_notfound(db, user_a):
    """不存在与无权限返回同一种错误，不泄露存在性。"""
    with pytest.raises(NotFound):
        await SessionRepo(db, user_a).get(uuid.uuid4())


async def test_soft_delete_hides_session_but_keeps_row(db, user_a):
    """R1：会话删除为软删除（产物 30 天后才清理）。"""
    sessions = SessionRepo(db, user_a)
    s = await sessions.create()
    await db.commit()
    await sessions.soft_delete(s.id)
    await db.commit()

    with pytest.raises(NotFound):
        await sessions.get(s.id)
    assert all(x.id != s.id for x in await sessions.list())

    from sqlalchemy import select

    from bench.db.models import Session as SM
    row = await db.scalar(select(SM).where(SM.id == s.id))
    assert row is not None and row.deleted_at is not None, "软删除必须保留行"


async def test_two_sessions_artifacts_do_not_cross(db, user_a):
    """R1 验收：并发跑两个会话，产物归属正确，不串。"""
    sessions = SessionRepo(db, user_a)
    s1 = await sessions.create(title="会话一")
    s2 = await sessions.create(title="会话二")
    arts = ArtifactRepo(db, user_a)
    await arts.create(session_id=s1.id, run_id=None, filename="one.docx",
                      mime="application/octet-stream", storage_key="k1", size_bytes=1)
    await arts.create(session_id=s2.id, run_id=None, filename="two.docx",
                      mime="application/octet-stream", storage_key="k2", size_bytes=2)
    await db.commit()

    assert [a.filename for a in await arts.list_for_session(s1.id)] == ["one.docx"]
    assert [a.filename for a in await arts.list_for_session(s2.id)] == ["two.docx"]
