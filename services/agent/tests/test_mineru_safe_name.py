"""P3-3 Step 1 — MinerU `_safe_name` 多格式扩展名保留测试。

MinerU 2.5+ 支持 Word/PPT/HTML（§0.6 多格式同管线）。`_safe_name` 在文件名超长
（>120 字符）时截断 stem，但**必须保留原扩展名**——MinerU 按扩展名判文件类型，
旧实现把所有截断名强制改成 `.pdf`，会让 .docx/.pptx/.html 报「unsupported file type」
（且批量提交原子，一个坏名整批全失败）。

校验：
  - 短名（≤120）原样返回，不改。
  - 长 .pdf 截断后仍以 .pdf 结尾，且总长 ≤120。
  - 长 .docx / .pptx / .html 截断后保留各自扩展名（先红：旧实现对 .docx 返回 .pdf）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ingest.mineru import _safe_name


def test_short_name_unchanged():
    """短文件名（≤120 字符）原样返回。"""
    p = Path("/tmp/Smith_2021_analyst_forecast.pdf")
    assert _safe_name(p) == "Smith_2021_analyst_forecast.pdf"

    p2 = Path("/data/某中文论文_2020.docx")
    assert _safe_name(p2) == "某中文论文_2020.docx"


def test_long_pdf_truncated_keeps_pdf():
    """超长 .pdf 名截断后仍以 .pdf 结尾，且总长 ≤120。"""
    long_stem = "A" * 200
    p = Path(f"/tmp/{long_stem}.pdf")
    name = _safe_name(p)
    assert name.endswith(".pdf"), f"截断后应保留 .pdf，实得 {name!r}"
    assert len(name) <= 120, f"截断后总长应 ≤120，实得 {len(name)}"


def test_long_docx_truncated_keeps_docx():
    """超长 .docx 名截断后必须保留 .docx（旧实现错误返回 .pdf → 先红）。"""
    long_stem = "B" * 200
    p = Path(f"/tmp/{long_stem}.docx")
    name = _safe_name(p)
    assert name.endswith(".docx"), f"截断后应保留 .docx，实得 {name!r}"
    assert len(name) <= 120, f"截断后总长应 ≤120，实得 {len(name)}"


def test_long_pptx_truncated_keeps_pptx():
    """超长 .pptx 名截断后保留 .pptx。"""
    long_stem = "C" * 200
    p = Path(f"/tmp/{long_stem}.pptx")
    name = _safe_name(p)
    assert name.endswith(".pptx"), f"截断后应保留 .pptx，实得 {name!r}"
    assert len(name) <= 120


def test_long_html_truncated_keeps_html():
    """超长 .html 名截断后保留 .html。"""
    long_stem = "D" * 200
    p = Path(f"/tmp/{long_stem}.html")
    name = _safe_name(p)
    assert name.endswith(".html"), f"截断后应保留 .html，实得 {name!r}"
    assert len(name) <= 120


def test_extremely_long_suffix_result_bounded():
    """极长 suffix（>120）兜底：keep 会为负，结果仍须 ≤120（整名截断保稳健）。

    罕见但要稳健：suffix 本身超 120 时 `keep = 120 - len(suffix)` 为负，
    旧实现 `stem[:负数] + suffix` 仍含完整 suffix，返回值必超上限。
    兜底退化为对整名截断到 120，保证返回长度始终 ≤120（不崩、不超限）。
    """
    long_suffix = "." + "x" * 200  # suffix 含点共 201 字符，远超 120
    p = Path(f"/tmp/doc{long_suffix}")
    name = _safe_name(p)
    assert len(name) <= 120, f"极长 suffix 时结果仍须 ≤120，实得 {len(name)}"
