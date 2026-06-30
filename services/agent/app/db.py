"""DB 引擎与会话 (单一职责: 连接/会话; 模型在 models.py)。"""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    pass


# 注意：此全局 engine 指向开发/生产库（settings.database_url）。
# 测试代码请自建独立 engine（指向 settings.test_database_url），勿直接引用此全局实例，
# 否则测试会污染开发库，且无法保证测试间隔离。
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as s:
        yield s
