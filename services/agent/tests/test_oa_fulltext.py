"""M4 溯源贯通测试：OA PDF → 安全下载 → MinerU → 挂既有 Paper 的页/块溯源。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.ingest import oa_fulltext as oa_mod
from app.ingest.oa_fulltext import fetch_and_store_oa_fulltext, resolve_oa_pdf_url
from app.ingest.pdf_download import PdfResolveResult
from app.models import Attachment, DocumentStructure
from app.repositories.library import add_paper
from app.sources.unpaywall import UnpaywallHit

_MD = "# OA Paper\n\n## Abstract\n\nThis open access paper demonstrates provenance.\n\n## Body\n\nContent here.\n"
_CONTENT_LIST = [
    {"type": "text", "text": "OA Paper", "page_idx": 0, "bbox": [0, 0, 100, 10]},
    {"type": "text", "text": "This open access paper demonstrates provenance.", "page_idx": 0,
     "bbox": [0, 20, 100, 30]},
    {"type": "text", "text": "Content here.", "page_idx": 1, "bbox": [0, 0, 100, 10]},
]


async def _fake_parse_pdfs(paths, language="en", max_files=200, *, _client=None):
    return [{"name": Path(paths[0]).name, "path": str(paths[0]), "status": "done",
             "markdown": _MD, "content_list": _CONTENT_LIST, "err": None}]


def _ok_asset(path: str) -> PdfResolveResult:
    return PdfResolveResult(ok=True, path=path, sha256="deadbeef" * 8, size=1234,
                            source_url="https://oa/x.pdf", content_type="application/pdf")


# --------------------------------------------------------------------------
# resolve_oa_pdf_url：候选 pdfUrl 优先 / Unpaywall 懒加载 / 无 DOI
# --------------------------------------------------------------------------

async def test_resolve_url_prefers_candidate_pdf():
    url, src = await resolve_oa_pdf_url("https://oa/direct.pdf", "10.1/x")
    assert url == "https://oa/direct.pdf" and src == "candidate"


async def test_resolve_url_unpaywall_lazy_when_no_pdf(monkeypatch):
    async def fake_lookup(self, doi):
        return UnpaywallHit(doi=doi, pdf_url="https://oa/uw.pdf", landing_url=None, oa_status="green")

    monkeypatch.setattr("app.sources.unpaywall.UnpaywallClient.lookup", fake_lookup)
    url, src = await resolve_oa_pdf_url(None, "10.1/x")
    assert url == "https://oa/uw.pdf" and src == "unpaywall"


async def test_resolve_url_none_without_doi_or_pdf():
    url, src = await resolve_oa_pdf_url(None, None)
    assert url is None and "无 pdfUrl 且无 DOI" in src


async def test_resolve_url_unpaywall_miss(monkeypatch):
    async def fake_lookup(self, doi):
        return None

    monkeypatch.setattr("app.sources.unpaywall.UnpaywallClient.lookup", fake_lookup)
    url, src = await resolve_oa_pdf_url(None, "10.1/x")
    assert url is None and "未命中" in src


# --------------------------------------------------------------------------
# fetch_and_store_oa_fulltext：挂到既有 paper + 建 DocumentStructure（溯源贯通）
# --------------------------------------------------------------------------

async def test_fetch_stores_fulltext_and_provenance_on_existing_paper(session, tmp_path):
    paper = await add_paper(session, {"title": "Existing Paper", "doi": "10.5/oa", "source": "openalex"})
    pdf_path = tmp_path / "x.pdf"
    pdf_path.write_bytes(b"%PDF-1.7 fake")

    async def fake_resolve(url, **kw):
        return _ok_asset(str(pdf_path))

    with patch.object(oa_mod, "resolve_pdf", side_effect=fake_resolve), \
         patch.object(oa_mod, "parse_pdfs", side_effect=_fake_parse_pdfs):
        res = await fetch_and_store_oa_fulltext(
            paper.id, pdf_url="https://oa/x.pdf", session=session)

    assert res["status"] == "done" and res["paper_id"] == paper.id
    # Attachment 挂到既有 paper
    att = (await session.execute(
        select(Attachment).where(Attachment.paper_id == paper.id))).scalar_one()
    assert att.mineru_status == "done" and att.markdown_path
    # DocumentStructure 页/块溯源结构建好（cite_check 可用）
    ds = (await session.execute(
        select(DocumentStructure).where(DocumentStructure.attachment_id == att.id))).scalar_one()
    assert ds.page_count >= 1 and ds.page_map is not None and ds.content_list


async def test_fetch_rejects_unsafe_pdf(session):
    paper = await add_paper(session, {"title": "P", "doi": "10.5/r", "source": "core"})

    async def fake_resolve(url, **kw):
        return PdfResolveResult.reject("URL 不安全: 内网", url)

    with patch.object(oa_mod, "resolve_pdf", side_effect=fake_resolve):
        res = await fetch_and_store_oa_fulltext(paper.id, pdf_url="http://10.0.0.1/x.pdf", session=session)
    assert res["status"] == "rejected" and "不安全" in res["reason"]
    # 未建任何附件
    atts = (await session.execute(select(Attachment).where(Attachment.paper_id == paper.id))).scalars().all()
    assert atts == []


async def test_fetch_skips_when_no_oa_source(session):
    paper = await add_paper(session, {"title": "P2", "source": "crossref"})  # 无 DOI 无 pdfUrl
    res = await fetch_and_store_oa_fulltext(paper.id, pdf_url=None, doi=None, session=session)
    assert res["status"] == "skipped"


async def test_fetch_idempotent_reuses_existing_attachment(session, tmp_path):
    paper = await add_paper(session, {"title": "Reuse", "doi": "10.5/re", "source": "openalex"})
    pdf_path = tmp_path / "r.pdf"
    pdf_path.write_bytes(b"%PDF-1.7 fake")

    async def fake_resolve(url, **kw):
        return _ok_asset(str(pdf_path))

    with patch.object(oa_mod, "resolve_pdf", side_effect=fake_resolve), \
         patch.object(oa_mod, "parse_pdfs", side_effect=_fake_parse_pdfs) as p:
        r1 = await fetch_and_store_oa_fulltext(paper.id, pdf_url="https://oa/r.pdf", session=session)
        r2 = await fetch_and_store_oa_fulltext(paper.id, pdf_url="https://oa/r.pdf", session=session)

    assert r1["status"] == "done" and r2["status"] == "done" and r2.get("reused") is True
    assert p.call_count == 1  # 第二次复用不重解析 → MinerU 只调一次


# --------------------------------------------------------------------------
# IngestTool oa_fulltext action：从检索缓存回查 pdfUrl + 配额上限
# --------------------------------------------------------------------------

async def test_fetch_mineru_exception_returns_failed_not_raise(session, tmp_path):
    # codex P1：MinerU 批级异常须转 {status:failed}，不得抛出（否则整批循环中断）。
    paper = await add_paper(session, {"title": "Boom", "doi": "10.5/b", "source": "openalex"})
    pdf_path = tmp_path / "b.pdf"
    pdf_path.write_bytes(b"%PDF-1.7 fake")

    async def fake_resolve(url, **kw):
        return _ok_asset(str(pdf_path))

    async def boom_parse(*a, **kw):
        raise RuntimeError("MinerU submit 502")

    with patch.object(oa_mod, "resolve_pdf", side_effect=fake_resolve), \
         patch.object(oa_mod, "parse_pdfs", side_effect=boom_parse):
        res = await fetch_and_store_oa_fulltext(paper.id, pdf_url="https://oa/b.pdf", session=session)
    assert res["status"] == "failed" and "MinerU" in res["reason"]


async def test_fetch_does_not_reuse_incomplete_same_sha_attachment(session, tmp_path):
    # codex P2：已有同 sha 但 pending/无 markdown 的附件不算可用全文，须重新解析。
    paper = await add_paper(session, {"title": "Incomplete", "doi": "10.5/inc", "source": "openalex"})
    pdf_path = tmp_path / "inc.pdf"
    pdf_path.write_bytes(b"%PDF-1.7 fake")
    sha = "deadbeef" * 8
    # 预置一个 pending、无 markdown_path 的同 sha 附件
    session.add(Attachment(paper_id=paper.id, path=str(pdf_path), content_type="application/pdf",
                           sha256=sha, mineru_status="pending", markdown_path=None))
    await session.commit()

    async def fake_resolve(url, **kw):
        return _ok_asset(str(pdf_path))  # sha 与 pending 附件相同

    with patch.object(oa_mod, "resolve_pdf", side_effect=fake_resolve), \
         patch.object(oa_mod, "parse_pdfs", side_effect=_fake_parse_pdfs) as p:
        res = await fetch_and_store_oa_fulltext(paper.id, pdf_url="https://oa/inc.pdf", session=session)
    assert res["status"] == "done" and res.get("reused") is not True
    assert p.call_count == 1  # 未复用 → 真的重新解析了


async def test_fetch_does_not_reuse_done_attachment_with_empty_markdown_path(session, tmp_path):
    # codex P2 补：mineru_status=done 但 markdown_path 为空串也不算可用全文，须重解析。
    paper = await add_paper(session, {"title": "EmptyMd", "doi": "10.5/em", "source": "openalex"})
    pdf_path = tmp_path / "em.pdf"
    pdf_path.write_bytes(b"%PDF-1.7 fake")
    session.add(Attachment(paper_id=paper.id, path=str(pdf_path), content_type="application/pdf",
                           sha256="deadbeef" * 8, mineru_status="done", markdown_path=""))
    await session.commit()

    async def fake_resolve(url, **kw):
        return _ok_asset(str(pdf_path))

    with patch.object(oa_mod, "resolve_pdf", side_effect=fake_resolve), \
         patch.object(oa_mod, "parse_pdfs", side_effect=_fake_parse_pdfs) as p:
        res = await fetch_and_store_oa_fulltext(paper.id, pdf_url="https://oa/em.pdf", session=session)
    assert res["status"] == "done" and res.get("reused") is not True
    assert p.call_count == 1


async def test_ingest_tool_oa_fulltext_action(session_factory, tmp_path):
    from app.tools.ingest import IngestTool

    async with session_factory() as s:
        paper = await add_paper(s, {"title": "Tool OA", "doi": "10.7/tool", "source": "openalex"})
        await s.commit()
        pid = paper.id

    pdf_path = tmp_path / "t.pdf"
    pdf_path.write_bytes(b"%PDF-1.7 fake")

    async def fake_resolve(url, **kw):
        assert url == "https://oa/tool.pdf"  # 从 ctx 候选按 DOI 回查到的 pdfUrl
        return _ok_asset(str(pdf_path))

    ctx = {
        "session_factory": session_factory,
        "search_candidates": [{"doi": "10.7/tool", "pdfUrl": "https://oa/tool.pdf"}],
    }
    with patch.object(oa_mod, "resolve_pdf", side_effect=fake_resolve), \
         patch.object(oa_mod, "parse_pdfs", side_effect=_fake_parse_pdfs):
        res = await IngestTool(session_factory).execute("oa_fulltext", {"paper_ids": [pid]}, ctx)

    assert res.success and "成功 1 篇" in res.summary
