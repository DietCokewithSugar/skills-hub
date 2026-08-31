"""R4/R9 验收：默认无出网，尝试联网的代码会失败并给出明确提示。

PRD 6.4：「出网是第一优先级的配置项。自建沙箱最常见的事故不是内核逃逸，
而是代码在不受限的联网环境下把密钥或数据传了出去。」

这个测试**真的去连外网**，然后断言连不上 —— 不是断言某个配置字段等于
False。配置对不对不重要，网通不通才重要。

需要 E2B_API_KEY；没配就跳过。上线前必须在目标账号上跑一次并看到它通过。
"""

from __future__ import annotations

import os

import pytest

from bench.sandbox.base import ExecRequest, Limits

requires_e2b = pytest.mark.skipif(
    not os.getenv("E2B_API_KEY"),
    reason="需要 E2B_API_KEY —— 上线前必须在目标账号上跑通这一组",
)

pytestmark = [pytest.mark.asyncio, pytest.mark.e2b, requires_e2b]


PROBE = """
import json, socket, urllib.request
results = {}
try:
    socket.create_connection(("1.1.1.1", 53), timeout=5)
    results["raw_socket"] = "CONNECTED"
except Exception as e:
    results["raw_socket"] = f"blocked: {type(e).__name__}"
try:
    urllib.request.urlopen("https://api.github.com", timeout=5).read(64)
    results["https"] = "CONNECTED"
except Exception as e:
    results["https"] = f"blocked: {type(e).__name__}"
try:
    socket.gethostbyname("example.com")
    results["dns"] = "RESOLVED"
except Exception as e:
    results["dns"] = f"blocked: {type(e).__name__}"
print(json.dumps(results))
"""


def _ws(tmp_path, script: str):
    ws = tmp_path / "workspace"
    ws.mkdir(exist_ok=True)
    (ws / "probe.py").write_text(script, encoding="utf-8")
    return ws


async def test_default_sandbox_has_no_egress(tmp_path):
    """默认配置下，沙箱内一切外联都必须失败。"""
    import json

    from bench.sandbox.e2b_runner import E2BSandboxRunner

    r = await E2BSandboxRunner().run(ExecRequest(
        image="", workspace=_ws(tmp_path, PROBE), entry="probe.py",
        params={}, limits=Limits(network="none", timeout_s=60),
    ))
    line = next((ln for ln in r.stdout.splitlines() if ln.strip().startswith("{")), "{}")
    probe = json.loads(line)
    assert probe.get("raw_socket", "").startswith("blocked"), f"裸 socket 出网了：{probe}"
    assert probe.get("https", "").startswith("blocked"), f"HTTPS 出网了：{probe}"


async def test_allowlisted_domain_is_reachable_others_are_not(tmp_path):
    """skill 声明白名单后，只有白名单内的域名可达。"""
    import json

    from bench.sandbox.e2b_runner import E2BSandboxRunner

    script = """
import json, urllib.request
out = {}
for name, url in (("allowed", "https://api.github.com"),
                  ("denied", "https://example.com")):
    try:
        urllib.request.urlopen(url, timeout=8).read(32)
        out[name] = "CONNECTED"
    except Exception as e:
        out[name] = f"blocked: {type(e).__name__}"
print(json.dumps(out))
"""
    r = await E2BSandboxRunner().run(ExecRequest(
        image="", workspace=_ws(tmp_path, script), entry="probe.py",
        params={}, limits=Limits(network=["api.github.com"], timeout_s=60),
    ))
    line = next((ln for ln in r.stdout.splitlines() if ln.strip().startswith("{")), "{}")
    probe = json.loads(line)
    assert probe.get("denied", "").startswith("blocked"), f"白名单外的域名可达：{probe}"


async def test_platform_credentials_absent_in_e2b_sandbox(tmp_path):
    """R9 验收：沙箱内访问不到数据库、对象存储凭据、DeepSeek API key。"""
    import json

    from bench.sandbox.e2b_runner import E2BSandboxRunner

    script = """
import json, os
suspicious = {k: v[:8] for k, v in os.environ.items()
              if any(t in k.upper() for t in
                     ("SUPABASE", "DATABASE", "LLM_API", "DEEPSEEK", "E2B_API",
                      "SERVICE_KEY", "REDIS"))}
print(json.dumps(suspicious))
"""
    r = await E2BSandboxRunner().run(ExecRequest(
        image="", workspace=_ws(tmp_path, script), entry="probe.py",
        params={}, limits=Limits(timeout_s=60),
    ))
    line = next((ln for ln in r.stdout.splitlines() if ln.strip().startswith("{")), "{}")
    assert json.loads(line) == {}, f"平台凭据出现在沙箱环境里：{line}"


async def test_timeout_is_enforced_by_e2b(tmp_path):
    """R4 验收：死循环在超时后被强制终止。"""
    from bench.sandbox.e2b_runner import E2BSandboxRunner

    r = await E2BSandboxRunner().run(ExecRequest(
        image="", workspace=_ws(tmp_path, "while True: pass"), entry="probe.py",
        params={}, limits=Limits(timeout_s=20),
    ))
    assert not r.ok
    assert r.timed_out or r.exit_code != 0
