"""B3: GET /projects/{pid}/papers/{paperId}/structure — 结构化溯源端点测试。

覆盖：
  1. 有 DocumentStructure 的 paper → 200 + 块/表/元数据信封形状正确。
  2. 未关联本项目的 paper → 404。
  3. 关联了 paper(+Attachment) 但无 DocumentStructure → 404（零伪造，不返回空壳）。

DB 种子方式：复用 test_paper_markdown.py 的内联种子机制（同一 session_factory + 依赖覆盖），
在其上插入一条 DocumentStructure 行：content_list 与 full_md 取内联合成契约样例，
page_map / block_line_ranges 经 build_line_page_map / build_block_line_ranges 生成。
"""
from __future__ import annotations

import hashlib

import httpx
import pytest
import pytest_asyncio

from app.db import get_session
from app.main import app, get_r_client
from app.models import Attachment, DocumentStructure, Paper, ProjectPaper
from app.repositories.project import create_project
from app.structure.page_map import build_block_line_ranges, build_line_page_map
from helpers_contract import contract_content_list, contract_full_markdown


@pytest_asyncio.fixture
async def aclient(session_factory, fake_r):
    async def _test_session():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_r_client] = lambda: fake_r
    app.dependency_overrides[get_session] = _test_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, session_factory
    app.dependency_overrides.clear()


_COUNTER = {"n": 0}


def _dedup() -> str:
    _COUNTER["n"] += 1
    return f"title:structure-test-{_COUNTER['n']}"


def _load_fixtures() -> tuple[list[dict], str]:
    return contract_content_list(), contract_full_markdown()


async def _mk_paper(factory, pid: int, *, with_attachment: bool, with_structure: bool) -> int:
    """建 Paper(+ProjectPaper)；可选附 Attachment，再可选附 DocumentStructure。"""
    async with factory() as s:
        paper = Paper(
            title="Structure Test Paper",
            creators=[],
            source="upload",
            item_type="journalArticle",
            dedup_key=_dedup(),
        )
        s.add(paper)
        await s.flush()

        if with_attachment:
            _COUNTER["n"] += 1
            sha = f"{_COUNTER['n']:064x}"  # 64-hex 占位 PDF sha
            att = Attachment(
                paper_id=paper.id,
                mineru_status="done",
                sha256=sha,
            )
            s.add(att)
            await s.flush()

            if with_structure:
                content_list, full_md = _load_fixtures()
                page_map = build_line_page_map(full_md, content_list)
                block_line_ranges = build_block_line_ranges(full_md, content_list)
                page_count = max(
                    (int(b.get("page_idx", 0)) + 1 for b in content_list), default=0)
                has_bbox = any(b.get("bbox") for b in content_list)
                md_sha = hashlib.sha256(full_md.encode("utf-8")).hexdigest()
                s.add(DocumentStructure(
                    attachment_id=att.id,
                    content_list=content_list,
                    page_map=page_map,
                    block_line_ranges=block_line_ranges,
                    page_count=page_count,
                    has_bbox=has_bbox,
                    markdown_sha256=md_sha,
                    source_pdf_sha256=sha,
                    schema_version=1,
                ))

        s.add(ProjectPaper(project_id=pid, paper_id=paper.id, inclusion_status="included"))
        await s.commit()
        return paper.id


@pytest.mark.asyncio
async def test_structure_endpoint_shape(aclient):
    c, factory = aclient
    async with factory() as s:
        pid = (await create_project(s, {"name": "Struct Shape"})).id
    paper_id = await _mk_paper(factory, pid, with_attachment=True, with_structure=True)

    r = await c.get(f"/projects/{pid}/papers/{paper_id}/structure")
    assert r.status_code == 200, r.text
    d = r.json()

    assert {"paper_id", "attachment_id", "page_count", "blocks", "tables",
            "has_bbox", "markdown_sha256"} <= set(d.keys())
    assert d["paper_id"] == paper_id
    assert d["page_count"] >= 2

    assert d["blocks"], "blocks 不应为空"
    for blk in d["blocks"]:
        assert {"block_idx", "type", "page_no", "md_line_start",
                "md_line_end", "section_title"} <= set(blk.keys())

    assert len(d["tables"]) >= 1
    t0 = d["tables"][0]
    assert all(len(row) == t0["n_cols"] for row in t0["grid"]), "首张表网格须列对齐"


@pytest.mark.asyncio
async def test_structure_404_when_paper_not_in_project(aclient):
    c, factory = aclient
    async with factory() as s:
        pid = (await create_project(s, {"name": "Struct 404 unlinked"})).id
    r = await c.get(f"/projects/{pid}/papers/999999/structure")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_structure_404_cross_project_isolation(aclient):
    """跨项目隔离：paper(+structure) 关联项目 A,经项目 B 请求必 404(不泄漏别项目的结构)。"""
    c, factory = aclient
    async with factory() as s:
        pid_a = (await create_project(s, {"name": "Struct Owner A"})).id
        pid_b = (await create_project(s, {"name": "Struct Other B"})).id
    paper_id = await _mk_paper(factory, pid_a, with_attachment=True, with_structure=True)

    # 经拥有者 A：200
    r_a = await c.get(f"/projects/{pid_a}/papers/{paper_id}/structure")
    assert r_a.status_code == 200, r_a.text
    # 经非拥有者 B：404(虽然 paper 存在且有 structure,但未关联 B)
    r_b = await c.get(f"/projects/{pid_b}/papers/{paper_id}/structure")
    assert r_b.status_code == 404


@pytest.mark.asyncio
async def test_structure_404_when_no_structure(aclient):
    c, factory = aclient
    async with factory() as s:
        pid = (await create_project(s, {"name": "Struct 404 no-structure"})).id
    paper_id = await _mk_paper(factory, pid, with_attachment=True, with_structure=False)
    r = await c.get(f"/projects/{pid}/papers/{paper_id}/structure")
    assert r.status_code == 404
