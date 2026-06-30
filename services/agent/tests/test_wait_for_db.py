from scripts.wait_for_db import _asyncpg_url


def test_asyncpg_url_accepts_plain_postgres_url():
    assert _asyncpg_url("postgresql://u:p@host:5432/db") == "postgresql://u:p@host:5432/db"


def test_asyncpg_url_converts_sqlalchemy_async_scheme():
    assert (
        _asyncpg_url("postgresql+asyncpg://u:p@host:5432/db")
        == "postgresql://u:p@host:5432/db"
    )
