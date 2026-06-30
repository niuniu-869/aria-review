"""B5: 轻量语料质检报告（确定性、非 LLM）测试。

覆盖：
  1. 直接单元测试 build_quality_report：脏语料种子 → 四类 issue 全覆盖，重复组每篇都标记。
  2. 端点测试 GET /projects/{pid}/quality-report → 200，信封含 total/issues/by_type。

DB 种子方式：复用 test_api_structure.py 的内联种子机制
（同一 session_factory + 依赖覆盖；dedup_key 用全局计数器保证唯一）。
"""
from __future__ import annotations

import httpx
import pytest
import pytest_asyncio

from app.db import get_session
from app.main import app, get_r_client
from app.models import Attachment, Paper, PaperExtraction, ProjectPaper
from app.repositories.project import create_project
from app.services.quality_check import build_quality_report


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
    return f"title:quality-test-{_COUNTER['n']}"


async def _seed_dirty_corpus(factory, pid: int) -> None:
    """种子脏语料：
    A: abstract=None, creators=[], year=None  → missing_metadata
    B,C: 相同 title+year+doi（dedup_key 不同）  → duplicate（两篇都标）
    D: 正常题录但无 Attachment              → not_parsed
    E: 有 mineru_status=done 附件但无 PaperExtraction → extraction_missing
    """
    async with factory() as s:
        # A — 缺元数据
        a = Paper(title="Paper A", creators=[], year=None, abstract=None,
                  source="upload", item_type="journalArticle", dedup_key=_dedup())
        # B,C — title+year+doi 撞车（dedup_key 各异，避免唯一约束冲突）
        b = Paper(title="Dup Paper", creators=[{"family": "X"}], year=2020,
                  doi="10.1/x", abstract="abs b", source="upload",
                  item_type="journalArticle", dedup_key=_dedup())
        c = Paper(title="Dup Paper", creators=[{"family": "Y"}], year=2020,
                  doi="10.1/x", abstract="abs c", source="upload",
                  item_type="journalArticle", dedup_key=_dedup())
        # D — 正常但无附件
        d = Paper(title="Paper D", creators=[{"family": "Z"}], year=2021,
                  abstract="abs d", source="upload",
                  item_type="journalArticle", dedup_key=_dedup())
        # E — 已解析(done)但无抽取
        e = Paper(title="Paper E", creators=[{"family": "W"}], year=2022,
                  abstract="abs e", source="upload",
                  item_type="journalArticle", dedup_key=_dedup())
        for p in (a, b, c, d, e):
            s.add(p)
        await s.flush()

        # E 的 done 附件
        s.add(Attachment(paper_id=e.id, mineru_status="done"))

        # 全部关联到项目
        order = 0
        for p in (a, b, c, d, e):
            s.add(ProjectPaper(project_id=pid, paper_id=p.id,
                               inclusion_status="included", order=order))
            order += 1
        await s.commit()


@pytest.mark.asyncio
async def test_build_quality_report_unit(aclient):
    c, factory = aclient
    async with factory() as s:
        pid = (await create_project(s, {"name": "Quality Unit"})).id
    await _seed_dirty_corpus(factory, pid)

    async with factory() as s:
        rep = await build_quality_report(s, pid)

    assert {"total", "issues", "by_type"} <= rep.keys()
    assert rep["total"] == 5

    types = {i["type"] for i in rep["issues"]}
    assert {"missing_metadata", "duplicate", "not_parsed",
            "extraction_missing"} <= types

    # B、C 都被标记为 duplicate
    assert rep["by_type"]["duplicate"] == 2

    # by_type 含全部四个键
    assert set(rep["by_type"].keys()) == {
        "missing_metadata", "duplicate", "not_parsed", "extraction_missing"}

    # issues 按 paper_id, type 排序（确定性）
    keys = [(i["paper_id"], i["type"]) for i in rep["issues"]]
    assert keys == sorted(keys)

    # 每条 issue 形状正确
    for i in rep["issues"]:
        assert {"paper_id", "type", "detail"} <= i.keys()
        assert isinstance(i["detail"], str) and i["detail"]


@pytest.mark.asyncio
async def test_quality_report_endpoint(aclient):
    c, factory = aclient
    async with factory() as s:
        pid = (await create_project(s, {"name": "Quality Endpoint"})).id
    await _seed_dirty_corpus(factory, pid)

    r = await c.get(f"/projects/{pid}/quality-report")
    assert r.status_code == 200, r.text
    d = r.json()
    assert {"total", "issues", "by_type"} <= set(d.keys())
    assert d["total"] == 5
    assert isinstance(d["issues"], list)
    assert d["by_type"]["duplicate"] == 2


@pytest.mark.asyncio
async def test_duplicate_detects_doi_variants(aclient):
    """DOI 变体归一(codex P2)：同一 DOI 的 https://doi.org/ 前缀变体应仍判重复；
    纯空白 abstract 应判 missing_metadata(codex P3)。"""
    c, factory = aclient
    async with factory() as s:
        pid = (await create_project(s, {"name": "Quality DOI"})).id
    async with factory() as s:
        p1 = Paper(title="Variant Title", creators=[{"family": "A"}], year=2019,
                   doi="10.5555/abc", abstract="   ",  # 纯空白 → missing abstract
                   source="upload", item_type="journalArticle", dedup_key=_dedup())
        p2 = Paper(title="Variant Title", creators=[{"family": "B"}], year=2019,
                   doi="https://doi.org/10.5555/ABC", abstract="abs",  # DOI 变体+大小写
                   source="upload", item_type="journalArticle", dedup_key=_dedup())
        s.add_all([p1, p2])
        await s.flush()
        s.add(ProjectPaper(project_id=pid, paper_id=p1.id, inclusion_status="included"))
        s.add(ProjectPaper(project_id=pid, paper_id=p2.id, inclusion_status="included"))
        await s.commit()

    async with factory() as s:
        rep = await build_quality_report(s, pid)
    # 两篇 DOI 变体被归一为同一 key → 都标记 duplicate
    assert rep["by_type"]["duplicate"] == 2
    # p1 的纯空白 abstract 被判缺
    p1_missing = [i for i in rep["issues"]
                  if i["paper_id"] == p1.id and i["type"] == "missing_metadata"]
    assert p1_missing and "abstract" in p1_missing[0]["detail"]


@pytest.mark.asyncio
async def test_quality_report_empty_project(aclient):
    """空/未知项目扫描 → total=0，issues 空，by_type 四键皆 0。"""
    c, factory = aclient
    async with factory() as s:
        pid = (await create_project(s, {"name": "Quality Empty"})).id

    async with factory() as s:
        rep = await build_quality_report(s, pid)
    assert rep["total"] == 0
    assert rep["issues"] == []
    assert rep["by_type"] == {
        "missing_metadata": 0, "duplicate": 0,
        "not_parsed": 0, "extraction_missing": 0}
