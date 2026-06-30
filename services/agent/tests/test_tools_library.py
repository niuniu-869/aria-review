"""阶段3b — LibraryTool 单元测试。

覆盖:
1. add 幂等（同 DOI 两次返回同 id，created=False）
2. find 命中与未命中
3. tag 幂等（同标签打两次不报错，返回相同列表）
4. get 正常 & 不存在
5. ToolRegistry 注册后 to_function_definitions 合法 schema
6. execute 正常 dispatch
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.tools.library import LibraryTool
from app.harness.tools import ToolRegistry


# ======================================================================
# fixture: 注入测试库 session_factory
# ======================================================================

@pytest.fixture
def lib_tool(session):
    """LibraryTool 注入测试库 session_factory。

    session fixture 来自 conftest.py（每测试建空库）。
    session.bind 返回 AsyncEngine，用它构建 async_sessionmaker。
    """
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    return LibraryTool(session_factory=factory)


# ======================================================================
# Test 1: add — 幂等
# ======================================================================

@pytest.mark.asyncio
async def test_add_creates_paper(lib_tool):
    """add 新论文 → success=True，created=True，返回 paper_id。"""
    result = await lib_tool.execute("add", {"title": "Deep Learning", "doi": "10.1/dl"})
    assert result.success
    assert len(result.data) == 1
    row = result.data[0]
    assert row["paper_id"] is not None
    assert row["created"] is True
    assert row["doi"] == "10.1/dl"


@pytest.mark.asyncio
async def test_add_idempotent_same_doi(lib_tool):
    """相同 DOI 两次 add → 同一 paper_id，第二次 created=False。"""
    r1 = await lib_tool.execute("add", {"title": "Paper X", "doi": "10.1/x"})
    r2 = await lib_tool.execute("add", {"title": "Paper X different title", "doi": "10.1/x"})
    assert r1.success and r2.success
    assert r1.data[0]["paper_id"] == r2.data[0]["paper_id"]
    assert r2.data[0]["created"] is False


@pytest.mark.asyncio
async def test_add_missing_title_fails(lib_tool):
    """add 缺少 title → success=False。"""
    result = await lib_tool.execute("add", {"doi": "10.1/notitle"})
    assert not result.success
    assert "title" in (result.error or "").lower()


# ======================================================================
# Test 2: find
# ======================================================================

@pytest.mark.asyncio
async def test_find_hits(lib_tool):
    """find 命中 → data 包含匹配论文。"""
    await lib_tool.execute("add", {"title": "Bibliometric Analysis in Python", "doi": "10.1/ba"})
    result = await lib_tool.execute("find", {"query": "Bibliometric"})
    assert result.success
    assert any("Bibliometric" in row["title"] for row in result.data)


@pytest.mark.asyncio
async def test_find_no_results(lib_tool):
    """find 无命中 → success=True，data=[]，summary 含提示。"""
    result = await lib_tool.execute("find", {"query": "ZZZNOMATCH999"})
    assert result.success
    assert result.data == []


@pytest.mark.asyncio
async def test_find_empty_query_fails(lib_tool):
    """find 空 query → success=False。"""
    result = await lib_tool.execute("find", {"query": ""})
    assert not result.success


# ======================================================================
# Test 3: get
# ======================================================================

@pytest.mark.asyncio
async def test_get_existing(lib_tool):
    """get 已存在的论文 → 返回完整详情。"""
    add_result = await lib_tool.execute(
        "add", {"title": "Survey Paper", "doi": "10.1/sp", "year": 2023}
    )
    paper_id = add_result.data[0]["paper_id"]

    result = await lib_tool.execute("get", {"paper_id": paper_id})
    assert result.success
    assert len(result.data) == 1
    row = result.data[0]
    assert row["id"] == paper_id
    assert row["title"] == "Survey Paper"
    assert row["year"] == 2023


@pytest.mark.asyncio
async def test_get_nonexistent(lib_tool):
    """get 不存在的 paper_id → success=False。"""
    result = await lib_tool.execute("get", {"paper_id": 99999999})
    assert not result.success
    assert "不存在" in (result.error or "")


# ======================================================================
# Test 4: tag — 幂等
# ======================================================================

@pytest.mark.asyncio
async def test_tag_paper(lib_tool):
    """tag 给论文打标签 → success=True，applied_tags 正确。"""
    add_r = await lib_tool.execute("add", {"title": "Tagged Paper", "doi": "10.1/tp"})
    pid = add_r.data[0]["paper_id"]

    result = await lib_tool.execute("tag", {"paper_id": pid, "tags": ["ml", "review"]})
    assert result.success
    applied = result.data[0]["applied_tags"]
    assert "ml" in applied
    assert "review" in applied


@pytest.mark.asyncio
async def test_tag_idempotent(lib_tool):
    """同一标签打两次 → 不报错，仍返回该标签。"""
    add_r = await lib_tool.execute("add", {"title": "Idempotent Tag", "doi": "10.1/it"})
    pid = add_r.data[0]["paper_id"]

    r1 = await lib_tool.execute("tag", {"paper_id": pid, "tags": ["idempotent"]})
    r2 = await lib_tool.execute("tag", {"paper_id": pid, "tags": ["idempotent"]})
    assert r1.success and r2.success
    assert "idempotent" in r1.data[0]["applied_tags"]
    assert "idempotent" in r2.data[0]["applied_tags"]


@pytest.mark.asyncio
async def test_tag_nonexistent_paper(lib_tool):
    """tag 不存在的 paper_id → success=False。"""
    result = await lib_tool.execute("tag", {"paper_id": 99999, "tags": ["x"]})
    assert not result.success


# ======================================================================
# Test 5: ToolRegistry 注册 + to_function_definitions
# ======================================================================

def test_library_tool_function_definitions(session):
    """LibraryTool 注册进 ToolRegistry 后 to_function_definitions 产出合法 schema。"""
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    tool = LibraryTool(session_factory=factory)

    registry = ToolRegistry()
    registry.register(tool)

    defs = registry.get_function_definitions()
    names = {d["function"]["name"] for d in defs}
    assert "library__add" in names
    assert "library__find" in names
    assert "library__get" in names
    assert "library__tag" in names

    for fd in defs:
        assert fd["type"] == "function"
        func = fd["function"]
        assert "name" in func
        assert "description" in func
        params = func["parameters"]
        assert params["type"] == "object"
        assert "properties" in params
