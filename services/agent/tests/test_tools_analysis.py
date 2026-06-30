"""阶段3b — AnalysisTool 单元测试。

覆盖:
1. overview → mock RClient 调用成功
2. corpus 不存在 → success=False（不抛异常）
3. r_corpus_id 为空 → success=False（不抛异常）
4. R 服务返回 404 → success=False
5. sources / authors / documents 同模式
6. ToolRegistry 注册后 to_function_definitions 合法 schema
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.tools.analysis import AnalysisTool
from app.harness.tools import ToolRegistry
from app.models import Corpus, Project


# ======================================================================
# Fixtures
# ======================================================================

@pytest.fixture
def session_factory(session):
    return async_sessionmaker(session.bind, expire_on_commit=False)


@pytest.fixture
def fake_r_with_corpus(fake_r):
    """预先在 FakeR 中注册一个语料 ID。"""
    fake_r.corpora["r-corpus-abc123"] = {
        "status": "ready", "documentCount": 5, "dbsource": "wos"
    }
    return fake_r


async def _seed_corpus(session, r_corpus_id: str | None = "r-corpus-abc123") -> Corpus:
    """在测试库中种一个 Corpus 行（含 r_corpus_id）。"""
    # 先要一个 Project
    proj = Project(name="SeedProject")
    session.add(proj)
    await session.flush()

    corpus = Corpus(
        project_id=proj.id,
        content_hash="deadbeef" * 8,
        r_corpus_id=r_corpus_id,
        status="ready",
        document_count=5,
    )
    session.add(corpus)
    await session.commit()
    await session.refresh(corpus)
    return corpus


# ======================================================================
# Test 1: overview 成功路径
# ======================================================================

@pytest.mark.asyncio
async def test_overview_success(session, session_factory, fake_r_with_corpus):
    """overview: corpus 存在且 r_corpus_id 有值 → 调 mock 并返回 success=True。"""
    corpus = await _seed_corpus(session, r_corpus_id="r-corpus-abc123")
    tool = AnalysisTool(session_factory=session_factory, r_client=fake_r_with_corpus)

    result = await tool.execute("overview", {"corpus_id": corpus.id})
    assert result.success, f"Expected success but got error: {result.error}"
    assert len(result.data) == 1
    body = result.data[0]
    assert "stats" in body  # FakeR get_overview 返回有 stats 字段


# ======================================================================
# Test 2: corpus 不存在
# ======================================================================

@pytest.mark.asyncio
async def test_overview_corpus_not_found(session_factory, fake_r):
    """corpus id 不存在 → success=False，不抛异常。"""
    tool = AnalysisTool(session_factory=session_factory, r_client=fake_r)
    result = await tool.execute("overview", {"corpus_id": 99999999})
    assert not result.success
    assert result.error is not None
    assert "不存在" in result.error


# ======================================================================
# Test 3: r_corpus_id 为空
# ======================================================================

@pytest.mark.asyncio
async def test_overview_r_corpus_id_empty(session, session_factory, fake_r):
    """corpus 存在但 r_corpus_id 为空（还没 build）→ success=False，不抛异常。"""
    corpus = await _seed_corpus(session, r_corpus_id=None)
    tool = AnalysisTool(session_factory=session_factory, r_client=fake_r)

    result = await tool.execute("overview", {"corpus_id": corpus.id})
    assert not result.success
    assert "r_corpus_id" in (result.error or "") or "build" in (result.error or "").lower()


# ======================================================================
# Test 4: R 服务 404
# ======================================================================

@pytest.mark.asyncio
async def test_overview_r_returns_404(session, session_factory, fake_r):
    """r_corpus_id 在 FakeR 中不存在 → R 返回 404 → success=False。"""
    # 种一个带 r_corpus_id 但 FakeR 不认识的 corpus
    corpus = await _seed_corpus(session, r_corpus_id="r-unknown-corpus")
    tool = AnalysisTool(session_factory=session_factory, r_client=fake_r)

    result = await tool.execute("overview", {"corpus_id": corpus.id})
    assert not result.success
    assert result.error is not None


# ======================================================================
# Test 5: sources / authors / documents
# ======================================================================

@pytest.mark.asyncio
async def test_sources_success(session, session_factory, fake_r_with_corpus):
    corpus = await _seed_corpus(session, r_corpus_id="r-corpus-abc123")
    tool = AnalysisTool(session_factory=session_factory, r_client=fake_r_with_corpus)

    result = await tool.execute("sources", {"corpus_id": corpus.id})
    assert result.success
    assert len(result.data) == 1
    assert "topSources" in result.data[0]


@pytest.mark.asyncio
async def test_authors_success(session, session_factory, fake_r_with_corpus):
    corpus = await _seed_corpus(session, r_corpus_id="r-corpus-abc123")
    tool = AnalysisTool(session_factory=session_factory, r_client=fake_r_with_corpus)

    result = await tool.execute("authors", {"corpus_id": corpus.id})
    assert result.success
    assert "topAuthors" in result.data[0]


@pytest.mark.asyncio
async def test_documents_success(session, session_factory, fake_r_with_corpus):
    corpus = await _seed_corpus(session, r_corpus_id="r-corpus-abc123")
    tool = AnalysisTool(session_factory=session_factory, r_client=fake_r_with_corpus)

    result = await tool.execute("documents", {"corpus_id": corpus.id})
    assert result.success
    assert "topCited" in result.data[0]


# ======================================================================
# Test 6: missing corpus_id param
# ======================================================================

@pytest.mark.asyncio
async def test_overview_missing_corpus_id(session_factory, fake_r):
    """corpus_id 参数缺失 → success=False。"""
    tool = AnalysisTool(session_factory=session_factory, r_client=fake_r)
    result = await tool.execute("overview", {})
    assert not result.success
    assert "corpus_id" in (result.error or "")


# ======================================================================
# Test 7: ToolRegistry schema
# ======================================================================

def test_analysis_tool_function_definitions(session, fake_r):
    """AnalysisTool 注册进 ToolRegistry 后 to_function_definitions 合法。"""
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    tool = AnalysisTool(session_factory=factory, r_client=fake_r)

    registry = ToolRegistry()
    registry.register(tool)

    defs = registry.get_function_definitions()
    names = {d["function"]["name"] for d in defs}
    assert "analysis__overview" in names
    assert "analysis__sources" in names
    assert "analysis__authors" in names
    assert "analysis__documents" in names

    for fd in defs:
        assert fd["type"] == "function"
        params = fd["function"]["parameters"]
        assert params["type"] == "object"
        assert "properties" in params
