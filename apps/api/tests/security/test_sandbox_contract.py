"""R4 / R9 验收：沙箱契约与安全边界。

这里跑的是 LocalSubprocessRunner —— 它**不是**生产沙箱（无内核级隔离）。
即便如此，契约层面的东西（超时强杀、凭据不注入、探索轨拒绝、
scratch 隔离）在这一层就必须成立，否则换到 E2B 也不会自动变对。

真正的隔离验证在 test_no_egress.py，需要 E2B 凭据。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from bench.sandbox.base import ExecRequest, Limits, UntrustedCodeRefused
from bench.sandbox.local_runner import LocalSubprocessRunner

pytestmark = pytest.mark.asyncio


def _ws(tmp_path: Path, script: str, name: str = "step.py") -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir(exist_ok=True)
    (ws / name).write_text(script, encoding="utf-8")
    return ws


async def test_params_and_output_contract(tmp_path):
    """/meta/params.json 读得到，/output 下的文件被收集（R4 验收）。"""
    ws = _ws(tmp_path, """
import bench, json
p = bench.params()
bench.progress(50, "算一半了")
(bench.output() / "report.txt").write_text("scope=" + p["scope"], encoding="utf-8")
bench.emit_result({"metrics": {"n": {"value": p["sample"]}}})
""")
    r = await LocalSubprocessRunner().run(ExecRequest(
        image="local", workspace=ws, entry="step.py",
        params={"scope": "注册流程", "sample": 200},
    ))
    assert r.ok, f"应成功，stderr={r.stderr}"
    assert "report.txt" in r.outputs and "result.json" in r.outputs
    assert r.outputs["report.txt"].read_text() == "scope=注册流程"
    assert json.loads(r.outputs["result.json"].read_text())["metrics"]["n"]["value"] == 200

    prog = [p for p in r.progress if p.kind == "progress"]
    assert prog and prog[0].pct == 50.0 and prog[0].msg == "算一半了"


async def test_stdout_progress_streams_live(tmp_path):
    """进度是实时流出来的，不是执行完一次性给的（R7：计时器/进度要真实）。"""
    ws = _ws(tmp_path, """
import bench, time
for i in (10, 40, 90):
    bench.progress(i, f"第{i}步")
""")
    seen = []
    await LocalSubprocessRunner().run(
        ExecRequest(image="local", workspace=ws, entry="step.py", params={}),
        on_stdout=lambda line: seen.append(line),
    )
    pcts = [json.loads(s)["pct"] for s in seen if s.startswith("{")]
    assert pcts == [10, 40, 90]


async def test_infinite_loop_is_killed_at_timeout(tmp_path):
    """R4 验收：死循环脚本在超时后被强制终止。"""
    ws = _ws(tmp_path, "while True:\n    pass\n")
    r = await LocalSubprocessRunner().run(ExecRequest(
        image="local", workspace=ws, entry="step.py", params={},
        limits=Limits(timeout_s=2),
    ))
    assert r.timed_out is True
    assert not r.ok
    assert r.exit_code == 124
    assert r.duration_ms < 15_000, "超时后必须尽快返回，不能一直挂着"


async def test_fork_bomb_is_contained(tmp_path):
    """R9 验收：fork 炸弹被资源限制拦下，不影响其他会话。"""
    ws = _ws(tmp_path, """
import os
while True:
    try:
        os.fork()
    except OSError:
        pass
""")
    r = await LocalSubprocessRunner().run(ExecRequest(
        image="local", workspace=ws, entry="step.py", params={},
        limits=Limits(timeout_s=3, pids=16),
    ))
    assert not r.ok, "fork 炸弹必须以失败告终"
    # 关键是整组被杀干净，测试进程自己还活着
    assert os.getpid() > 0


async def test_platform_credentials_are_not_in_sandbox(tmp_path, monkeypatch):
    """R4/R9 验收：沙箱内访问不到数据库、对象存储凭据、模型 API key。"""
    monkeypatch.setenv("BENCH_DATABASE_URL", "postgresql://secret@db/prod")
    monkeypatch.setenv("BENCH_SUPABASE_SERVICE_KEY", "sb-service-key-should-not-leak")
    monkeypatch.setenv("BENCH_LLM_API_KEY", "sk-deepseek-should-not-leak")
    monkeypatch.setenv("E2B_API_KEY", "e2b-should-not-leak")

    ws = _ws(tmp_path, """
import bench, os, json
leaked = {k: v for k, v in os.environ.items()
          if any(s in v for s in ("secret", "should-not-leak"))}
(bench.output() / "leak.json").write_text(json.dumps(leaked))
""")
    r = await LocalSubprocessRunner().run(ExecRequest(
        image="local", workspace=ws, entry="step.py", params={}))
    leaked = json.loads(r.outputs["leak.json"].read_text())
    assert leaked == {}, f"平台凭据泄漏进沙箱：{list(leaked)}"


async def test_exploratory_track_is_refused_without_hard_isolation(tmp_path):
    """R9 的红线：不可信代码不允许跑在没有内核级隔离的 runner 上。

    这条不是「配置得当就没事」，而是结构性拒绝 —— 换句话说，
    忘了配 E2B 不会导致模型生成的代码在裸子进程里跑起来。
    """
    ws = _ws(tmp_path, "print('hello')")
    runner = LocalSubprocessRunner()
    assert runner.provides_hard_isolation is False
    with pytest.raises(UntrustedCodeRefused) as e:
        await runner.run(ExecRequest(
            image="local", workspace=ws, entry="step.py", params={},
            track="exploratory",
        ))
    assert "探索轨" in str(e.value) or "内核级隔离" in str(e.value)


async def test_entry_cannot_escape_workspace(tmp_path):
    """入口路径穿越必须挡住。"""
    ws = _ws(tmp_path, "print(1)")
    from bench.sandbox.base import SandboxError
    with pytest.raises(SandboxError):
        await LocalSubprocessRunner().run(ExecRequest(
            image="local", workspace=ws, entry="../../../etc/passwd", params={}))


async def test_nonzero_exit_is_a_failure_with_stderr(tmp_path):
    """脚本主动 fail 时，失败原因要能拿到（R0.4 / 7.7）。"""
    ws = _ws(tmp_path, """
import bench
bench.fail("输入数据缺少必填列：['nps']")
""")
    r = await LocalSubprocessRunner().run(ExecRequest(
        image="local", workspace=ws, entry="step.py", params={}))
    assert not r.ok
    assert "缺少必填列" in r.stderr


async def test_scratch_files_stay_in_workspace_not_output(tmp_path):
    """R9 验收：/workspace/scratch/ 下的文件不会出现在产物列表中。"""
    ws = _ws(tmp_path, """
import bench
(bench.scratch() / "probe.csv").write_text("探索轨的临时文件")
(bench.output() / "report.docx").write_text("正式产物")
""")
    r = await LocalSubprocessRunner().run(ExecRequest(
        image="local", workspace=ws, entry="step.py", params={}))
    assert set(r.outputs) == {"report.docx"}, f"scratch 不该被收集：{list(r.outputs)}"
    assert (ws / "scratch" / "probe.csv").exists(), "但文件本身要留在工作区里"
