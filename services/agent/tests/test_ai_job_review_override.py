"""AI 综述任务(review kind)的 LLM override 透传回归测试。

锁定链路:create_ai_job → _run_ai_job(override=_llm_override(request)) → run_review(override=override)。
此前 _run_ai_job 硬编码 override=None,使 AiJob 综述路径忽略用户在请求头自带的 key/模型,
与 agent-run 路径(早已透传 _llm_override)不一致 —— 真实漏 key 缺陷(commit 298d4b8 链路修复后补此守护)。

本测试断言:
  1) 带 X-LLM-Key 头创建 review job → run_review 收到的 override 即该 key 构造的 OverrideLLMConfig;
  2) 无任何 key(且 env key 清空)→ override 为 None(回归安全,与旧 override=None 行为等价)。
位置参数顺序(override 夹在 llm 与 r 之间)亦被本测试间接锁定:若错序,run_review 收不到正确 override。

后台 _run_ai_job 走 BackgroundTasks + 模块级 SessionLocal;沿用 test_ai_infographic 的
httpx.AsyncClient(ASGITransport)同 loop 驱动范式,避免 asyncpg "different loop"。
"""
import httpx
import pytest
import pytest_asyncio

import app.config as cfg
from app.db import get_session
from app.main import app, get_r_client


@pytest_asyncio.fixture
async def ai_client(session_factory, fake_r):
    import app.main as _main
    _orig_session_local = _main.SessionLocal
    _main.SessionLocal = session_factory  # 后台 _run_ai_job 用模块级 SessionLocal → 测试库(同 loop)

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


# run_review 完成路径需要的最小返回形状(_run_ai_job 读 error/validation_summary/provenance_map/review_md/evidence_refs)
_FAKE_REVIEW_RESULT = {
    "error": None,
    "review_md": "## 综述\n\n要点[[anchor:a1_0_0__occ0]][1][[/anchor]]。",
    "validation_summary": {"fabricated_citations": 0},
    "provenance_map": {},
    "evidence_refs": [],
}


async def _project_id(client) -> int:
    res = await client.post("/projects", json={"name": "override review project"})
    assert res.status_code == 201
    return int(res.json()["id"])


def _patch_review_pipeline(monkeypatch, captured: dict):
    """patch load_project_corpus(非空→走 provenance 分支) + run_review(捕获 override)。"""

    async def _fake_load_corpus(s, pid):
        return (["# 文献A\n\n图神经网络用于引文网络嵌入。"], [{"paper_id": 1, "title": "A"}], [])

    async def _fake_run_review(*args, **kwargs):
        captured["override"] = kwargs.get("override")
        return dict(_FAKE_REVIEW_RESULT)

    monkeypatch.setattr("app.main.load_project_corpus", _fake_load_corpus)
    monkeypatch.setattr("app.main.run_review", _fake_run_review)


@pytest.mark.asyncio
async def test_review_job_threads_override_from_request_key(ai_client, monkeypatch):
    """带 X-LLM-Key → run_review 必须收到非空 override 且 api_key == 头中 key(原 bug: 硬编码 None)。"""
    captured: dict = {}
    _patch_review_pipeline(monkeypatch, captured)

    pid = await _project_id(ai_client)
    res = await ai_client.post(
        f"/projects/{pid}/ai/jobs",
        json={"kind": "review", "type": "undergrad", "topic": "图神经网络引文网络嵌入"},
        headers={"X-LLM-Key": "test-api-key-override-abc123"},
    )
    assert res.status_code == 202
    job_id = res.json()["id"]

    job = (await ai_client.get(f"/projects/{pid}/ai/jobs/{job_id}")).json()
    assert job["status"] == "done", job

    override = captured.get("override")
    assert override is not None, "带 X-LLM-Key 时 run_review 必须收到非空 override(回归: 勿改回 override=None)"
    assert override.api_key == "test-api-key-override-abc123"


@pytest.mark.asyncio
async def test_review_job_override_none_without_any_key(ai_client, monkeypatch):
    """无请求头 key 且 env key 清空 → override 为 None(回归安全,等价旧行为)。"""
    monkeypatch.setattr(cfg.settings, "deepseek_api_key", "", raising=False)  # 清掉 .env 真实 key 的回退
    captured: dict = {}
    _patch_review_pipeline(monkeypatch, captured)

    pid = await _project_id(ai_client)
    res = await ai_client.post(
        f"/projects/{pid}/ai/jobs",
        json={"kind": "review", "type": "undergrad", "topic": "图神经网络引文网络嵌入"},
    )
    assert res.status_code == 202
    job_id = res.json()["id"]

    job = (await ai_client.get(f"/projects/{pid}/ai/jobs/{job_id}")).json()
    assert job["status"] == "done", job
    assert captured.get("override") is None
