"""沙箱执行契约（PRD R4 / 6.4）。

平台与脚本之间只通过约定的文件与流交互：

    /workspace          输入文件与中间产物，跨 step 保留
    /meta/params.json   本步骤的输入参数与前序卡片答案
    /output             产物目录，执行结束由平台自动收集上传
    stdout              JSONL 进度协议
    stderr              日志，实时流式推送到前端
    exit code           0 成功，非 0 失败

执行层封装成 Protocol（P2-R15/R16：不与具体供应商 SDK 耦合），
换 gVisor / Kata / 自建 Firecracker 只需新增一个实现。
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from bench.config import get_settings

#: 探索轨的工作目录。写在这里的东西永远不会被当作正式产物收集（R9）。
SCRATCH_DIR = "scratch"

WORKSPACE_MOUNT = "/workspace"
OUTPUT_MOUNT = "/output"
META_MOUNT = "/meta"


class SandboxError(Exception):
    pass


class SandboxTimeout(SandboxError):
    """墙钟超时被强杀（R4 验收：死循环脚本超时后被强制终止）。"""


class UntrustedCodeRefused(SandboxError):
    """在没有硬隔离的 runner 上尝试执行模型生成的代码。

    这不是一个可以「配置绕过」的错误。探索轨执行的是不可信代码，
    只允许跑在内核级隔离的沙箱里（PRD 6.4 已决策）。
    """


@dataclass(slots=True)
class Limits:
    """资源限制。

    PRD 6.4 的硬性配置全在这里。`network` 默认 none —— 「出网是第一
    优先级的配置项。自建沙箱最常见的事故不是内核逃逸，而是代码在不受限的
    联网环境下把密钥或数据传了出去。先关死出网，再按需开白名单。」
    """

    timeout_s: int = 300
    memory_mb: int = 1024
    disk_mb: int = 2048
    cpus: float = 1.0
    pids: int = 128
    #: "none" 或域名白名单
    network: Literal["none"] | list[str] = "none"
    read_only_root: bool = True
    run_as_root: bool = False

    @classmethod
    def platform_defaults(cls) -> Limits:
        s = get_settings()
        return cls(timeout_s=s.sandbox_timeout_s, memory_mb=s.sandbox_memory_mb,
                   disk_mb=s.sandbox_disk_mb, cpus=s.sandbox_cpus, pids=s.sandbox_pids)

    def clamp_to_platform(self) -> Limits:
        """skill 可以在硬上限内下调资源，不可上调（6.4：有硬上限）。"""
        hard = Limits.platform_defaults()
        return Limits(
            timeout_s=min(self.timeout_s, hard.timeout_s),
            memory_mb=min(self.memory_mb, hard.memory_mb),
            disk_mb=min(self.disk_mb, hard.disk_mb),
            cpus=min(self.cpus, hard.cpus),
            pids=min(self.pids, hard.pids),
            network=self.network,
            read_only_root=self.read_only_root,
            run_as_root=False,     # 非 root 不容协商
        )


@dataclass(slots=True)
class ExecRequest:
    """一次沙箱执行的全部输入。"""

    #: 沙箱镜像 / E2B template
    image: str
    #: 平台侧的工作区目录，会被同步进沙箱的 /workspace
    workspace: Path
    #: 相对 /workspace 的入口脚本路径
    entry: str
    #: 落到 /meta/params.json 的内容
    params: dict
    limits: Limits = field(default_factory=Limits)
    #: trusted = 仓库中审核过的 skill 步骤；exploratory = 模型现场生成
    track: Literal["trusted", "exploratory"] = "trusted"
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass(slots=True)
class RunResult:
    exit_code: int
    duration_ms: int
    stdout: str = ""
    stderr: str = ""
    #: 从 stdout JSONL 解析出的进度事件
    progress: list = field(default_factory=list)
    #: 执行后 /output 下的文件（相对路径 -> 本地临时文件）
    outputs: dict[str, Path] = field(default_factory=dict)
    timed_out: bool = False
    #: 沙箱实例标识，用于取消时定位
    sandbox_ref: str | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


#: 日志/进度回调：(line) -> None，用于实时推到前端
StreamCallback = Callable[[str], Awaitable[None] | None]


@runtime_checkable
class SandboxRunner(Protocol):
    """执行层接口。

    P2-R15：将来要支持 pause/resume，实现替换即可，编排器不改。
    P2-R16：私有化部署时换成内网 gVisor 实现，同样不改编排器。
    """

    #: 该实现是否提供内核级隔离。False 的 runner 不允许跑探索轨代码。
    provides_hard_isolation: bool
    #: 该实现是否能保证 network:none。False 时启动自检会告警。
    guarantees_no_egress: bool

    async def run(self, req: ExecRequest, *,
                  on_stdout: StreamCallback | None = None,
                  on_stderr: StreamCallback | None = None,
                  should_cancel: Callable[[], Awaitable[bool]] | None = None,
                  ) -> RunResult: ...

    async def kill(self, sandbox_ref: str) -> None: ...


def get_runner() -> SandboxRunner:
    """按配置返回沙箱实现。"""
    s = get_settings()
    if s.sandbox == "e2b":
        from bench.sandbox.e2b_runner import E2BSandboxRunner
        return E2BSandboxRunner()
    from bench.sandbox.local_runner import LocalSubprocessRunner
    return LocalSubprocessRunner()
