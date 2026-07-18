"""/events 产品埋点上报测试（0.6.1 P0 漏斗观测）。

覆盖：
  1. 登录用户 POST /events → 202；行落库，user_id/project_id/event/props 正确。
  2. 校验：event 为空 → 422；带未知字段 → 422（extra=forbid）。
  3. best-effort：project_id 指向不存在项目 → 丢弃归属（project_id=None），事件仍记，不 500。
  4. 跨租户防污染（codex P1）：用户 A 上报用户 B 拥有的 projectId → 归属被丢弃（project_id=None）。
  5. props 超限（codex P2）→ 落库为 {"_truncated": True}。
"""
from __future__ import annotations

import os
import sys

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.auth import get_current_user
from app.db import get_session
from app.main import app
from app.models import AnalyticsEvent, User
from app.repositories import user as user_repo
from app.repositories.project import create_project


@pytest_asyncio.fixture
async def events_client(session_factory):
    """AsyncClient + ASGI，共享 session_factory（同一事件循环），便于 POST 后按同一测试库读回。

    造用户 A（当前登录）+ 其项目 projA，以及另一用户 B + 其项目 projB，
    并把 get_current_user 覆盖为 A，用来走通归属校验（本人项目落库 / 他人项目丢弃归属）。
    """
    async with session_factory() as s:
        user_a = await user_repo.register_with_invite(
            s, email="a@e2e.local", password_hash="x",
            invite_code=None, invite_required=False)
        user_b = await user_repo.register_with_invite(
            s, email="b@e2e.local", password_hash="x",
            invite_code=None, invite_required=False)
        proj_a = await create_project(s, {"name": "ProjA"}, owner_id=user_a.id)
        proj_b = await create_project(s, {"name": "ProjB"}, owner_id=user_b.id)
        ids = dict(user_a=user_a.id, proj_a=proj_a.id, proj_b=proj_b.id)

    async def _test_session():
        async with session_factory() as s:
            yield s

    stub = User(id=ids["user_a"], email="a@e2e.local", role="user", status="active", credits=0)
    app.dependency_overrides[get_session] = _test_session
    app.dependency_overrides[get_current_user] = lambda: stub
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, session_factory, ids
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_current_user, None)


async def _rows(factory):
    async with factory() as s:
        return (await s.execute(select(AnalyticsEvent))).scalars().all()


@pytest.mark.asyncio
async def test_record_event_persists(events_client):
    c, factory, ids = events_client
    resp = await c.post("/events", json={
        "event": "review_view",
        "projectId": ids["proj_a"],
        "props": {"blocked": "no_included"},
    })
    assert resp.status_code == 202
    assert resp.json() == {"ok": True}

    rows = await _rows(factory)
    assert len(rows) == 1
    ev = rows[0]
    assert ev.event == "review_view"
    assert ev.user_id == ids["user_a"]
    assert ev.project_id == ids["proj_a"]
    assert ev.props == {"blocked": "no_included"}


@pytest.mark.asyncio
async def test_record_event_rejects_empty_and_extra(events_client):
    c, *_ = events_client
    assert (await c.post("/events", json={"event": ""})).status_code == 422
    assert (await c.post("/events", json={"event": "x", "junk": 1})).status_code == 422


@pytest.mark.asyncio
async def test_record_event_foreign_project_drops_attribution(events_client):
    """跨租户防污染（codex P1）：A 上报 B 的项目 → 事件仍记但 project_id 置空。"""
    c, factory, ids = events_client
    resp = await c.post("/events", json={"event": "review_view", "projectId": ids["proj_b"]})
    assert resp.status_code == 202
    rows = await _rows(factory)
    assert len(rows) == 1
    assert rows[0].user_id == ids["user_a"]
    assert rows[0].project_id is None  # 不写入他人项目


@pytest.mark.asyncio
async def test_record_event_unknown_project_drops_attribution(events_client):
    """项目不存在 → 归属丢弃，事件仍记（best-effort，不 500）。"""
    c, factory, _ids = events_client
    resp = await c.post("/events", json={"event": "review_view", "projectId": 999999})
    assert resp.status_code == 202
    rows = await _rows(factory)
    assert len(rows) == 1
    assert rows[0].project_id is None


@pytest.mark.asyncio
async def test_record_event_truncates_oversized_props(events_client):
    """props 超限 → 落库为标记，不整体拒绝（codex P2）。"""
    c, factory, ids = events_client
    big = {"x": "y" * 6000}
    resp = await c.post("/events", json={"event": "review_view", "projectId": ids["proj_a"], "props": big})
    assert resp.status_code == 202
    rows = await _rows(factory)
    assert len(rows) == 1
    assert rows[0].props == {"_truncated": True}
