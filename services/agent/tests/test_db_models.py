"""Task 1.2 & 1.3: DB 引擎连接 + ORM 建表冒烟测试。

测试库使用 settings.test_database_url (带密码)。
每个测试函数内部创建自己的 async engine，避免跨 event loop 共享连接池。
"""
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings


async def test_engine_connects():
    """引擎能连上测试库并执行 select 1。"""
    engine = create_async_engine(settings.test_database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as c:
            result = await c.execute(text("select 1"))
            assert result.scalar() == 1
    finally:
        await engine.dispose()


async def test_create_all_tables():
    """能对测试库执行 create_all（11 张表）再 drop_all，无错。"""
    from app.db import Base
    import app.models  # noqa: F401 — 注册所有 ORM 映射

    engine = create_async_engine(settings.test_database_url, pool_pre_ping=True)
    try:
        async with engine.begin() as c:
            await c.run_sync(Base.metadata.create_all)

        # 验证 11 张表存在
        expected_tables = {
            "paper", "tag", "paper_tag", "note", "attachment",
            "project", "project_paper", "draft", "agent_run",
            "corpus", "corpus_paper",
        }
        async with engine.connect() as c:
            result = await c.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname = 'public'"
                )
            )
            actual = {row[0] for row in result.fetchall()}
        assert expected_tables <= actual, f"缺少表: {expected_tables - actual}"
    finally:
        # 清理
        async with engine.begin() as c:
            await c.run_sync(Base.metadata.drop_all)
        await engine.dispose()
