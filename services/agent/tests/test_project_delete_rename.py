"""F-15: DELETE /projects/{pid}（DB 级联删除）+ PATCH /projects/{pid}（重命名）。

标 real_guard 走真实 global_guard：覆盖 owner 隔离（跨租户 404，不泄露存在性）。
fixture 结构同 test_authz.py（NullPool + 测试库 create_all/drop_all）。
"""
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import auth
from app.config import settings
from app.db import Base, get_session
from app.main import app, get_r_client
from app.models import Paper, ProjectPaper
from app.repositories import session as session_repo
from app.repositories import user as user_repo
from app.repositories.project import add_paper_to_project, create_project

pytestmark = pytest.mark.real_guard


class _StubR:
    async def health(self):
        return True


@pytest_asyncio.fixture
async def proj_client():
    # NullPool：TestClient 每请求新 loop，避免连接池跨 loop 冲突。
    engine = create_async_engine(settings.test_database_url, poolclass=NullPool)
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as s:
        ua = await user_repo.register_with_invite(
            s, email="a@proj.io", password_hash=auth.hash_password("pw12345678"),
            invite_code=None, invite_required=False)
        ub = await user_repo.register_with_invite(
            s, email="b@proj.io", password_hash=auth.hash_password("pw12345678"),
            invite_code=None, invite_required=False)
        proj_a = await create_project(s, {"name": "A 的项目"}, owner_id=ua.id)
        tok_a, th_a = auth.new_session_token()
        tok_b, th_b = auth.new_session_token()
        await session_repo.create_session(s, ua.id, th_a, 14)
        await session_repo.create_session(s, ub.id, th_b, 14)
        ids = {"pa": proj_a.id, "tok_a": tok_a, "tok_b": tok_b}

    async def _override_get_session():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_r_client] = lambda: _StubR()
    app.dependency_overrides[get_session] = _override_get_session
    with TestClient(app) as c:
        yield c, ids, factory
    app.dependency_overrides.clear()

    async with engine.begin() as c:
        await c.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def test_delete_project_cascades_keeps_paper(proj_client):
    c, ids, factory = proj_client
    async with factory() as s:
        paper = Paper(title="共享文献", dedup_key="dk-del-1")
        s.add(paper)
        await s.commit()
        await s.refresh(paper)
        await add_paper_to_project(s, ids["pa"], paper.id)
        paper_id = paper.id

    c.cookies.set(auth.COOKIE_NAME, ids["tok_a"])
    r = c.delete(f"/projects/{ids['pa']}")
    assert r.status_code == 204, r.text

    r = c.get(f"/projects/{ids['pa']}")
    assert r.status_code == 404, r.text  # 删除后详情 404

    async with factory() as s:
        rows = (await s.execute(
            select(ProjectPaper).where(ProjectPaper.project_id == ids["pa"])
        )).scalars().all()
        assert rows == []  # 子表 project_paper 行被 DB 级联删除
        assert await s.get(Paper, paper_id) is not None  # 共享 Paper 题录保留


async def test_rename_project_ok(proj_client):
    c, ids, _ = proj_client
    c.cookies.set(auth.COOKIE_NAME, ids["tok_a"])
    r = c.patch(f"/projects/{ids['pa']}", json={"name": "改名后"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == ids["pa"] and body["name"] == "改名后"


async def test_rename_project_duplicate_409(proj_client):
    c, ids, _ = proj_client
    c.cookies.set(auth.COOKIE_NAME, ids["tok_a"])
    r = c.post("/projects", json={"name": "另一个项目"})
    assert r.status_code == 201, r.text
    other_id = r.json()["id"]
    r = c.patch(f"/projects/{other_id}", json={"name": "A 的项目"})
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "PROJECT_NAME_EXISTS"


async def test_rename_project_blank_name_422(proj_client):
    c, ids, _ = proj_client
    c.cookies.set(auth.COOKIE_NAME, ids["tok_a"])
    r = c.patch(f"/projects/{ids['pa']}", json={"name": "   "})
    assert r.status_code == 422, r.text  # 空白名由 pydantic 拦截


async def test_cross_tenant_delete_rename_404(proj_client):
    c, ids, _ = proj_client
    c.cookies.set(auth.COOKIE_NAME, ids["tok_b"])  # user B 操作 user A 的项目
    r = c.delete(f"/projects/{ids['pa']}")
    assert r.status_code == 404, r.text
    r = c.patch(f"/projects/{ids['pa']}", json={"name": "越权改名"})
    assert r.status_code == 404, r.text


async def test_null_owner_project_mutation_rejected(proj_client):
    """codex 二审 P1：owner_id 为空的存量项目，global_guard 读放行，
    但 DELETE/PATCH 这类不可逆写必须按严格 owner 拒绝（404 不泄露存在性）。"""
    c, ids, factory = proj_client
    async with factory() as s:
        legacy = await create_project(s, {"name": "存量公共项目"}, owner_id=None)
        legacy_id = legacy.id

    c.cookies.set(auth.COOKIE_NAME, ids["tok_a"])
    # 读（global_guard 兼容放行）应 200
    r = c.get(f"/projects/{legacy_id}")
    assert r.status_code == 200, r.text
    # 写（严格 owner）应 404
    r = c.delete(f"/projects/{legacy_id}")
    assert r.status_code == 404, r.text
    r = c.patch(f"/projects/{legacy_id}", json={"name": "劫持改名"})
    assert r.status_code == 404, r.text

    async with factory() as s:
        assert await s.get(type(legacy), legacy_id) is not None  # 未被删除
