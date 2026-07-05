"""阶段3b — ProjectTool 单元测试。

覆盖:
1. create 创建项目
2. add 向项目添加论文（幂等）
3. set_inclusion 设置筛选状态（含 score）
4. list 无 project_id → 列项目；有 project_id → 列论文含 inclusion_status
5. ToolRegistry 注册后 to_function_definitions 合法 schema
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.errors import ApiError
from app.models import Attachment, Paper
from app.tools.project import ProjectTool
from app.harness.tools import ToolRegistry
from app.repositories.library import add_paper


# ======================================================================
# Fixtures
# ======================================================================

@pytest.fixture
def proj_tool(session):
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    return ProjectTool(session_factory=factory)


@pytest.fixture
def session_factory(session):
    return async_sessionmaker(session.bind, expire_on_commit=False)


# ======================================================================
# Test 1: create
# ======================================================================

@pytest.mark.asyncio
async def test_create_project(proj_tool):
    """create 写入项目，返回 project_id 和 name。"""
    result = await proj_tool.execute("create", {"name": "SLR on ML", "research_question": "What?"})
    assert result.success
    row = result.data[0]
    assert row["project_id"] is not None
    assert row["name"] == "SLR on ML"
    assert row["research_question"] == "What?"


@pytest.mark.asyncio
async def test_create_missing_name(proj_tool):
    """create 无 name → success=False。"""
    result = await proj_tool.execute("create", {})
    assert not result.success


# ======================================================================
# Test 2: add
# ======================================================================

@pytest.mark.asyncio
async def test_add_papers_to_project(session, proj_tool):
    """add 向项目添加多篇论文。"""
    # 先建论文
    p1 = await add_paper(session, {"title": "Paper A", "doi": "10.1/a"})
    p2 = await add_paper(session, {"title": "Paper B", "doi": "10.1/b"})

    # 创建项目
    create_r = await proj_tool.execute("create", {"name": "P1"})
    project_id = create_r.data[0]["project_id"]

    result = await proj_tool.execute(
        "add", {"project_id": project_id, "paper_ids": [p1.id, p2.id]}
    )
    assert result.success
    assert len(result.data) == 2
    statuses = {row["inclusion_status"] for row in result.data}
    assert statuses == {"candidate"}


@pytest.mark.asyncio
async def test_add_papers_idempotent(session, proj_tool):
    """相同论文两次 add → 不报错，返回相同 project_paper_id。"""
    paper = await add_paper(session, {"title": "Idempotent", "doi": "10.1/idem"})
    create_r = await proj_tool.execute("create", {"name": "P2"})
    project_id = create_r.data[0]["project_id"]

    r1 = await proj_tool.execute("add", {"project_id": project_id, "paper_ids": [paper.id]})
    r2 = await proj_tool.execute("add", {"project_id": project_id, "paper_ids": [paper.id]})
    assert r1.success and r2.success
    assert r1.data[0]["project_paper_id"] == r2.data[0]["project_paper_id"]


@pytest.mark.asyncio
async def test_import_search_results_preserves_metadata_and_dedups(proj_tool):
    """import_search_results 从检索缓存导入，保留题录并按 DOI/title 幂等去重。"""
    create_r = await proj_tool.execute("create", {"name": "Search Import"})
    project_id = create_r.data[0]["project_id"]
    ctx = {
        "search_candidates": [
            {
                "candidate_id": "W1",
                "openalexId": "W1",
                "title": "AI structural design in civil engineering",
                "doi": "10.1000/ai-struct",
                "authors": ["Ada Chen", "Bo Wang"],
                "year": 2024,
                "abstract": "A real metadata-rich OpenAlex candidate.",
                "containerTitle": "Engineering Structures",
                "url": "https://doi.org/10.1000/ai-struct",
                "source": "openalex",
            },
            {
                "candidate_id": "W1-dup",
                "openalexId": "W1",
                "title": "AI structural design in civil engineering",
                "doi": "10.1000/ai-struct",
                "authors": ["Ada Chen"],
                "year": 2024,
            },
        ]
    }

    r1 = await proj_tool.execute(
        "import_search_results",
        {"project_id": project_id, "default_status": "included"},
        context=ctx,
    )
    r2 = await proj_tool.execute(
        "import_search_results",
        {"project_id": project_id, "default_status": "included"},
        context=ctx,
    )

    assert r1.success and r2.success
    assert r1.data[0]["imported"] == 1
    assert r1.data[0]["skipped"] == 0
    assert r2.data[0]["imported"] == 0
    assert r2.data[0]["skipped"] == 1

    listed = await proj_tool.execute("list", {"project_id": project_id})
    assert len(listed.data) == 1
    row = listed.data[0]
    assert row["doi"] == "10.1000/ai-struct"
    assert row["inclusion_status"] == "included"


async def test_import_search_results_blocks_bulk_import_without_selection(proj_tool):
    """双级筛硬约束：候选量超阈值却不传 candidate_ids → 拒绝，逼 Agent 先挑选（QA 污染库缺陷）。"""
    create_r = await proj_tool.execute("create", {"name": "Bulk Guard"})
    project_id = create_r.data[0]["project_id"]
    # 造 60 个不同候选（> 阈值 50）
    cands = [
        {"candidate_id": f"C{i}", "title": f"Paper number {i}", "doi": f"10.1/{i}", "year": 2022}
        for i in range(60)
    ]
    ctx = {"search_candidates": cands}

    # 不传 candidate_ids → 拒绝，不导入
    blocked = await proj_tool.execute(
        "import_search_results", {"project_id": project_id}, context=ctx,
    )
    assert blocked.success is False
    assert "candidate_ids" in blocked.error and "整批导入" in blocked.error

    # 传 candidate_ids 精选 3 篇 → 正常导入这 3 篇
    ok = await proj_tool.execute(
        "import_search_results",
        {"project_id": project_id, "candidate_ids": ["C0", "C1", "C2"]},
        context=ctx,
    )
    assert ok.success and ok.data[0]["imported"] == 3
    listed = await proj_tool.execute("list", {"project_id": project_id})
    assert len(listed.data) == 3


async def test_import_search_results_small_set_still_allows_default_all(proj_tool):
    """阈值内的小候选集仍允许省略 candidate_ids 默认全导（不误伤既有小集流程）。"""
    create_r = await proj_tool.execute("create", {"name": "Small Set"})
    project_id = create_r.data[0]["project_id"]
    cands = [
        {"candidate_id": f"S{i}", "title": f"Small paper {i}", "doi": f"10.2/{i}", "year": 2023}
        for i in range(5)
    ]
    r = await proj_tool.execute(
        "import_search_results", {"project_id": project_id}, context={"search_candidates": cands},
    )
    assert r.success and r.data[0]["imported"] == 5


@pytest.mark.asyncio
async def test_import_search_results_auto_fetches_sciverse_fulltext(proj_tool, session, monkeypatch, tmp_path):
    """导入 Sciverse doc_id 候选后自动补全文，单篇失败不影响题录导入。"""

    class FakeSciverseClient:
        async def content(self, doc_id, offset=None, limit=None):
            if doc_id == "doc-bad":
                raise ApiError(503, "SCIVERSE_UNAVAILABLE", "mock failed")
            return {"text": f"# {doc_id}\n\nFull text", "more": False}

    monkeypatch.setattr(settings, "corpora_dir", str(tmp_path))
    monkeypatch.setattr("app.tools.project.SciverseClient", lambda cfg: FakeSciverseClient())

    create_r = await proj_tool.execute("create", {"name": "Sciverse Import"})
    project_id = create_r.data[0]["project_id"]
    ctx = {
        "sciverse": {"base_url": "https://api.sciverse.space", "api_token": "token"},
        "search_candidates": [
            {
                "candidate_id": "s1",
                "title": "Sciverse Fulltext OK",
                "doi": "10.2000/s1",
                "sciverseDocId": "doc-ok",
                "citedByCount": 9.8,
            },
            {
                "candidate_id": "s2",
                "title": "Sciverse Fulltext Bad",
                "doi": "10.2000/s2",
                "sciverseDocId": "doc-bad",
            },
            {
                "candidate_id": "m1",
                "title": "Metadata Only",
                "doi": "10.2000/m1",
            },
        ],
    }

    result = await proj_tool.execute(
        "import_search_results",
        {"project_id": project_id, "default_status": "included"},
        context=ctx,
    )

    assert result.success, result.error
    row = result.data[0]
    assert row["imported"] == 3
    assert row["sciverse_fulltext"]["eligible"] == 2
    assert row["sciverse_fulltext"]["fetched"] == 1
    assert row["sciverse_fulltext"]["failed"] == 1
    assert "已自动拉取 1 篇成功 1 篇失败" in result.summary

    async with async_sessionmaker(session.bind, expire_on_commit=False)() as s:
        ok = (await s.execute(select(Paper).where(Paper.doi == "10.2000/s1"))).scalar_one()
        bad = (await s.execute(select(Paper).where(Paper.doi == "10.2000/s2"))).scalar_one()
        ok_atts = list((await s.execute(
            select(Attachment).where(Attachment.paper_id == ok.id)
        )).scalars().all())
        bad_atts = list((await s.execute(
            select(Attachment).where(Attachment.paper_id == bad.id)
        )).scalars().all())

    assert ok.csl_json["citedByCount"] == 9
    assert len(ok_atts) == 1
    assert ok_atts[0].content_type == "text/markdown"
    assert bad_atts == []


# ======================================================================
# Test 3: set_inclusion
# ======================================================================

@pytest.mark.asyncio
async def test_set_inclusion_included(session, proj_tool):
    """set_inclusion → included，reason/score 正确写入。"""
    paper = await add_paper(session, {"title": "Included Paper", "doi": "10.1/inc"})
    create_r = await proj_tool.execute("create", {"name": "P3"})
    project_id = create_r.data[0]["project_id"]
    await proj_tool.execute("add", {"project_id": project_id, "paper_ids": [paper.id]})

    result = await proj_tool.execute("set_inclusion", {
        "project_id": project_id,
        "paper_id": paper.id,
        "status": "included",
        "score": 95,
    })
    assert result.success
    row = result.data[0]
    assert row["inclusion_status"] == "included"
    assert row["screening_score"] == 95


@pytest.mark.asyncio
async def test_set_inclusion_excluded_with_reason(session, proj_tool):
    """set_inclusion → excluded，exclusion_reason 写入。"""
    paper = await add_paper(session, {"title": "Excluded Paper", "doi": "10.1/exc"})
    create_r = await proj_tool.execute("create", {"name": "P4"})
    project_id = create_r.data[0]["project_id"]
    await proj_tool.execute("add", {"project_id": project_id, "paper_ids": [paper.id]})

    result = await proj_tool.execute("set_inclusion", {
        "project_id": project_id,
        "paper_id": paper.id,
        "status": "excluded",
        "reason": "Out of scope",
    })
    assert result.success
    row = result.data[0]
    assert row["inclusion_status"] == "excluded"
    assert row["exclusion_reason"] == "Out of scope"


@pytest.mark.asyncio
async def test_set_inclusion_invalid_status(session, proj_tool):
    """非法 status → success=False。"""
    paper = await add_paper(session, {"title": "Bad Status", "doi": "10.1/bs"})
    create_r = await proj_tool.execute("create", {"name": "P5"})
    project_id = create_r.data[0]["project_id"]
    await proj_tool.execute("add", {"project_id": project_id, "paper_ids": [paper.id]})

    result = await proj_tool.execute("set_inclusion", {
        "project_id": project_id,
        "paper_id": paper.id,
        "status": "invalid_status",
    })
    assert not result.success


@pytest.mark.asyncio
async def test_set_inclusion_paper_not_in_project(session, proj_tool):
    """论文未关联项目时 set_inclusion → success=False。"""
    paper = await add_paper(session, {"title": "Orphan Paper", "doi": "10.1/orp"})
    create_r = await proj_tool.execute("create", {"name": "P6"})
    project_id = create_r.data[0]["project_id"]
    # 故意不 add

    result = await proj_tool.execute("set_inclusion", {
        "project_id": project_id,
        "paper_id": paper.id,
        "status": "included",
    })
    assert not result.success
    assert "add" in (result.error or "").lower()


# ======================================================================
# Test 4: list
# ======================================================================

@pytest.mark.asyncio
async def test_list_projects(proj_tool):
    """list 无 project_id → 列出所有项目。"""
    await proj_tool.execute("create", {"name": "ListTest1"})
    await proj_tool.execute("create", {"name": "ListTest2"})

    result = await proj_tool.execute("list", {})
    assert result.success
    names = [r["name"] for r in result.data]
    assert "ListTest1" in names
    assert "ListTest2" in names


@pytest.mark.asyncio
async def test_list_papers_in_project(session, proj_tool):
    """list 有 project_id → 列该项目论文含 inclusion_status。"""
    p1 = await add_paper(session, {"title": "List Paper A", "doi": "10.1/lpa"})
    p2 = await add_paper(session, {"title": "List Paper B", "doi": "10.1/lpb"})
    create_r = await proj_tool.execute("create", {"name": "ListProject"})
    project_id = create_r.data[0]["project_id"]
    await proj_tool.execute("add", {"project_id": project_id, "paper_ids": [p1.id, p2.id]})

    # 将 p2 设为 included
    await proj_tool.execute("set_inclusion", {
        "project_id": project_id, "paper_id": p2.id, "status": "included"
    })

    result = await proj_tool.execute("list", {"project_id": project_id})
    assert result.success
    assert len(result.data) == 2
    statuses = {row["paper_id"]: row["inclusion_status"] for row in result.data}
    assert statuses[p1.id] == "candidate"
    assert statuses[p2.id] == "included"


# ======================================================================
# Test 5: ToolRegistry schema
# ======================================================================

def test_project_tool_function_definitions(session):
    """ProjectTool 注册进 ToolRegistry 后 to_function_definitions 合法。"""
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    tool = ProjectTool(session_factory=factory)

    registry = ToolRegistry()
    registry.register(tool)

    defs = registry.get_function_definitions()
    names = {d["function"]["name"] for d in defs}
    assert "project__create" in names
    assert "project__add" in names
    assert "project__import_search_results" in names
    assert "project__set_inclusion" in names
    assert "project__list" in names

    for fd in defs:
        assert fd["type"] == "function"
        params = fd["function"]["parameters"]
        assert params["type"] == "object"
        assert "properties" in params
