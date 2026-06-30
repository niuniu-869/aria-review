"""M2: materialize corpus 端点 + stale 判定集成测试。

测试覆盖：
  - POST /projects/{pid}/corpus/materialize 建新语料（调 R → ready）
  - POST 幂等：相同 included 集合第二次调用命中同一 corpus（复用，不重调 R）
  - POST included 为空时返回 422 EMPTY_INCLUDED
  - GET /projects/{pid} 的 activeCorpus 字段（ready corpus + stale 判定）
  - stale 判定：纳入集变化前后 hash 变化

复用 conftest.py 的 aclient / session_factory / fake_r fixtures。
"""
from __future__ import annotations

import pytest
import pytest_asyncio
import httpx

from app.db import get_session
from app.main import app, get_r_client
from app.repositories.library import add_paper
from app.repositories.project import add_paper_to_project, create_project, set_inclusion


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def aclient(session_factory, fake_r):
    """AsyncClient + ASGI transport；get_session → 测试库。"""
    async def _test_session():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_r_client] = lambda: fake_r
    app.dependency_overrides[get_session] = _test_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, session_factory
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 辅助：创建含 included 论文的项目
# ---------------------------------------------------------------------------

async def _make_project_with_included(factory, n_included: int = 2) -> int:
    """在测试库里建项目 + n_included 篇 included 论文，返回 project_id。"""
    async with factory() as s:
        proj = await create_project(s, {"name": f"MaterializeTest-{n_included}"})
        for i in range(n_included):
            paper = await add_paper(
                s,
                {
                    "title": f"Paper {i}",
                    "doi": f"10.1/mat{i}",
                    "csl_json": {"title": f"Paper {i}", "DOI": f"10.1/mat{i}"},
                },
            )
            pp = await add_paper_to_project(s, proj.id, paper.id)
            await set_inclusion(s, pp.id, "included")
        return proj.id


# ---------------------------------------------------------------------------
# 测试：materialize 端点
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_materialize_creates_ready_corpus(aclient):
    """materialize 成功：status=ready，返回 corpusId/rCorpusId/documentCount。"""
    c, factory = aclient
    pid = await _make_project_with_included(factory, 2)

    r = await c.post(f"/projects/{pid}/corpus/materialize")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ready"
    assert body["corpusId"] is not None
    assert body["rCorpusId"] is not None   # FakeR 返回非空 r_corpus_id
    assert body["documentCount"] == 2
    assert body["contentHash"]             # 非空 hash 字符串


@pytest.mark.asyncio
async def test_materialize_idempotent_same_hash(aclient):
    """相同 included 集合第二次调用命中同一 corpus（corpusId 相同）。"""
    c, factory = aclient
    pid = await _make_project_with_included(factory, 2)

    r1 = await c.post(f"/projects/{pid}/corpus/materialize")
    r2 = await c.post(f"/projects/{pid}/corpus/materialize")

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["corpusId"] == r2.json()["corpusId"]
    assert r1.json()["contentHash"] == r2.json()["contentHash"]


@pytest.mark.asyncio
async def test_materialize_empty_included_422(aclient):
    """included 为空时返回 422 EMPTY_INCLUDED。"""
    c, factory = aclient
    # 建空项目（无 included 论文）
    async with factory() as s:
        proj = await create_project(s, {"name": "EmptyIncluded"})
        pid = proj.id

    r = await c.post(f"/projects/{pid}/corpus/materialize")
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "EMPTY_INCLUDED"


@pytest.mark.asyncio
async def test_materialize_project_not_found_404(aclient):
    """不存在的 project 返回 404。"""
    c, _ = aclient
    r = await c.post("/projects/99999/corpus/materialize")
    assert r.status_code == 404
    assert r.json()["code"] == "PROJECT_NOT_FOUND"


# ---------------------------------------------------------------------------
# 测试：activeCorpus 内嵌于 GET /projects/{pid}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_project_active_corpus_null_before_materialize(aclient):
    """materialize 前 activeCorpus 为 null。"""
    c, factory = aclient
    async with factory() as s:
        proj = await create_project(s, {"name": "NoCorpus"})
        pid = proj.id

    r = await c.get(f"/projects/{pid}")
    assert r.status_code == 200
    body = r.json()
    assert body["activeCorpus"] is None


@pytest.mark.asyncio
async def test_get_project_active_corpus_after_materialize(aclient):
    """materialize 后 GET /projects/{pid} 的 activeCorpus 含 ready corpus 信息。"""
    c, factory = aclient
    pid = await _make_project_with_included(factory, 2)

    # 先物化
    mat = await c.post(f"/projects/{pid}/corpus/materialize")
    assert mat.status_code == 200

    # 再取详情
    r = await c.get(f"/projects/{pid}")
    assert r.status_code == 200
    body = r.json()
    ac = body["activeCorpus"]
    assert ac is not None
    assert ac["status"] == "ready"
    assert ac["corpusId"] == mat.json()["corpusId"]
    assert ac["rCorpusId"] == mat.json()["rCorpusId"]
    assert ac["documentCount"] == 2
    assert ac["stale"] is False    # 纳入集未变，不 stale


@pytest.mark.asyncio
async def test_stale_detection_after_inclusion_change(aclient):
    """纳入集变化后 stale=True。

    流程：
      1. 建项目 + 2 篇 included → materialize → stale=False
      2. 再 included 第 3 篇 → GET /projects/{pid} → stale=True
    """
    c, factory = aclient
    pid = await _make_project_with_included(factory, 2)

    # 物化
    mat_r = await c.post(f"/projects/{pid}/corpus/materialize")
    assert mat_r.status_code == 200

    # 加第 3 篇 included
    async with factory() as s:
        new_paper = await add_paper(
            s,
            {"title": "Extra Paper", "doi": "10.1/extra",
             "csl_json": {"title": "Extra Paper", "DOI": "10.1/extra"}},
        )
        pp = await add_paper_to_project(s, pid, new_paper.id)
        await set_inclusion(s, pp.id, "included")

    # GET 后 stale=True
    r = await c.get(f"/projects/{pid}")
    assert r.status_code == 200
    ac = r.json()["activeCorpus"]
    assert ac is not None
    assert ac["stale"] is True


@pytest.mark.asyncio
async def test_materialize_r_missing_corpus_id_502(session_factory):
    """codex M2-P2#2: R 返回 200 但缺 corpusId → 502 R_INVALID_RESPONSE, 不标 ready。"""
    # 自定义 fake R: parse_from_records 返回 200 但 body 无 corpusId
    class _BadR:
        async def parse_from_records(self, records: list):
            return 200, {"documentCount": len(records)}  # 缺 corpusId

    async def _test_session():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_r_client] = lambda: _BadR()
    app.dependency_overrides[get_session] = _test_session
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            pid = await _make_project_with_included(session_factory, 2)
            r = await c.post(f"/projects/{pid}/corpus/materialize")
            assert r.status_code == 502
            assert r.json()["code"] == "R_INVALID_RESPONSE"
            # active corpus 不应为 ready
            gr = await c.get(f"/projects/{pid}")
            assert gr.json()["activeCorpus"] is None
    finally:
        app.dependency_overrides.clear()
