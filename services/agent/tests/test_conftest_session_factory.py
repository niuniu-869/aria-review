"""Task P1-0: 测试基建 — session_factory fixture 与 client override 验证。

TDD 规程:
  1. 先跑此文件 → 预期 FAIL (fixture 不存在)
  2. conftest.py 加 session_factory + 改造 client → 再跑 → PASS
  3. 回归: test_repo_library.py / test_corpus.py 必须仍绿
"""
import pytest


@pytest.mark.asyncio
async def test_session_factory_yields_working_session(session_factory):
    """session_factory fixture 应 yield 一个可用的 async_sessionmaker；
    通过它开事务可正常读写 DB（create_project 能落库并返回带 id 的 Project）。"""
    from app.repositories.project import create_project

    async with session_factory() as s:
        p = await create_project(s, {"name": "X"})
        assert p.id is not None


def test_client_overrides_get_session(client):
    """client fixture 必须同时 override app.db.get_session，
    使新 REST 端点测试走测试库而非开发库。"""
    from app.main import app
    from app.db import get_session
    assert get_session in app.dependency_overrides
