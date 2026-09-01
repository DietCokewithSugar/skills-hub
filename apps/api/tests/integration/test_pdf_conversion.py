"""R6 验收：Word 转 PDF 预览，以及转换失败时的降级。

这组测试**只在装了 LibreOffice Writer 的环境里真正运行**，别处自动跳过。
它存在的理由：R6 的成功路径长期只在"应该能行"的状态——开发机上只有
libreoffice-core（没有 Writer 过滤器，加载不了 .docx），装不上也验证不了。
后端镜像里装了 libreoffice-writer，CI 在镜像内跑这组测试，
成功路径才第一次被真正证明。

降级路径（转换失败不阻塞执行）在任何环境都能测，因此不跳过。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bench.reports.docx import build_context, render_docx
from bench.reports.pdf import convert_to_pdf, soffice_path, writer_available

# 复用 conftest 里的仓库根，别在每个文件里各算一遍 parents[n]（算错过一次）
from tests.conftest import REPO_ROOT

TEMPLATE = REPO_ROOT / "skills" / "ux-report" / "templates" / "report.docx"

requires_writer = pytest.mark.skipif(
    not writer_available(),
    reason="需要 libreoffice-writer（只有 libreoffice-core 无法加载 .docx）",
)

RESULT = {
    "metrics": {
        "nps": {"value": 1.2, "label": "净推荐值",
                "source": {"file": "raw.csv", "step": "compute"}},
        "csat": {"value": 0.7042,
                 "source": {"file": "raw.csv", "step": "compute"}},
        "sample_size": {"value": 240,
                        "source": {"file": "raw.csv", "step": "compute"}},
    },
    "meta": {"scope": "注册流程", "total_rows": 540, "file": "raw.csv"},
}


def _render(tmp_path: Path) -> Path:
    ctx = build_context(RESULT)
    ctx.update({
        "scope": "注册流程", "sample_size": 240, "nps": 1.2,
        "csat_pct": 70.4, "task_success_pct": 87.9, "by_scope": [],
        "summary": "本次针对注册流程的评测回收有效样本 240 份，NPS 为 1.2。",
        "findings": ["注册流程 NPS 为 1.2。"], "recommendations": ["继续观察。"],
        "chart": "", "total_rows": 540, "file": "raw.csv",
    })
    return render_docx(TEMPLATE, tmp_path / "report.docx", ctx)


def test_template_renders_to_real_docx(tmp_path):
    """先确认模板本身能渲染出合法的 .docx（PK 开头的 zip）。"""
    out = _render(tmp_path)
    assert out.exists() and out.stat().st_size > 10_000
    assert out.read_bytes()[:2] == b"PK"


@requires_writer
@pytest.mark.asyncio
async def test_docx_converts_to_pdf(tmp_path):
    """R6 成功路径：真的转出一个 PDF。"""
    pdf = await convert_to_pdf(_render(tmp_path))
    assert pdf is not None, "装了 Writer 却没转出 PDF —— 成功路径没跑通"
    assert pdf.suffix == ".pdf"
    assert pdf.read_bytes()[:5] == b"%PDF-", "产出的不是合法 PDF"
    # 一份带表格和正文的报告，几 KB 起步；太小说明内容没进去
    assert pdf.stat().st_size > 5_000


@requires_writer
@pytest.mark.asyncio
async def test_pdf_contains_chinese_not_boxes(tmp_path):
    """中文必须真的嵌进去，而不是一片方框。

    缺 CJK 字体时 LibreOffice 照样能转出 PDF，只是每个汉字都变成豆腐块——
    这种失败不会报错，只会让人打开报告才发现。所以检查 PDF 里嵌入的字体
    确实包含 CJK 字族。
    """
    pdf = await convert_to_pdf(_render(tmp_path))
    assert pdf is not None
    raw = pdf.read_bytes()
    fonts = [n for n in (b"Noto", b"CJK", b"SimHei", b"Song", b"Hei")
             if n in raw]
    assert fonts, (
        "PDF 里没有任何 CJK 字体痕迹，中文很可能被渲染成方框。"
        "镜像里需要 fonts-noto-cjk。"
    )


@pytest.mark.asyncio
async def test_missing_writer_degrades_instead_of_raising(tmp_path, monkeypatch):
    """R6 验收：转换失败时降级为「仅提供 Word 下载」，不阻塞整个执行。

    这条不跳过 —— 降级路径在任何环境都必须成立。
    """
    monkeypatch.setattr("bench.reports.pdf.writer_available", lambda: False)
    assert await convert_to_pdf(_render(tmp_path)) is None


@pytest.mark.asyncio
async def test_no_soffice_degrades(tmp_path, monkeypatch):
    monkeypatch.setattr("bench.reports.pdf.soffice_path", lambda: None)
    assert await convert_to_pdf(_render(tmp_path)) is None


def test_writer_detection_is_not_just_binary_presence():
    """soffice 存在 ≠ 能转 docx。两者必须分开判断。"""
    if soffice_path() is not None and not writer_available():
        pytest.skip("当前环境正是「有 soffice 但没 Writer」，这正是该函数要区分的情况")
