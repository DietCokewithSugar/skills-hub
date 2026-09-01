"""E2B 沙箱 runner —— v1 的默认实现（Firecracker microVM，内核级硬隔离）。

PRD 6.4 决策：平台允许模型现场生成 Python 并执行，这是不可信代码；
普通 Docker 共享宿主内核，风险不可接受。E2B 每个沙箱有独立 guest 内核。

**关于出网**：规划阶段我们以为「默认无出网」只能在 E2B template 侧配置，
实际 SDK 直接支持：

    allow_internet_access=False          完全无出网（默认走这条）
    network={"allow_out": [域名...]}     按 skill 声明的白名单逐域名放行

因此 6.4 那句「network: none，按 skill 白名单逐域名开」是逐字落地的，
不依赖运维在控制台记得勾某个选项 —— 代码里默认值就是关死的，
要开必须在 skill.yaml 里显式写域名。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from bench.config import get_settings
from bench.sandbox.base import (
    META_MOUNT,
    OUTPUT_MOUNT,
    WORKSPACE_MOUNT,
    ExecRequest,
    RunResult,
    SandboxError,
    StreamCallback,
)
from bench.sandbox.protocol import parse_stdout_line

logger = logging.getLogger(__name__)

#: 取消标志的轮询间隔。R8 要求取消后沙箱 5 秒内销毁。
CANCEL_POLL_SECONDS = 1.0
#: 单个文件上/下行大小上限，防一个巨大中间文件把内存吃穿
MAX_FILE_BYTES = 512 * 1024 * 1024


class E2BSandboxRunner:
    provides_hard_isolation = True
    guarantees_no_egress = True

    def __init__(self, *, template: str | None = None,
                 api_key: str | None = None) -> None:
        s = get_settings()
        self.template = template or s.e2b_template
        self.api_key = api_key or s.e2b_api_key
        self._sandboxes: dict[str, object] = {}

    # ── 出网策略 ────────────────────────────────────────────────
    @staticmethod
    def _network_kwargs(limits) -> dict:
        """把 Limits.network 翻译成 E2B 的创建参数。

        默认关死。只有 skill 在 skill.yaml 里显式声明了
        limits.network_allowlist，才会放行那几个域名。
        """
        if limits.network == "none" or not limits.network:
            return {"allow_internet_access": False}
        domains = list(limits.network)
        return {
            "allow_internet_access": True,
            "network": {"allow_out": domains, "deny_out": ["0.0.0.0/0"]},
        }

    async def run(self, req: ExecRequest, *,
                  on_stdout: StreamCallback | None = None,
                  on_stderr: StreamCallback | None = None,
                  should_cancel: Callable[[], Awaitable[bool]] | None = None,
                  ) -> RunResult:
        try:
            from e2b import AsyncSandbox
        except ImportError as exc:  # pragma: no cover
            raise SandboxError("未安装 e2b SDK") from exc

        if not self.api_key:
            raise SandboxError(
                "E2B_API_KEY 未配置。BENCH_SANDBOX=e2b 需要凭据；"
                "本地跑回归测试可设 BENCH_SANDBOX=local（无隔离，仅限可信 skill）。"
            )

        limits = req.limits.clamp_to_platform()
        started = time.monotonic()
        stdout_buf: list[str] = []
        stderr_buf: list[str] = []
        progress: list = []
        sandbox = None
        timed_out = False
        cancelled = False

        try:
            sandbox = await AsyncSandbox.create(
                template=self.template,
                api_key=self.api_key,
                # 墙钟超时留一点余量给上下行文件，真正的强杀由下面的 wait_for 控制
                timeout=limits.timeout_s + 60,
                metadata={"bench_run_id": req.run_id, "bench_track": req.track},
                # PRD 6.4：沙箱内不注入任何平台凭据。这里给的全是契约路径，
                # 没有一个是密钥。产物由平台侧收集上传，沙箱不需要 Storage 凭据。
                envs={
                    "BENCH_WORKSPACE": WORKSPACE_MOUNT,
                    "BENCH_OUTPUT": OUTPUT_MOUNT,
                    "BENCH_META": META_MOUNT,
                    "PYTHONUNBUFFERED": "1",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "MPLBACKEND": "Agg",
                },
                **self._network_kwargs(limits),
            )
            self._sandboxes[req.run_id] = sandbox
            sandbox_ref = sandbox.sandbox_id

            await self._upload_workspace(sandbox, req)

            async def _stdout(line: str) -> None:
                stdout_buf.append(line + "\n")
                ev = parse_stdout_line(line)
                if ev.kind != "log":
                    progress.append(ev)
                if on_stdout:
                    r = on_stdout(line)
                    if asyncio.iscoroutine(r):
                        await r

            async def _stderr(line: str) -> None:
                stderr_buf.append(line + "\n")
                if on_stderr:
                    r = on_stderr(line)
                    if asyncio.iscoroutine(r):
                        await r

            cmd = f"cd {WORKSPACE_MOUNT} && python -u {_sh_quote(req.entry)}"
            exec_task = asyncio.create_task(sandbox.commands.run(
                cmd, timeout=limits.timeout_s,
                on_stdout=_stdout, on_stderr=_stderr,
            ))

            exit_code = 0
            try:
                if should_cancel is None:
                    result = await asyncio.wait_for(exec_task, timeout=limits.timeout_s + 15)
                    exit_code = getattr(result, "exit_code", 0) or 0
                else:
                    result = await self._wait_with_cancel(exec_task, should_cancel, limits)
                    if result is None:
                        cancelled = True
                        exit_code = 125
                    else:
                        exit_code = getattr(result, "exit_code", 0) or 0
            except TimeoutError:
                timed_out = True
                exit_code = 124
                exec_task.cancel()
            except Exception as exc:  # noqa: BLE001
                # commands.run 在非零退出时会抛 CommandExitException，
                # 那是「脚本失败」而不是「平台故障」，要区分开。
                code = getattr(exc, "exit_code", None)
                if code is None:
                    raise
                exit_code = int(code)
                stderr_buf.append(str(getattr(exc, "stderr", "") or ""))

            outputs: dict[str, Path] = {}
            if not cancelled:
                # 超时也要尽力回收 —— 半成品产物有时正是排查的关键
                with contextlib.suppress(Exception):
                    outputs = await self._download_outputs(sandbox, req)
                with contextlib.suppress(Exception):
                    await self._download_workspace(sandbox, req)

            return RunResult(
                exit_code=exit_code,
                duration_ms=int((time.monotonic() - started) * 1000),
                stdout="".join(stdout_buf), stderr="".join(stderr_buf),
                progress=progress, outputs=outputs, timed_out=timed_out,
                sandbox_ref=sandbox_ref,
            )
        finally:
            # R1：一个 Run 对应一个沙箱实例，执行结束即销毁。
            self._sandboxes.pop(req.run_id, None)
            if sandbox is not None:
                with contextlib.suppress(Exception):
                    await sandbox.kill()

    async def _wait_with_cancel(self, task: asyncio.Task, should_cancel, limits):
        """边等执行边看取消标志（R8：5 秒内销毁）。"""
        deadline = time.monotonic() + limits.timeout_s + 15
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError
            try:
                return await asyncio.wait_for(
                    asyncio.shield(task), timeout=CANCEL_POLL_SECONDS)
            except TimeoutError:
                if await should_cancel():
                    task.cancel()
                    with contextlib.suppress(Exception, asyncio.CancelledError):
                        await task
                    return None

    async def _upload_workspace(self, sandbox, req: ExecRequest) -> None:
        """把平台侧工作区同步进 /workspace，并写 /meta/params.json。"""
        ws = req.workspace.resolve()
        for p in sorted(ws.rglob("*")):
            if not p.is_file():
                continue
            if p.stat().st_size > MAX_FILE_BYTES:
                raise SandboxError(f"文件过大无法上传沙箱：{p.name}")
            rel = p.relative_to(ws).as_posix()
            await sandbox.files.write(f"{WORKSPACE_MOUNT}/{rel}", p.read_bytes())

        await sandbox.files.write(
            f"{META_MOUNT}/params.json",
            json.dumps(req.params, ensure_ascii=False, indent=2),
        )
        await sandbox.commands.run(f"mkdir -p {OUTPUT_MOUNT}", timeout=30)

    async def _download_outputs(self, sandbox, req: ExecRequest) -> dict[str, Path]:
        """收集 /output。

        R6 验收：/output 下的文件在执行结束后 100% 出现在消息流中。
        注意这里**只**看 /output —— 探索轨写的 /workspace/scratch/ 不在
        收集范围内，这是 R9 的目录级硬排除。
        """
        dest = Path(tempfile.mkdtemp(prefix=f"bench-out-{req.run_id[:8]}-"))
        out: dict[str, Path] = {}
        for entry in await _walk(sandbox, OUTPUT_MOUNT):
            rel = entry[len(OUTPUT_MOUNT) + 1:]
            if not rel:
                continue
            data = await sandbox.files.read(entry, format="bytes")
            local = dest / rel
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(data if isinstance(data, bytes) else bytes(data))
            out[rel] = local
        return out

    async def _download_workspace(self, sandbox, req: ExecRequest) -> None:
        """把 /workspace 同步回平台侧 —— R4：工作区跨 step 保留。"""
        ws = req.workspace.resolve()
        for entry in await _walk(sandbox, WORKSPACE_MOUNT):
            rel = entry[len(WORKSPACE_MOUNT) + 1:]
            if not rel:
                continue
            data = await sandbox.files.read(entry, format="bytes")
            local = ws / rel
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(data if isinstance(data, bytes) else bytes(data))

    async def kill(self, sandbox_ref: str) -> None:
        sandbox = self._sandboxes.get(sandbox_ref)
        if sandbox is not None:
            with contextlib.suppress(Exception):
                await sandbox.kill()
            self._sandboxes.pop(sandbox_ref, None)
            return
        # 沙箱可能由另一个 worker 进程创建（取消请求落到别的实例上）
        with contextlib.suppress(Exception):
            from e2b import AsyncSandbox
            await AsyncSandbox.kill(sandbox_ref, api_key=self.api_key)


async def _walk(sandbox, root: str, depth: int = 12) -> list[str]:
    """列出 root 下所有文件的绝对路径。"""
    try:
        entries = await sandbox.files.list(root, depth=depth)
    except Exception:  # noqa: BLE001  目录不存在就是没有产物
        return []
    files = []
    for e in entries:
        is_file = getattr(e, "type", None)
        name = getattr(e, "path", None) or getattr(e, "name", "")
        if is_file is not None and str(is_file).lower().endswith("file"):
            files.append(name if name.startswith("/") else f"{root}/{name}")
        elif is_file is None and name:
            files.append(name)
    return files


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def cleanup_temp_outputs(result: RunResult) -> None:
    """产物入库后清理临时目录。"""
    dirs = {p.parent for p in result.outputs.values()}
    for d in dirs:
        with contextlib.suppress(Exception):
            shutil.rmtree(d, ignore_errors=True)
