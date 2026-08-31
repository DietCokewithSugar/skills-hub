"""Bench 沙箱 SDK —— skill 脚本在沙箱内 `import bench` 拿到的就是这个。

它封装 R4 的契约，让 skill 作者不用记住 /meta/params.json 这些路径：

    import bench

    params = bench.params()                  # 读 /meta/params.json
    df = bench.io.read_table(params["raw_data"])
    bench.progress(40, "正在聚合")            # 走 stdout JSONL 协议
    bench.emit_result(result)                # 写 /output/result.json

注意这个包**只**在沙箱里可用，平台侧不 import 它（平台侧的 `bench` 是
另一个包）。两者靠 PYTHONPATH 隔开。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from bench import asserts, io, stats  # noqa: F401  (对外暴露 bench.io / bench.stats / bench.asserts)
from bench.provenance import Metric, source  # noqa: F401

__all__ = ["params", "progress", "log", "emit_result", "workspace", "output",
           "scratch", "io", "stats", "asserts", "Metric", "source", "fail"]


def _path(env: str, default: str) -> Path:
    return Path(os.environ.get(env, default))


def workspace() -> Path:
    """跨 step 保留的工作区。"""
    return _path("BENCH_WORKSPACE", "/workspace")


def output() -> Path:
    """产物目录。执行结束由平台收集上传。"""
    p = _path("BENCH_OUTPUT", "/output")
    p.mkdir(parents=True, exist_ok=True)
    return p


def scratch() -> Path:
    """探索轨的临时目录。这里的文件**不会**被当作产物收集（R9）。"""
    p = workspace() / "scratch"
    p.mkdir(parents=True, exist_ok=True)
    return p


def params() -> dict[str, Any]:
    """本步骤的输入参数与前序卡片答案。"""
    p = _path("BENCH_META", "/meta") / "params.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def progress(pct: float, msg: str = "") -> None:
    """上报进度。

    走 stdout JSONL 协议（R4）。R7：「有进度百分比时显示细进度条，
    无则不显示假进度」—— 所以只在你真的知道进度时才调用它。
    """
    print(json.dumps({"type": "progress", "pct": float(pct), "msg": msg},
                     ensure_ascii=False), flush=True)


def log(msg: str) -> None:
    """写一行日志。走 stderr，实时流到前端的日志抽屉。"""
    print(msg, file=sys.stderr, flush=True)


def emit_result(result: dict[str, Any]) -> None:
    """写 /output/result.json —— 这一步的结构化输出。

    平台会用 step 声明的 schema 校验它，并检查每个指标的 source 标注
    （R0.2 / R0.3）。校验不过这一步就算失败，数据不会往下传。
    """
    path = output() / "result.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
                    encoding="utf-8")
    print(json.dumps({"type": "result", "msg": str(path)}, ensure_ascii=False), flush=True)


def fail(msg: str, *, detail: dict[str, Any] | None = None) -> None:
    """主动中断。

    R0.4：「数据异常时中断执行并报告，而不是照算不误」。
    宁可报错，不可产出一份看起来正常但数字错了的报告。
    """
    payload = {"error": msg, "detail": detail or {}}
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr, flush=True)
    raise SystemExit(2)
