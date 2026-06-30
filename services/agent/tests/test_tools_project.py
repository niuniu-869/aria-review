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
from sqlalchemy.ext.asyncio import async_sessionmaker

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
