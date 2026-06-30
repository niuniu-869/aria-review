"""全文摄取 (fulltext.py) 单元测试。

验证：
  - ingest_pdf：mock MinerU → 存盘 + Paper/Attachment 建好 + 元数据抽取
  - 元数据抽取：_extract_metadata_from_markdown, _extract_metadata_from_filename
  - Attachment.markdown_path 持久化
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingest.fulltext import (
    _extract_metadata_from_markdown,
    _extract_metadata_from_filename,
    _merge_metadata,
    ingest_pdf,
)
from app.models import Attachment, Paper


# ---------------------------------------------------------------------------
# 元数据抽取单元测试
# ---------------------------------------------------------------------------

class TestExtractMetadataFromMarkdown:
    def test_extracts_title(self):
        md = "# Effect of X on Y\n\nSome text."
        meta = _extract_metadata_from_markdown(md)
        assert meta["title"] == "Effect of X on Y"

    def test_extracts_abstract(self):
        md = "# Title\n\n## Abstract\n\nThis paper studies X.\n\n# Introduction"
        meta = _extract_metadata_from_markdown(md)
        assert "studies X" in meta["abstract"]

    def test_extracts_abstract_uppercase(self):
        md = "# Title\n\n## ABSTRACT\n\nThis is the abstract text."
        meta = _extract_metadata_from_markdown(md)
        assert "abstract text" in meta["abstract"]

    def test_extracts_authors(self):
        md = "# Title\n\nAuthors: John Smith, Jane Doe\n\n## Abstract\n\nText."
        meta = _extract_metadata_from_markdown(md)
        assert len(meta["creators"]) == 2
        names = [c["literal"] for c in meta["creators"]]
        assert "John Smith" in names
        assert "Jane Doe" in names

    def test_no_heading_no_title(self):
        md = "Just some text without heading"
        meta = _extract_metadata_from_markdown(md)
        assert "title" not in meta

    def test_no_abstract(self):
        md = "# Title\n\nNo abstract section here."
        meta = _extract_metadata_from_markdown(md)
        assert "abstract" not in meta


class TestExtractMetadataFromFilename:
    def test_full_format_author_year_title(self):
        meta = _extract_metadata_from_filename("Smith_2020_IPO Underpricing Study.pdf")
        assert meta["title"] == "IPO Underpricing Study"
        assert meta["year"] == 2020
        assert meta["creators"][0]["literal"] == "Smith"

    def test_two_parts(self):
        meta = _extract_metadata_from_filename("Smith_2021.pdf")
        assert meta["year"] == 2021

    def test_single_part(self):
        meta = _extract_metadata_from_filename("SomeDocument.pdf")
        assert meta["title"] == "SomeDocument"

    def test_invalid_year_not_set(self):
        meta = _extract_metadata_from_filename("Smith_notayear_Title.pdf")
        assert "year" not in meta


class TestMergeMetadata:
    def test_markdown_takes_priority(self):
        from_md = {"title": "MD Title", "abstract": "Abstract from MD"}
        from_fn = {"title": "FN Title", "year": 2020}
        merged = _merge_metadata(from_md, from_fn)
        assert merged["title"] == "MD Title"
        assert merged["year"] == 2020  # from filename
        assert merged["abstract"] == "Abstract from MD"

    def test_fallback_to_filename_title(self):
        merged = _merge_metadata({}, {"title": "FN Title"})
        assert merged["title"] == "FN Title"

    def test_always_has_title(self):
        merged = _merge_metadata({}, {})
        assert merged["title"] == "Unknown Title"


# ---------------------------------------------------------------------------
# ingest_pdf 集成测试（mock MinerU + 真实 DB）
# ---------------------------------------------------------------------------

_SAMPLE_MARKDOWN = """\
# Bibliometric Analysis of IPO Research

Authors: Jane Smith, Bob Lee

## Abstract

This paper presents a comprehensive bibliometric analysis of IPO research
from 2000 to 2020. We identify key themes and influential authors.

## Introduction

Initial Public Offerings (IPOs) represent a critical stage...
"""


def _make_zip_bytes(md_content: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("full.md", md_content)
    return buf.getvalue()


async def _fake_parse_pdfs(paths, language="en", max_files=200, *, _client=None):
    """parse_pdfs 的 mock：返回固定 markdown。"""
    return [
        {
            "name": Path(paths[0]).name,
            "path": str(paths[0]),
            "status": "done",
            "markdown": _SAMPLE_MARKDOWN,
            "err": None,
        }
    ]


@pytest.mark.asyncio
async def test_ingest_pdf_happy(session, tmp_path):
    """ingest_pdf：mock MinerU → 验证存盘 + Paper/Attachment 建好 + 元数据。"""
    # 准备 fake PDF 文件
    pdf = tmp_path / "Smith_2020_IPO Study.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    corpora_dir = tmp_path / "corpora"

    with patch("app.ingest.fulltext.parse_pdfs", side_effect=_fake_parse_pdfs), \
         patch("app.config.settings.corpora_dir", str(corpora_dir)):
        result = await ingest_pdf(pdf, language="en", session=session)

    # 基本返回结构
    assert result["status"] == "done", f"ingest_pdf 失败: {result['err']}"
    assert result["paper_id"] is not None
    assert result["attachment_id"] is not None
    assert result["markdown_len"] > 0
    assert result["err"] is None

    # 存盘验证
    md_path = Path(result["markdown_path"])
    assert md_path.exists(), f"Markdown 文件不存在: {md_path}"
    md_text = md_path.read_text(encoding="utf-8")
    assert "Bibliometric Analysis" in md_text

    # 元数据验证：从 Markdown 抽到了标题
    from sqlalchemy import select
    paper_q = select(Paper).where(Paper.id == result["paper_id"])
    paper = (await session.execute(paper_q)).scalar_one_or_none()
    assert paper is not None
    assert "Bibliometric Analysis" in paper.title or "IPO Study" in paper.title
    assert paper.source == "upload"

    # Attachment 验证
    att_q = select(Attachment).where(Attachment.id == result["attachment_id"])
    att = (await session.execute(att_q)).scalar_one_or_none()
    assert att is not None
    assert att.paper_id == result["paper_id"]
    assert att.mineru_status == "done"
    assert att.content_type == "application/pdf"
    assert att.sha256 is not None and len(att.sha256) == 64
    assert att.markdown_path is not None
    assert "fulltext" in att.markdown_path


@pytest.mark.asyncio
async def test_ingest_pdf_abstract_extracted(session, tmp_path):
    """验证摘要从 Markdown 正确抽取并存入 Paper.abstract。"""
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    corpora_dir = tmp_path / "corpora"

    with patch("app.ingest.fulltext.parse_pdfs", side_effect=_fake_parse_pdfs), \
         patch("app.config.settings.corpora_dir", str(corpora_dir)):
        result = await ingest_pdf(pdf, session=session)

    from sqlalchemy import select
    paper = (await session.execute(
        select(Paper).where(Paper.id == result["paper_id"])
    )).scalar_one_or_none()
    assert paper is not None
    # 摘要应该被抽出
    assert paper.abstract is not None
    assert "bibliometric" in paper.abstract.lower()


@pytest.mark.asyncio
async def test_ingest_pdf_idempotent(session, tmp_path):
    """幂等验证：同一 PDF 再次 ingest → 返回相同 paper_id。"""
    pdf = tmp_path / "dup.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake idempotent")
    corpora_dir = tmp_path / "corpora"

    with patch("app.ingest.fulltext.parse_pdfs", side_effect=_fake_parse_pdfs), \
         patch("app.config.settings.corpora_dir", str(corpora_dir)):
        r1 = await ingest_pdf(pdf, session=session)
        r2 = await ingest_pdf(pdf, session=session)

    # 因为相同标题，add_paper 应幂等返回同一 paper
    assert r1["paper_id"] == r2["paper_id"]


@pytest.mark.asyncio
async def test_ingest_pdf_mineru_failed(session, tmp_path):
    """MinerU 返回 failed → ingest 返回 status=failed，不建 Paper。"""
    pdf = tmp_path / "bad.pdf"
    pdf.write_bytes(b"%PDF fake")

    async def _fake_fail(paths, language="en", max_files=200, *, _client=None):
        return [{
            "name": Path(paths[0]).name,
            "path": str(paths[0]),
            "status": "failed",
            "markdown": None,
            "err": "MinerU 解析出错",
        }]

    with patch("app.ingest.fulltext.parse_pdfs", side_effect=_fake_fail):
        result = await ingest_pdf(pdf, session=session)

    assert result["status"] == "failed"
    assert result["paper_id"] is None
    assert "MinerU" in result["err"] or result["err"] is not None


@pytest.mark.asyncio
async def test_ingest_pdf_file_not_found(session, tmp_path):
    """PDF 不存在 → 抛 FileNotFoundError。"""
    pdf = tmp_path / "nonexistent.pdf"
    with pytest.raises(FileNotFoundError):
        await ingest_pdf(pdf, session=session)
