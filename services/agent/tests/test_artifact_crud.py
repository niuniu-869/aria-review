"""M4 工件 CRUD + pin 端点测试。

覆盖：
  - GET /projects/{pid}/artifacts — 空列表、按 pinned 过滤
  - POST /projects/{pid}/artifacts — 创建工件
  - PATCH /projects/{pid}/artifacts/{aid} — 改 title/pinned/annotation/order
  - DELETE /projects/{pid}/artifacts/{aid} — 删除
  - 404 场景：project 不存在 / artifact 不存在
  - pin 完整工作流

与其他端点测试一致，使用 httpx.AsyncClient + ASGITransport。
"""
from __future__ import annotations

import pytest
import pytest_asyncio
import httpx

from app.db import get_session
from app.main import app, get_r_client


# ---------------------------------------------------------------------------
# Fixture：async client + 测试库
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
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

async def _create_project(aclient) -> int:
    resp = await aclient.post("/projects", json={"name": "M4测试项目"})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_artifacts_empty(aclient):
    """新项目工件列表为空。"""
    pid = await _create_project(aclient)
    resp = await aclient.get(f"/projects/{pid}/artifacts")
    assert resp.status_code == 200
    assert resp.json()["artifacts"] == []


@pytest.mark.asyncio
async def test_create_artifact(aclient):
    """POST 创建工件，返回 201 + 正确字段。"""
    pid = await _create_project(aclient)
    body = {
        "type": "review",
        "title": "IPO 文本分析综述",
        "pinned": False,
        "order": 0,
    }
    resp = await aclient.post(f"/projects/{pid}/artifacts", json=body)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["type"] == "review"
    assert data["title"] == "IPO 文本分析综述"
    assert data["pinned"] is False
    assert data["projectId"] == pid
    assert data["id"] > 0


@pytest.mark.asyncio
async def test_list_artifacts_after_create(aclient):
    """创建工件后列表非空。"""
    pid = await _create_project(aclient)
    await aclient.post(f"/projects/{pid}/artifacts", json={"title": "综述A"})
    await aclient.post(f"/projects/{pid}/artifacts", json={"title": "综述B", "pinned": True})

    resp = await aclient.get(f"/projects/{pid}/artifacts")
    assert resp.status_code == 200
    arts = resp.json()["artifacts"]
    assert len(arts) == 2


@pytest.mark.asyncio
async def test_list_artifacts_filter_pinned(aclient):
    """pinned=true 过滤只返回 pinned 工件。"""
    pid = await _create_project(aclient)
    await aclient.post(f"/projects/{pid}/artifacts", json={"title": "未 pin", "pinned": False})
    await aclient.post(f"/projects/{pid}/artifacts", json={"title": "已 pin", "pinned": True})

    resp = await aclient.get(f"/projects/{pid}/artifacts?pinned=true")
    assert resp.status_code == 200
    arts = resp.json()["artifacts"]
    assert len(arts) == 1
    assert arts[0]["title"] == "已 pin"


@pytest.mark.asyncio
async def test_patch_artifact_title_and_pin(aclient):
    """PATCH 改 title + pinned，返回更新后字段。"""
    pid = await _create_project(aclient)
    create_resp = await aclient.post(f"/projects/{pid}/artifacts", json={"title": "原标题"})
    aid = create_resp.json()["id"]

    patch_resp = await aclient.patch(
        f"/projects/{pid}/artifacts/{aid}",
        json={"title": "新标题", "pinned": True, "order": 5},
    )
    assert patch_resp.status_code == 200
    data = patch_resp.json()
    assert data["title"] == "新标题"
    assert data["pinned"] is True
    assert data["order"] == 5


@pytest.mark.asyncio
async def test_patch_artifact_annotation(aclient):
    """PATCH 改 userAnnotation。"""
    pid = await _create_project(aclient)
    create_resp = await aclient.post(f"/projects/{pid}/artifacts", json={"title": "工件"})
    aid = create_resp.json()["id"]

    patch_resp = await aclient.patch(
        f"/projects/{pid}/artifacts/{aid}",
        json={"userAnnotation": "这是用户注释"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["userAnnotation"] == "这是用户注释"


@pytest.mark.asyncio
async def test_delete_artifact(aclient):
    """DELETE 工件后列表为空。"""
    pid = await _create_project(aclient)
    create_resp = await aclient.post(f"/projects/{pid}/artifacts", json={"title": "待删"})
    aid = create_resp.json()["id"]

    del_resp = await aclient.delete(f"/projects/{pid}/artifacts/{aid}")
    assert del_resp.status_code == 204

    list_resp = await aclient.get(f"/projects/{pid}/artifacts")
    assert list_resp.json()["artifacts"] == []


@pytest.mark.asyncio
async def test_404_project_not_found(aclient):
    """项目不存在 → 404。"""
    resp = await aclient.get("/projects/99999/artifacts")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_404_artifact_not_found(aclient):
    """工件不存在 → 404。"""
    pid = await _create_project(aclient)
    resp = await aclient.patch(f"/projects/{pid}/artifacts/99999", json={"title": "x"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_pin_workflow(aclient):
    """完整 pin 工作流：创建 → pin → 过滤验证 → unpin → 过滤空。"""
    pid = await _create_project(aclient)
    create_resp = await aclient.post(f"/projects/{pid}/artifacts", json={"title": "工件X"})
    aid = create_resp.json()["id"]

    # 初始未 pin
    assert create_resp.json()["pinned"] is False

    # pin
    await aclient.patch(f"/projects/{pid}/artifacts/{aid}", json={"pinned": True})
    pinned_list = (await aclient.get(f"/projects/{pid}/artifacts?pinned=true")).json()["artifacts"]
    assert len(pinned_list) == 1

    # unpin
    await aclient.patch(f"/projects/{pid}/artifacts/{aid}", json={"pinned": False})
    pinned_list = (await aclient.get(f"/projects/{pid}/artifacts?pinned=true")).json()["artifacts"]
    assert len(pinned_list) == 0
