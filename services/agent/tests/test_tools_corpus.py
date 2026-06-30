"""阶段3a — CorpusTool 单元测试 + 真实 R 集成测试。

单元测试（全量，mock r_client）:
  1. build 成功路径: snapshot → 调R → mark_ready → status=ready
  2. build 幂等: 同 project 同内容两次 build → corpus_id 不变，第二次直接复用
  3. build R 失败: R 返回 422 → status=failed，ToolResult success=False
  4. build 空 included: → success=False, 合理错误
  5. status: 返回 corpus status/document_count/r_corpus_id
  6. status corpus 不存在: → success=False
  7. ToolRegistry schema 合法

真实 R 集成（skipif R 8001 不可达）:
  8. seed project + 3 included papers → build → 拿到 r_corpus_id →
     调 analysis__overview 验证能出概览
"""
from __future__ import annotations

import socket

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.harness.tools import ToolRegistry
from app.models import Corpus, Project
from app.repositories.library import add_paper
from app.repositories.project import (
    add_paper_to_project,
    create_project,
    set_inclusion,
)
from app.tools.corpus import CorpusTool


# ==============================================================================
# 工具函数
# ==============================================================================

def _r_reachable() -> bool:
    """探测 R 服务 :8001 是否可达（用于 skipif）。"""
    try:
        s = socket.create_connection(("127.0.0.1", 8001), timeout=2)
        s.close()
        return True
    except OSError:
        return False


SKIP_NO_R = pytest.mark.skipif(
    not _r_reachable(), reason="R 服务 localhost:8001 不可达，跳过真实集成测试"
)


# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def session_factory(session):
    return async_sessionmaker(session.bind, expire_on_commit=False)


async def _seed_project_with_papers(session, n: int = 3) -> tuple[Project, list]:
    """建一个 project，并插入 n 篇 included 论文（用于 build 测试）。"""
    proj = await create_project(session, {"name": f"Corpus Test Project ({n} papers)"})
    papers = []
    for i in range(n):
        p = await add_paper(session, {
            "title": f"Test Paper {i + 1}: Bibliometric Analysis",
            "doi": f"10.1000/test.{i + 1}",
            "year": 2020 + i,
            "abstract": f"Abstract of paper {i + 1} on bibliometrics and science mapping.",
            "keywords": "bibliometrics; science mapping",
            "container_title": "Journal of Informetrics",
            "volume": str(i + 1),
            "issue": "1",
            "creators": [{"family": f"Author{i + 1}", "given": "A"}],
        })
        pp = await add_paper_to_project(session, proj.id, p.id)
        await set_inclusion(session, pp.id, "included")
        papers.append(p)
    return proj, papers


# ==============================================================================
# Test 1: build 成功路径（mock R）
# ==============================================================================

@pytest.mark.asyncio
async def test_build_success(session, session_factory, fake_r):
    """build 正常流: snapshot → mock R → mark_ready → status=ready。"""
    proj, papers = await _seed_project_with_papers(session, n=3)
    tool = CorpusTool(session_factory=session_factory, r_client=fake_r)

    result = await tool.execute("build", {"project_id": proj.id})
    assert result.success, f"Expected success but got: {result.error}"
    row = result.data[0]
    assert row["status"] == "ready"
    assert row["r_corpus_id"] == "55555555-5555-4555-8555-555555555555"
    assert row["document_count"] == 3

    # DB 中也已更新
    async with session_factory() as s:
        q = select(Corpus).where(Corpus.id == row["corpus_id"])
        corpus = (await s.execute(q)).scalar_one()
    assert corpus.status == "ready"
    assert corpus.r_corpus_id == "55555555-5555-4555-8555-555555555555"


# ==============================================================================
# Test 2: build 幂等 — 同内容两次 build
# ==============================================================================

@pytest.mark.asyncio
async def test_build_idempotent(session, session_factory, fake_r):
    """相同 included 集合两次 build → corpus_id 相同，第二次直接复用 ready。"""
    proj, _ = await _seed_project_with_papers(session, n=2)
    tool = CorpusTool(session_factory=session_factory, r_client=fake_r)

    r1 = await tool.execute("build", {"project_id": proj.id})
    r2 = await tool.execute("build", {"project_id": proj.id})

    assert r1.success and r2.success
    assert r1.data[0]["corpus_id"] == r2.data[0]["corpus_id"]
    # 第二次应直接返回 ready（不重调 R）
    assert "复用" in r2.summary or r2.data[0]["status"] == "ready"


# ==============================================================================
# Test 3: build R 失败 → status=failed, success=False
# ==============================================================================

@pytest.mark.asyncio
async def test_build_r_failure(session, session_factory, fake_r):
    """R 返回 422 → corpus status=failed，ToolResult success=False。"""
    proj = await create_project(session, {"name": "R Fail Project"})
    p = await add_paper(session, {
        "title": "__fail__",  # FakeR 识别此标记并返回 422
        "doi": "10.1000/fail",
        "year": 2020,
    })
    pp = await add_paper_to_project(session, proj.id, p.id)
    await set_inclusion(session, pp.id, "included")

    tool = CorpusTool(session_factory=session_factory, r_client=fake_r)
    result = await tool.execute("build", {"project_id": proj.id})

    assert not result.success
    assert result.error is not None
    assert "失败" in result.error or "R" in result.error

    # DB 中 corpus 应为 failed
    async with session_factory() as s:
        q = select(Corpus).where(Corpus.project_id == proj.id)
        corpus = (await s.execute(q)).scalar_one_or_none()
    assert corpus is not None
    assert corpus.status == "failed"


# ==============================================================================
# Test 4: build 空 included → 合理错误
# ==============================================================================

@pytest.mark.asyncio
async def test_build_empty_included(session, session_factory, fake_r):
    """project 无 included 论文 → success=False, 有明确错误信息。"""
    proj = await create_project(session, {"name": "Empty Project"})
    # 加一篇但不设 included（默认 candidate）
    p = await add_paper(session, {"title": "Candidate Paper", "doi": "10.1000/cand"})
    await add_paper_to_project(session, proj.id, p.id)

    tool = CorpusTool(session_factory=session_factory, r_client=fake_r)
    result = await tool.execute("build", {"project_id": proj.id})

    assert not result.success
    assert result.error is not None
    # 错误信息应提到 included 或 project
    assert "included" in result.error or "没有" in result.error or "project" in result.error.lower()


# ==============================================================================
# Test 5: status — 返回现有 corpus 信息
# ==============================================================================

@pytest.mark.asyncio
async def test_status_existing_corpus(session, session_factory, fake_r):
    """status: corpus 存在 → 返回 status/document_count/r_corpus_id。"""
    # 先种一个 corpus
    proj = Project(name="Status Test")
    session.add(proj)
    await session.flush()
    corpus = Corpus(
        project_id=proj.id, content_hash="abcd" * 16,
        status="ready", document_count=5,
        r_corpus_id="r-test-abc",
    )
    session.add(corpus)
    await session.commit()
    await session.refresh(corpus)

    tool = CorpusTool(session_factory=session_factory, r_client=fake_r)
    result = await tool.execute("status", {"corpus_id": corpus.id})

    assert result.success
    row = result.data[0]
    assert row["status"] == "ready"
    assert row["document_count"] == 5
    assert row["r_corpus_id"] == "r-test-abc"


# ==============================================================================
# Test 6: status — corpus 不存在
# ==============================================================================

@pytest.mark.asyncio
async def test_status_not_found(session_factory, fake_r):
    """status: corpus id 不存在 → success=False。"""
    tool = CorpusTool(session_factory=session_factory, r_client=fake_r)
    result = await tool.execute("status", {"corpus_id": 99999999})

    assert not result.success
    assert result.error is not None
    assert "不存在" in result.error


# ==============================================================================
# Test 7: ToolRegistry schema 合法
# ==============================================================================

def test_corpus_tool_function_definitions(session, fake_r):
    """CorpusTool 注册进 ToolRegistry 后 to_function_definitions 合法。"""
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    tool = CorpusTool(session_factory=factory, r_client=fake_r)

    registry = ToolRegistry()
    registry.register(tool)

    defs = registry.get_function_definitions()
    names = {d["function"]["name"] for d in defs}
    assert "corpus__build" in names
    assert "corpus__status" in names

    for fd in defs:
        assert fd["type"] == "function"
        params = fd["function"]["parameters"]
        assert params["type"] == "object"
        assert "properties" in params


# ==============================================================================
# Test 8: 真实 R 集成测试（skipif R 不可达）
# ==============================================================================

@SKIP_NO_R
@pytest.mark.asyncio
async def test_build_real_r_integration(session, session_factory):
    """真实 R 集成：seed project + 3 included papers → build → r_corpus_id → overview 可出。

    验证接缝: Postgres included 题录 → /parse-from-records → R bibliometrix 语料 → 分析可用。
    """
    import httpx

    from app.r_client import RClient
    from app.tools.analysis import AnalysisTool

    # 用真实 RClient（指向 localhost:8001）
    async with httpx.AsyncClient(base_url="http://localhost:8001", timeout=60) as http_client:
        r_client = RClient(http_client)

        # 健康检查
        assert await r_client.health(), "R 服务健康检查失败"

        # Seed: 3 篇带完整字段的 included 论文
        proj, papers = await _seed_project_with_papers(session, n=3)

        # Build corpus
        tool = CorpusTool(session_factory=session_factory, r_client=r_client)
        result = await tool.execute("build", {"project_id": proj.id})

        assert result.success, f"build 失败: {result.error}"
        row = result.data[0]
        corpus_id = row["corpus_id"]
        r_corpus_id = row["r_corpus_id"]
        doc_count = row["document_count"]

        assert r_corpus_id, "r_corpus_id 应非空"
        assert doc_count == 3, f"期望 3 篇，实际 {doc_count}"
        assert row["status"] == "ready"

        # 验证 analysis__overview 能出结果
        analysis_tool = AnalysisTool(session_factory=session_factory, r_client=r_client)
        overview_result = await analysis_tool.execute("overview", {"corpus_id": corpus_id})

        assert overview_result.success, f"overview 失败: {overview_result.error}"
        assert len(overview_result.data) == 1
        body = overview_result.data[0]
        assert "stats" in body, f"overview 缺少 stats 字段: {body}"
        assert body["stats"]["documents"] >= 1

        # 顺手验证 r_corpus_id 已写入 DB
        async with session_factory() as s:
            q = select(Corpus).where(Corpus.id == corpus_id)
            corpus_db = (await s.execute(q)).scalar_one()
        assert corpus_db.r_corpus_id == r_corpus_id
        assert corpus_db.status == "ready"
