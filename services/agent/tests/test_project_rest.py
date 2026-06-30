"""P1-7: 领域 REST API 端点集成测试。

使用 httpx.AsyncClient + ASGITransport，与 test_agent_endpoints.py 模式一致，
避免 sync TestClient + async engine 的 event loop 冲突。
"""
from __future__ import annotations

import pytest
import pytest_asyncio
import httpx

from app.db import get_session
from app.main import app, get_r_client
from app.repositories.library import add_paper
from app.repositories.project import add_paper_to_project, create_project


# ---------------------------------------------------------------------------
# Fixture: async HTTP client，session override 指向测试库
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
# GET /projects — 空列表
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_projects_empty(aclient):
    c, _ = aclient
    r = await c.get("/projects")
    assert r.status_code == 200
    body = r.json()
    assert "projects" in body
    assert body["projects"] == []


# ---------------------------------------------------------------------------
# POST /projects — 创建
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_project_201(aclient):
    c, _ = aclient
    r = await c.post("/projects", json={"name": "SLR Test"})
    assert r.status_code == 201
    body = r.json()
    assert body["id"] is not None
    assert body["name"] == "SLR Test"
    assert "createdAt" in body


@pytest.mark.asyncio
async def test_create_project_with_all_fields(aclient):
    c, _ = aclient
    r = await c.post("/projects", json={
        "name": "Full Project",
        "researchQuestion": "What is AI?",
        "description": "A comprehensive review",
    })
    assert r.status_code == 201
    assert r.json()["name"] == "Full Project"


@pytest.mark.asyncio
async def test_create_project_then_list(aclient):
    """创建后 list 能看到新项目。"""
    c, _ = aclient
    await c.post("/projects", json={"name": "ListMe"})
    r = await c.get("/projects")
    assert r.status_code == 200
    names = [p["name"] for p in r.json()["projects"]]
    assert "ListMe" in names


@pytest.mark.asyncio
async def test_create_project_missing_name_422(aclient):
    """缺少 name 字段 Pydantic 返回 422。"""
    c, _ = aclient
    r = await c.post("/projects", json={"description": "no name"})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# GET /projects/{pid:int} — 项目详情
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_project_detail_404(aclient):
    c, _ = aclient
    r = await c.get("/projects/99999")
    assert r.status_code == 404
    assert r.json()["code"] == "PROJECT_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_project_detail_happy(aclient):
    c, _ = aclient
    create_r = await c.post("/projects", json={"name": "Detail Project"})
    pid = create_r.json()["id"]

    r = await c.get(f"/projects/{pid}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == pid
    assert body["name"] == "Detail Project"
    assert body["paperCount"] == 0
    assert body["includedCount"] == 0


# ---------------------------------------------------------------------------
# GET /projects/{pid:int}/papers — 论文列表
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_papers_empty(aclient):
    c, _ = aclient
    create_r = await c.post("/projects", json={"name": "Empty Papers"})
    pid = create_r.json()["id"]

    r = await c.get(f"/projects/{pid}/papers")
    assert r.status_code == 200
    assert r.json()["papers"] == []


@pytest.mark.asyncio
async def test_list_papers_with_data(aclient):
    """通过 DB 层预置数据后，列表端点返回正确结果。"""
    c, factory = aclient
    async with factory() as s:
        proj = await create_project(s, {"name": "WithPapers"})
        paper = await add_paper(s, {"title": "Listed Paper", "doi": "10.1/lp", "year": 2020})
        await add_paper_to_project(s, proj.id, paper.id)
        pid = proj.id
        paper_id = paper.id

    r = await c.get(f"/projects/{pid}/papers")
    assert r.status_code == 200
    items = r.json()["papers"]
    assert len(items) == 1
    assert items[0]["paperId"] == paper_id
    assert items[0]["inclusionStatus"] == "candidate"


# ---------------------------------------------------------------------------
# GET /projects/{pid:int}/papers/{paperId:int} — 文献详情
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_paper_detail_404(aclient):
    """文献未关联项目时返回 404。"""
    c, factory = aclient
    async with factory() as s:
        proj = await create_project(s, {"name": "NoLink"})
        pid = proj.id

    r = await c.get(f"/projects/{pid}/papers/99999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_paper_detail_happy(aclient):
    """正常情况返回完整 PaperDetail。"""
    c, factory = aclient
    async with factory() as s:
        proj = await create_project(s, {"name": "DetailREST"})
        paper = await add_paper(s, {
            "title": "REST Detail Paper",
            "doi": "10.1/rdp",
            "abstract": "Abstract text",
        })
        await add_paper_to_project(s, proj.id, paper.id)
        pid = proj.id
        paper_id = paper.id

    r = await c.get(f"/projects/{pid}/papers/{paper_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["paperId"] == paper_id
    assert body["inclusionStatus"] == "candidate"
    assert "tags" in body
    assert "notes" in body


# ---------------------------------------------------------------------------
# PATCH /projects/{pid:int}/papers/{paperId:int} — 纳排
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_patch_inclusion_happy(aclient):
    """PATCH 纳排状态为 included。"""
    c, factory = aclient
    async with factory() as s:
        proj = await create_project(s, {"name": "PatchTest"})
        paper = await add_paper(s, {"title": "Patch Paper", "doi": "10.1/patch", "year": 2021})
        await add_paper_to_project(s, proj.id, paper.id)
        pid = proj.id
        paper_id = paper.id

    r = await c.patch(
        f"/projects/{pid}/papers/{paper_id}",
        json={"inclusionStatus": "included", "screeningScore": 85},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["paperId"] == paper_id
    assert body["inclusionStatus"] == "included"
    assert body["screeningScore"] == 85


@pytest.mark.asyncio
async def test_patch_inclusion_excluded_with_reason(aclient):
    """PATCH 纳排状态为 excluded。"""
    c, factory = aclient
    async with factory() as s:
        proj = await create_project(s, {"name": "ExclTest"})
        paper = await add_paper(s, {"title": "Excl Paper", "doi": "10.1/excl2", "year": 2020})
        await add_paper_to_project(s, proj.id, paper.id)
        pid = proj.id
        paper_id = paper.id

    r = await c.patch(
        f"/projects/{pid}/papers/{paper_id}",
        json={"inclusionStatus": "excluded", "exclusionReason": "Out of scope"},
    )
    assert r.status_code == 200
    assert r.json()["inclusionStatus"] == "excluded"


@pytest.mark.asyncio
async def test_patch_inclusion_invalid_status_400(aclient):
    """非法 inclusionStatus 返回 400。"""
    c, factory = aclient
    async with factory() as s:
        proj = await create_project(s, {"name": "InvStatus"})
        paper = await add_paper(s, {"title": "Bad Status", "doi": "10.1/bs2"})
        await add_paper_to_project(s, proj.id, paper.id)
        pid = proj.id
        paper_id = paper.id

    r = await c.patch(
        f"/projects/{pid}/papers/{paper_id}",
        json={"inclusionStatus": "bad_status"},
    )
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_patch_inclusion_paper_not_in_project_404(aclient):
    """paper 未关联到 project 时返回 404。"""
    c, factory = aclient
    async with factory() as s:
        proj = await create_project(s, {"name": "NotLinked2"})
        pid = proj.id

    r = await c.patch(
        f"/projects/{pid}/papers/99999",
        json={"inclusionStatus": "included"},
    )
    assert r.status_code == 404
    assert r.json()["code"] == "PROJECT_PAPER_NOT_FOUND"


# ---------------------------------------------------------------------------
# 路由共存验证：int 路径与旧 str 路径共存
# ---------------------------------------------------------------------------

def test_old_string_project_id_route_still_works(client):
    """旧 str 路由 POST /projects/{project_id}/corpus 仍可正常 202。"""
    r = client.post("/projects/proj1/corpus",
                    files={"file": ("x.txt", b"some wos content")},
                    data={"dbsource": "wos"})
    assert r.status_code == 202
