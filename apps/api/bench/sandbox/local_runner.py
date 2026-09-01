"""本地子进程 runner —— **仅供 fixtures 回归与 CI**。

没有内核级隔离。它存在的唯一理由是：让 R0.5 的回归测试集和 CI 能在
没有 E2B 凭据的环境里跑起来。

三条硬约束，不可配置绕过：

  1. `provides_hard_isolation = False`，因此 `track="exploratory"` 的请求
     直接抛 UntrustedCodeRefused。模型生成的代码只允许跑在硬隔离沙箱里
     （PRD 6.4 已决策）。
  2. 环境变量白名单化 —— 平台凭据（DB 连接串、Storage key、模型 API key）
     不进子进程（PRD 6.4：沙箱内不注入任何平台凭据）。
  3. 尽力关掉出网：设置指向黑洞的代理变量。这**不是**安全边界，
     只是让「本地跑通了，上 E2B 就断网」这类问题早点暴露。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import resource
import shutil
import signal
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from bench.sandbox.base import (
    ExecRequest,
    RunResult,
    SandboxError,
    StreamCallback,
    UntrustedCodeRefused,
)
from bench.sandbox.protocol import parse_stdout_line

logger = logging.getLogger(__name__)

#: 沙箱 SDK 所在目录（子进程 import bench 解析到这里）
SDK_ROOT = Path(__file__).resolve().parents[3].parent / "sandbox" / "runtime"

#: 允许透传给子进程的环境变量。其余一律不给。
_ENV_ALLOWLIST = ("PATH", "LANG", "LC_ALL", "TZ", "HOME", "TMPDIR")


def _child_env(workspace: Path) -> dict[str, str]:
    env = {k: os.environ[k] for k in _ENV_ALLOWLIST if k in os.environ}
    env.update({
        "HOME": str(workspace),
        "PYTHONUNBUFFERED": "1",         # 不缓冲，进度才是实时的
        "PYTHONDONTWRITEBYTECODE": "1",
        "MPLBACKEND": "Agg",             # matplotlib 无显示环境
        # 指向黑洞的代理：让联网尝试快速失败而不是挂住
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "NO_PROXY": "",
    })
    return env


def _preexec(limits) -> Callable[[], None]:
    """子进程自限：内存、进程数、文件大小，并独立成进程组便于整组杀。"""
    mem = limits.memory_mb * 1024 * 1024
    nproc = limits.pids
    fsize = limits.disk_mb * 1024 * 1024

    def apply() -> None:
        os.setsid()
        with contextlib.suppress(Exception):
            resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        with contextlib.suppress(Exception):
            resource.setrlimit(resource.RLIMIT_NPROC, (nproc, nproc))
        with contextlib.suppress(Exception):
            resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
        with contextlib.suppress(Exception):
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return apply


class LocalSubprocessRunner:
    provides_hard_isolation = False
    guarantees_no_egress = False

    def __init__(self) -> None:
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        logger.warning(
            "使用 LocalSubprocessRunner：无内核级隔离，仅可用于 fixtures 回归与 CI。"
        )

    async def run(self, req: ExecRequest, *,
                  on_stdout: StreamCallback | None = None,
                  on_stderr: StreamCallback | None = None,
                  should_cancel: Callable[[], Awaitable[bool]] | None = None,
                  ) -> RunResult:
        if req.track == "exploratory":
            raise UntrustedCodeRefused(
                "拒绝在本地子进程中执行模型生成的代码。探索轨要求内核级隔离沙箱"
                "（PRD 6.4）。请配置 BENCH_SANDBOX=e2b 与 E2B_API_KEY。"
            )

        limits = req.limits.clamp_to_platform()
        workspace = req.workspace.resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        output_dir = workspace / "_output"
        output_dir.mkdir(exist_ok=True)
        meta_dir = Path(tempfile.mkdtemp(prefix="bench-meta-"))
        (meta_dir / "params.json").write_text(
            json.dumps(req.params, ensure_ascii=False, indent=2), encoding="utf-8")

        entry_path = (workspace / req.entry).resolve()
        if not entry_path.is_relative_to(workspace):
            raise SandboxError(f"入口脚本越出工作区：{req.entry}")
        if not entry_path.exists():
            raise SandboxError(f"入口脚本不存在：{req.entry}")

        env = _child_env(workspace)
        # 本地 runner 没有真实挂载点，用环境变量告诉脚本约定路径在哪。
        # bench_sdk 优先读这些变量，因此同一份脚本在两种 runner 下都能跑。
        env.update({
            "BENCH_WORKSPACE": str(workspace),
            "BENCH_OUTPUT": str(output_dir),
            "BENCH_META": str(meta_dir),
        })
        # 只把沙箱 SDK 放进 PYTHONPATH。平台侧也有一个叫 bench 的包，
        # 两者靠这里隔开：子进程 import bench 拿到的是 sandbox/runtime/bench。
        env["PYTHONPATH"] = str(SDK_ROOT)

        started = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-u", str(entry_path),
            cwd=str(workspace), env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            preexec_fn=_preexec(limits),
        )
        self._procs[req.run_id] = proc

        stdout_buf: list[str] = []
        stderr_buf: list[str] = []
        progress: list = []

        async def read(stream, buf, cb, is_stdout):
            async for raw in stream:
                line = raw.decode("utf-8", errors="replace")
                buf.append(line)
                if is_stdout:
                    ev = parse_stdout_line(line)
                    if ev.kind != "log":
                        progress.append(ev)
                if cb:
                    r = cb(line.rstrip("\n"))
                    if asyncio.iscoroutine(r):
                        await r

        readers = asyncio.gather(
            read(proc.stdout, stdout_buf, on_stdout, True),
            read(proc.stderr, stderr_buf, on_stderr, False),
        )

        timed_out = False
        cancelled = False
        try:
            async with asyncio.timeout(limits.timeout_s):
                if should_cancel is None:
                    await proc.wait()
                else:
                    # R8：取消后沙箱要在 5 秒内销毁，所以每秒查一次标志
                    while True:
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=1.0)
                            break
                        except TimeoutError:
                            if await should_cancel():
                                cancelled = True
                                await self.kill(req.run_id)
                                break
        except TimeoutError:
            timed_out = True
            await self.kill(req.run_id)
        finally:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(readers, timeout=5)
            self._procs.pop(req.run_id, None)

        outputs = {
            str(p.relative_to(output_dir)): p
            for p in sorted(output_dir.rglob("*")) if p.is_file()
        }
        shutil.rmtree(meta_dir, ignore_errors=True)

        exit_code = proc.returncode if proc.returncode is not None else -1
        if timed_out:
            exit_code = 124
        if cancelled:
            exit_code = 125

        return RunResult(
            exit_code=exit_code,
            duration_ms=int((time.monotonic() - started) * 1000),
            stdout="".join(stdout_buf), stderr="".join(stderr_buf),
            progress=progress, outputs=outputs, timed_out=timed_out,
            sandbox_ref=req.run_id,
        )

    async def kill(self, sandbox_ref: str) -> None:
        proc = self._procs.get(sandbox_ref)
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)   # 整组杀，防 fork 逃逸
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=5)
