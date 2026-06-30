"""AI 一图读懂(infographic)端点测试。

后台 _run_ai_job 走 BackgroundTasks + 模块级 SessionLocal。同步 TestClient 的 portal
event loop 与 pytest-asyncio fixture loop 不同,会导致 asyncpg "different loop";故改用
httpx.AsyncClient(ASGITransport)在 pytest-asyncio loop 内驱动,并把 SessionLocal patch 到
同 loop 的测试 factory,使请求 + 后台任务 + DB 全在同一 loop。
"""
import pytest
import pytest_asyncio
import httpx

from app.main import app, get_r_client
from app.db import get_session


@pytest_asyncio.fixture
async def ai_client(session_factory, fake_r):
    import app.main as _main
    _orig_session_local = _main.SessionLocal
    _main.SessionLocal = session_factory  # 后台 _run_ai_job 用模块级 SessionLocal → 指向测试库(同 loop)

    async def _test_session():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _test_session
    app.dependency_overrides[get_r_client] = lambda: fake_r

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_r_client, None)
    _main.SessionLocal = _orig_session_local


async def _project_id(client) -> int:
    res = await client.post("/projects", json={"name": "infographic project"})
    assert res.status_code == 201
    return int(res.json()["id"])


@pytest.mark.asyncio
async def test_infographic_prompt_job_uses_llm_and_persists(ai_client):
    pid = await _project_id(ai_client)
    res = await ai_client.post(
        f"/projects/{pid}/ai/jobs",
        json={
            "kind": "infographic_prompt",
            "topic": "慢性病管理",
            "text": "数字健康干预、基层医疗、自我管理和长期随访是主要综述线索。",
            "style": "academic infographic",
        },
    )
    assert res.status_code == 202
    job_id = res.json()["id"]

    job = (await ai_client.get(f"/projects/{pid}/ai/jobs/{job_id}")).json()
    assert job["status"] == "done"
    assert job["kind"] == "infographic_prompt"
    assert job["resultText"]
    assert job["request"]["image"]["hasApiKey"] is False
    assert "_image" not in job["request"]


@pytest.mark.asyncio
async def test_infographic_image_job_falls_back_to_svg_without_key(ai_client):
    pid = await _project_id(ai_client)
    res = await ai_client.post(
        f"/projects/{pid}/ai/jobs",
        json={
            "kind": "infographic_image",
            "topic": "慢性病管理",
            "imagePrompt": "A clean academic infographic about chronic disease management.",
        },
    )
    assert res.status_code == 202
    job_id = res.json()["id"]

    job = (await ai_client.get(f"/projects/{pid}/ai/jobs/{job_id}")).json()
    assert job["status"] == "done"
    assert job["kind"] == "infographic_image"
    assert job["summary"]["status"] == "prompt-only"
    assert job["summary"]["url"].endswith(".svg")

    asset = await ai_client.get(job["summary"]["url"])
    assert asset.status_code == 200
    assert "image/svg+xml" in asset.headers["content-type"]


@pytest.mark.asyncio
async def test_image_ping_requires_key(ai_client):
    res = await ai_client.post("/ai/image/ping", json={})
    assert res.status_code == 400
    assert res.json()["code"] == "IMAGE_KEY_REQUIRED"


@pytest.mark.asyncio
async def test_image_ping_rejects_body_api_key(ai_client):
    res = await ai_client.post("/ai/image/ping", json={"apiKey": "provider-credential-for-test"})

    assert res.status_code == 422
    assert res.json()["detail"][0]["type"] == "extra_forbidden"
    assert res.json()["detail"][0]["loc"] == ["body", "apiKey"]


@pytest.mark.asyncio
async def test_infographic_remote_image_fetch_does_not_forward_provider_auth(monkeypatch, tmp_path):
    import app.main as _main

    calls = []

    class FakePostResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"data": [{"url": "https://cdn.example.com/generated.png"}]}

    class FakeGetResponse:
        status_code = 200
        content = b"png"
        headers = {"content-type": "image/png"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            calls.append(("post", url, kwargs))
            return FakePostResponse()

        async def get(self, url, **kwargs):
            calls.append(("get", url, kwargs))
            return FakeGetResponse()

    monkeypatch.setattr(_main.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(_main.settings, "corpora_dir", str(tmp_path), raising=False)

    result = await _main._generate_infographic_image(
        {
            "apiKey": "provider-credential-for-test",
            "baseUrl": "https://images.example.com/v1",
            "model": "gpt-image-1",
            "size": "1024x1024",
        },
        "A clean academic infographic.",
        "Topic",
        "Grounding text",
    )

    post_call = next(call for call in calls if call[0] == "post")
    get_call = next(call for call in calls if call[0] == "get")
    assert post_call[2]["headers"]["Authorization"] == "Bearer provider-credential-for-test"
    assert "headers" not in get_call[2]
    assert result["status"] == "generated"
    assert result["url"].startswith("/ai/assets/infographics/")


@pytest.mark.asyncio
async def test_infographic_prompt_with_corpus_context_does_not_crash(ai_client, fake_r):
    """corpus_id 分支:build_review_context 返回 dict,必须序列化进 context 而非直接
    append 到 str 列表 join——否则报 'sequence item: expected str instance, dict found'。
    回归:此前测试只覆盖纯 text, 漏了 corpus 路径。"""
    cid = "11111111-1111-4111-8111-111111111111"  # conftest._VALID_CID
    fake_r.corpora[cid] = {"status": "ready", "documentCount": 2, "dbsource": "bibliocn"}
    pid = await _project_id(ai_client)
    res = await ai_client.post(
        f"/projects/{pid}/ai/jobs",
        json={
            "kind": "infographic_prompt",
            "topic": "IPO underpricing",
            "corpusId": cid,
            "style": "academic infographic",
        },
    )
    assert res.status_code == 202
    job_id = res.json()["id"]

    job = (await ai_client.get(f"/projects/{pid}/ai/jobs/{job_id}")).json()
    assert job["status"] == "done"  # 修复前: failed(dict 进入 join)
    assert job["resultText"]
