"""Word → PDF（R6）。

「Word 同时用无头 LibreOffice 转一份 PDF 供网页预览；Word 供下载。」

验收：「转换失败时降级为『仅提供 Word 下载』，不阻塞整个执行。」
所以这个模块的所有失败路径都返回 None，不抛异常 —— 预览是锦上添花，
不该让一次成功的计算功亏一篑。
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

CONVERT_TIMEOUT_S = 120


def soffice_path() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def writer_available() -> bool:
    """soffice 在不等于能转 .docx。

    只装 libreoffice-core 而没装 libreoffice-writer 时，soffice 二进制存在，
    但没有 Writer 过滤器，转换会以「source file could not be loaded」失败。
    先查一下能给出可操作的日志，而不是一句含糊的转换失败。
    """
    for base in ("/usr/lib/libreoffice/program", "/usr/lib64/libreoffice/program",
                 "/opt/libreoffice/program"):
        d = Path(base)
        if d.is_dir() and any(d.glob("libswlo.*")):
            return True
    return False


async def convert_to_pdf(docx: Path) -> Path | None:
    """转 PDF。失败返回 None（调用方据此降级为仅 Word）。"""
    exe = soffice_path()
    if exe is None:
        logger.info("未找到 LibreOffice，跳过 PDF 预览生成（报告仍可下载 Word）")
        return None
    if not writer_available():
        logger.warning(
            "检测到 soffice 但缺少 Writer 过滤器（libswlo），无法加载 .docx。"
            "请安装 libreoffice-writer；在此之前 PDF 预览会持续降级为仅 Word 下载。"
        )
        return None

    outdir = Path(tempfile.mkdtemp(prefix="bench-pdf-"))
    # 每次转换用独立的 user profile：LibreOffice 并发跑同一个 profile 会互相锁死
    profile = outdir / "profile"
    cmd = [
        exe, "--headless", "--norestore", "--invisible",
        f"-env:UserInstallation=file://{profile}",
        "--convert-to", "pdf", "--outdir", str(outdir), str(docx),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(),
                                               timeout=CONVERT_TIMEOUT_S)
        except TimeoutError:
            proc.kill()
            logger.warning("PDF 转换超时，降级为仅 Word 下载")
            return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("PDF 转换失败，降级为仅 Word 下载：%s", exc)
        return None

    if proc.returncode != 0:
        logger.warning("LibreOffice 返回 %s，降级为仅 Word 下载：%s",
                       proc.returncode, stderr.decode("utf-8", "replace")[:300])
        return None

    pdf = outdir / (docx.stem + ".pdf")
    if not pdf.exists():
        logger.warning("LibreOffice 没有产出 PDF，降级为仅 Word 下载")
        return None
    return pdf
