"""Round 5 权限矩阵：真实全局守卫下的认证 + owner 隔离（跨租户 404）。

标 real_guard → conftest 的 _bypass_global_guard 不放行，走真实 global_guard。
"""
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import auth
from app.config import settings
from app.db import Base, get_session
from app.main import app, get_r_client
from app.repositories import session as session_repo
from app.repositories import user as user_repo
from app.repositories.project import create_project

pytestmark = pytest.mark.real_guard


class _StubR:
    async def health(self):
        return True


@pytest_asyncio.fixture
async def authz_client():
    # NullPool：TestClient 每请求新 loop，避免连接池跨 loop 冲突。
    engine = create_async_engine(settings.test_database_url, poolclass=NullPool)
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as s:
        ua = await user_repo.register_with_invite(
            s, email="a@authz.io", password_hash=auth.hash_password("pw12345678"),
            invite_code=None, invite_required=False)
        ub = await user_repo.register_with_invite(
            s, email="b@authz.io", password_hash=auth.hash_password("pw12345678"),
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
        yield c, ids
    app.dependency_overrides.clear()

    async with engine.begin() as c:
        await c.run_sync(Base.metadata.drop_all)
    await engine.dispose()


def test_unauthenticated_401(authz_client):
    c, ids = authz_client
    r = c.get(f"/projects/{ids['pa']}")
    assert r.status_code == 401, r.text  # 无 cookie


def test_owner_can_access(authz_client):
    c, ids = authz_client
    c.cookies.set(auth.COOKIE_NAME, ids["tok_a"])
    r = c.get(f"/projects/{ids['pa']}")
    assert r.status_code == 200, r.text


def test_cross_tenant_404(authz_client):
    c, ids = authz_client
    c.cookies.set(auth.COOKIE_NAME, ids["tok_b"])
    r = c.get(f"/projects/{ids['pa']}")
    assert r.status_code == 404, r.text  # user B 访问 user A 的 project → 404（不泄露存在性）


def test_healthz_exempt(authz_client):
    c, _ = authz_client
    r = c.get("/healthz")
    assert r.status_code == 200, r.text  # 豁免路径无需登录


# --------------------------- F-14: 全局守卫 CSRF Origin 校验 ---------------------------


def test_csrf_evil_origin_rejected(authz_client):
    c, ids = authz_client
    c.cookies.set(auth.COOKIE_NAME, ids["tok_a"])
    r = c.post("/projects", json={"name": "CSRF-a"},
               headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "CSRF_REJECTED"


def test_csrf_trusted_origin_passes(authz_client):
    c, ids = authz_client
    c.cookies.set(auth.COOKIE_NAME, ids["tok_a"])
    # settings.cors_origins 默认含 http://localhost:8080（trusted_origins 未配时回退到它）
    r = c.post("/projects", json={"name": "CSRF-b"},
               headers={"Origin": "http://localhost:8080"})
    assert r.status_code == 201, r.text


def test_csrf_no_origin_no_referer_passes(authz_client):
    c, ids = authz_client
    c.cookies.set(auth.COOKIE_NAME, ids["tok_a"])
    r = c.post("/projects", json={"name": "CSRF-c"})  # curl/脚本语义：无来源头，放行
    assert r.status_code == 201, r.text


def test_csrf_referer_fallback_rejected(authz_client):
    c, ids = authz_client
    c.cookies.set(auth.COOKIE_NAME, ids["tok_a"])
    r = c.post("/projects", json={"name": "CSRF-d"},
               headers={"Referer": "https://evil.example.com/page"})
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "CSRF_REJECTED"


def test_csrf_auth_login_exempt(authz_client):
    c, _ = authz_client
    # /auth/ 前缀豁免（login/register 是文档化的 CSRF 豁免入口）：evil Origin 不拦截
    r = c.post("/auth/login", json={"email": "a@authz.io", "password": "pw12345678"},
               headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 200, r.text


# --------------------------- F-23: 422 中文化 ---------------------------


def test_validation_error_chinese(authz_client):
    c, _ = authz_client
    r = c.post("/auth/register", json={"email": "x@y.io", "password": "abc"})
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "密码" in body["message"] and "8" in body["message"]
    assert body["details"]["errors"]


def test_public_stats_exempt(authz_client):
    c, _ = authz_client
    r = c.get("/public/stats")  # 无 cookie：authz 豁免的公开着陆页统计
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"papers", "blockAnchors", "dois"}
    # 端点真跑（含 json_array_length 聚合）；空测试库各计数为 0 且均为非负整数
    assert all(isinstance(body[k], int) and body[k] >= 0 for k in body)
