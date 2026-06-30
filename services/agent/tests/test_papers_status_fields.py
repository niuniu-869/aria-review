"""Task 2: papers 列表逐篇状态字段（hasPdf/ocrStatus/hasAbstract，无 N+1）。

使用 httpx.AsyncClient + ASGITransport（与 test_project_rest.py 相同模式），
避免 sync TestClient + async engine 的 event loop 冲突。
"""
import pytest
import pytest_asyncio
import httpx

from app.db import get_session
from app.main import app, get_r_client
from app.repositories import library as lib
from app.repositories import project as proj


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


@pytest.mark.asyncio
async def test_papers_list_has_status_fields(aclient):
    """REST 端点 /projects/{pid}/papers 返回含 hasPdf/ocrStatus/hasAbstract 字段且值正确。"""
    c, factory = aclient

    async with factory() as session:
        pr = await proj.create_project(session, {"name": "TestStatusProject"})

        # 有 PDF + abstract
        p1 = await lib.add_paper(session, {"title": "WithPdf", "abstract": "has abstract"})
        await lib.add_attachment(session, p1.id, sha256="abc", mineru_status="done")

        # 仅标题，无附件，无 abstract
        p2 = await lib.add_paper(session, {"title": "BareTitle"})

        await proj.add_paper_to_project(session, pr.id, p1.id)
        await proj.add_paper_to_project(session, pr.id, p2.id)
        await session.commit()
        pid = pr.id

    r = await c.get(f"/projects/{pid}/papers")
    assert r.status_code == 200
    items = r.json()["papers"]

    a = next(i for i in items if i["title"] == "WithPdf")
    assert a["hasPdf"] is True
    assert a["ocrStatus"] == "done"
    assert a["hasAbstract"] is True

    b = next(i for i in items if i["title"] == "BareTitle")
    assert b["hasPdf"] is False
    assert b["ocrStatus"] == "none"
    assert b["hasAbstract"] is False
