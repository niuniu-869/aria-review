"""Agent run REST + SSE 端点集成测试 (Task P1-6)。

覆盖：
- POST /projects/{pid}/agent/runs → 200 + runId + status=running
- 后台 drive 完成后 GET /runs/{rid} status in (done, failed)
- drive 完成后 list_events 事件 seq 连续
- _sse(..., seq=5) 输出含 "id: 5"

测试集成方案：
  - 用 httpx.AsyncClient + ASGITransport 取代 sync TestClient，避免 event loop 冲突。
  - 在测试里构造 RunController(session_factory fixture + FakeR + stub LLM)
    并赋给 app.state，绕过 lifespan 对真实 DB/LLM 的依赖。
  - _drive 通过 patch("app.harness.engine.call_llm_with_fallback") stub LLM。
  - 等后台 task：轮询 GET /runs/{rid} 直到 status ∈ {done, failed}，带超时。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.agent.context import AgentContext
from app.agent.run_controller import RunController
from app.db import get_session
from app.harness.config import EngineConfig, set_config
from app.harness.events import SubscribableEventPublisher
from app.harness.llm import LLMRouter
from app.harness.tools import BaseTool, ToolRegistry, ToolResult
from app.main import app, _sse
from app.repositories.project import create_project
from app.repositories import agent_run as repo


# ======================================================================
# 辅助 / 测试工具
# ======================================================================

def _make_config() -> EngineConfig:
    return EngineConfig(
        context_limit=128_000,
        context_reserve=20_000,
        tool_concurrency=8,
        tool_timeout=30,
        tool_result_max_chars=4000,
        loop_base_timeout=120,
        loop_per_round_timeout=90,
        memo_interval=8,
    )


def _make_router() -> LLMRouter:
    router = LLMRouter()
    router.add_provider(
        name="stub",
        api_key="stub-key",
        base_url="http://stub.local/v1",
        models=["stub-model"],
    )
    return router


class EchoTool(BaseTool):
    tool_id = "echo"
    tool_name = "Echo Tool"
    description = "Echo test tool"
    actions = ["run"]
    action_schemas = {
        "run": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }
    }
    tags = ["read"]

    async def _execute(self, action: str, params: dict, context: Any = None) -> ToolResult:
        return ToolResult(
            tool_id=self.tool_id,
            action=action,
            success=True,
            data=[{"result": params.get("query", "")}],
            summary=f"Echo: {params.get('query', '')}",
            data_source="stub",
        )


def _build_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(EchoTool())
    return reg


def _make_build_ctx(registry: ToolRegistry, max_rounds: int = 3):
    async def build_ctx(project_id: int, entry: str | None = None) -> AgentContext:
        return AgentContext(
            registry=registry,
            llm_router=_make_router(),
            model_names=["stub-model"],
            system_prompt="你是测试助手",
            tool_ids=None,
            max_rounds=max_rounds,
            wrap_up_prompt="收尾",
        )
    return build_ctx


def _assistant_message(content: str, tool_calls: list[dict] | None = None) -> dict:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _llm_response(message: dict) -> tuple[dict, str]:
    return (
        {"choices": [{"message": message, "finish_reason": "stop"}]},
        "stub-model",
    )


async def _make_project(session_factory, name: str = "TestProj") -> int:
    async with session_factory() as s:
        proj = await create_project(s, {"name": name})
        return proj.id


# ======================================================================
# Fixtures
# ======================================================================

@pytest.fixture(autouse=True)
def patch_config():
    set_config(_make_config())
    yield
    set_config(None)


@pytest_asyncio.fixture
async def agent_client(session_factory):
    """AsyncClient + ASGI transport + 注入测试用 RunController。

    - session_factory：测试库（隔离，每测试独立 create_all/drop_all）。
    - publisher：SubscribableEventPublisher（内存）。
    - RunController：用 session_factory 构造，不碰开发库。
    - LLM：通过 patch call_llm_with_fallback stub，测试里按需覆盖。
    """
    publisher = SubscribableEventPublisher()
    registry = _build_registry()
    ctrl = RunController(
        session_factory=session_factory,
        publisher=publisher,
        build_ctx=_make_build_ctx(registry),
    )

    # 注入 app.state（覆盖 lifespan 装配的真实控制器）
    app.state.publisher = publisher
    app.state.run_controller = ctrl

    # Override get_session → 测试库 session（不打开发库）
    async def _test_session():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _test_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, ctrl, session_factory, publisher

    app.dependency_overrides.pop(get_session, None)


# ======================================================================
# 测试: _sse id 行
# ======================================================================

def test_sse_id_line():
    """_sse(..., seq=5) 输出含 'id: 5'。"""
    out = _sse("token", {"text": "hi"}, seq=5)
    assert "id: 5\n" in out
    assert "event: token\n" in out
    assert '"text": "hi"' in out


def test_sse_no_id_line_when_seq_none():
    """seq=None 时不输出 id: 行（保持旧调用兼容）。"""
    out = _sse("done", {})
    assert "id:" not in out
    assert "event: done\n" in out


# ======================================================================
# 测试: POST /projects/{pid}/agent/runs
# ======================================================================

@pytest.mark.asyncio
async def test_create_run_returns_running(agent_client):
    """POST /projects/{pid}/agent/runs → 200 + runId + status=running。"""
    client, ctrl, session_factory, publisher = agent_client
    pid = await _make_project(session_factory)

    # stub LLM 立即返回终止消息，防止 _drive 挂起
    async def fake_llm(router, model_names, messages, tools=None, **kwargs):
        return _llm_response(_assistant_message("直接给答案"))

    with patch("app.harness.engine.call_llm_with_fallback", new=fake_llm):
        resp = await client.post(
            f"/projects/{pid}/agent/runs",
            json={"prompt": "帮我做个测试综述", "autoConfirm": False},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "runId" in data
    assert data["projectId"] == pid
    assert data["status"] == "running"


# ======================================================================
# 测试: run 最终达到 done 状态
# ======================================================================

@pytest.mark.asyncio
async def test_run_reaches_done(agent_client):
    """创建 run → 等待后台 task 完成 → status == done。"""
    client, ctrl, session_factory, publisher = agent_client
    pid = await _make_project(session_factory)

    call_count = 0

    async def fake_llm(router, model_names, messages, tools=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # 第一轮：带工具调用
            msg = _assistant_message(
                "我需要调用工具",
                tool_calls=[{
                    "id": "c-001",
                    "type": "function",
                    "function": {"name": "echo__run", "arguments": '{"query": "hello"}'},
                }],
            )
        else:
            msg = _assistant_message("最终结果: hello")
        return _llm_response(msg)

    with patch("app.harness.engine.call_llm_with_fallback", new=fake_llm):
        resp = await client.post(
            f"/projects/{pid}/agent/runs",
            json={"prompt": "调用工具并给结论"},
        )
        assert resp.status_code == 200
        run_id = resp.json()["runId"]

        # 等待后台 task 完成（最多 10 秒）
        deadline = asyncio.get_event_loop().time() + 10.0
        while asyncio.get_event_loop().time() < deadline:
            # 给 event loop 机会执行后台 task
            await asyncio.sleep(0.05)
            # 如果 task 已完成，直接退出等待
            task = ctrl._tasks.get(run_id)
            if task is None or task.done():
                break

    # 查询 run 状态
    resp2 = await client.get(f"/projects/{pid}/agent/runs/{run_id}")
    assert resp2.status_code == 200
    detail = resp2.json()
    assert detail["status"] in ("done", "failed"), f"run status: {detail['status']}"


# ======================================================================
# 测试: 事件 seq 连续
# ======================================================================

@pytest.mark.asyncio
async def test_events_persisted_with_seq(agent_client):
    """drive 完成后 list_events 返回连续 seq（1..N）。"""
    client, ctrl, session_factory, publisher = agent_client
    pid = await _make_project(session_factory)

    async def fake_llm(router, model_names, messages, tools=None, **kwargs):
        return _llm_response(_assistant_message("结论"))

    with patch("app.harness.engine.call_llm_with_fallback", new=fake_llm):
        run_id = await ctrl.create(project_id=pid, user_prompt="测试 seq")
        # 直接 await _drive（同步驱动，确保事件全部落库后再断言）
        await ctrl._drive(run_id, None)

    async with session_factory() as s:
        events = await repo.list_events(s, run_id)

    seqs = [e.seq for e in events]
    assert seqs, "至少应有一个事件"
    assert seqs == list(range(1, len(seqs) + 1)), f"seq 不连续: {seqs}"


# ======================================================================
# 测试: GET /projects/{pid}/agent/runs 列表
# ======================================================================

@pytest.mark.asyncio
async def test_list_runs(agent_client):
    """GET /projects/{pid}/agent/runs 返回已创建的 runs。"""
    client, ctrl, session_factory, publisher = agent_client
    pid = await _make_project(session_factory)

    async def fake_llm(router, model_names, messages, tools=None, **kwargs):
        return _llm_response(_assistant_message("结论"))

    with patch("app.harness.engine.call_llm_with_fallback", new=fake_llm):
        run_id = await ctrl.create(project_id=pid, user_prompt="列表测试")

    resp = await client.get(f"/projects/{pid}/agent/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert "runs" in data
    run_ids = [r["runId"] for r in data["runs"]]
    assert run_id in run_ids


# ======================================================================
# 测试: GET /projects/{pid}/agent/runs/{rid} 详情
# ======================================================================

@pytest.mark.asyncio
async def test_get_run_detail(agent_client):
    """GET /projects/{pid}/agent/runs/{rid} 返回 RunDetail。"""
    client, ctrl, session_factory, publisher = agent_client
    pid = await _make_project(session_factory)

    async def fake_llm(router, model_names, messages, tools=None, **kwargs):
        return _llm_response(_assistant_message("最终输出"))

    with patch("app.harness.engine.call_llm_with_fallback", new=fake_llm):
        run_id = await ctrl.create(project_id=pid, user_prompt="详情测试")
        await ctrl._drive(run_id, None)

    resp = await client.get(f"/projects/{pid}/agent/runs/{run_id}")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["runId"] == run_id
    assert detail["status"] == "done"
    assert detail["finalOutput"] == "最终输出"
    assert detail["prompt"] == "详情测试"  # RunDetail.prompt: 原始用户指令


@pytest.mark.asyncio
async def test_get_run_not_found(agent_client):
    """GET /projects/{pid}/agent/runs/9999 → 404。"""
    client, ctrl, session_factory, publisher = agent_client
    pid = await _make_project(session_factory)

    resp = await client.get(f"/projects/{pid}/agent/runs/9999")
    assert resp.status_code == 404


# ======================================================================
# 修复3 (codex P1-9): 端点校验
# ======================================================================

@pytest.mark.asyncio
async def test_create_run_unknown_project_404(agent_client):
    """修复3: POST runs 用不存在的 pid → 404 PROJECT_NOT_FOUND（创建前校验）。"""
    client, ctrl, session_factory, publisher = agent_client

    resp = await client.post(
        "/projects/999999/agent/runs",
        json={"prompt": "x"},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "PROJECT_NOT_FOUND"


# ======================================================================
# Task P2-4: GET /runs/{rid}/runlog 端点
# ======================================================================

@pytest.mark.asyncio
async def test_runlog_endpoint_happy(agent_client):
    """GET /projects/{pid}/agent/runs/{rid}/runlog → 200 + schema=runlog/v1。"""
    from app.agent.runlog import RUNLOG_SCHEMA_VERSION

    client, ctrl, session_factory, publisher = agent_client
    pid = await _make_project(session_factory)

    async def fake_llm(router, model_names, messages, tools=None, **kwargs):
        return _llm_response(_assistant_message("综述成品"))

    with patch("app.harness.engine.call_llm_with_fallback", new=fake_llm):
        run_id = await ctrl.create(project_id=pid, user_prompt="请综述主题 X")
        await ctrl._drive(run_id, None)

    resp = await client.get(f"/projects/{pid}/agent/runs/{run_id}/runlog")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["schema_version"] == RUNLOG_SCHEMA_VERSION
    assert body["run"]["id"] == run_id
    assert body["run"]["prompt"] == "请综述主题 X"
    # drive 跑完后应有哈希链事件 + chain_head 对齐末条 event_hash
    assert body["manifest"]["event_count"] == len(body["events"])
    if body["events"]:
        assert body["manifest"]["chain_head"] == body["events"][-1]["event_hash"]


@pytest.mark.asyncio
async def test_runlog_endpoint_run_not_found(agent_client):
    """GET runlog 用不存在的 rid → 404 RUN_NOT_FOUND。"""
    client, ctrl, session_factory, publisher = agent_client
    pid = await _make_project(session_factory)

    resp = await client.get(f"/projects/{pid}/agent/runs/999999/runlog")
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "RUN_NOT_FOUND"


@pytest.mark.asyncio
async def test_runlog_endpoint_wrong_project_404(agent_client):
    """GET runlog 用错配的 pid（run 不属于该 project）→ 404。"""
    client, ctrl, session_factory, publisher = agent_client
    pid = await _make_project(session_factory)
    other_pid = await _make_project(session_factory, name="OtherProj")

    async def fake_llm(router, model_names, messages, tools=None, **kwargs):
        return _llm_response(_assistant_message("x"))

    with patch("app.harness.engine.call_llm_with_fallback", new=fake_llm):
        run_id = await ctrl.create(project_id=pid, user_prompt="x")
        await ctrl._drive(run_id, None)

    resp = await client.get(f"/projects/{other_pid}/agent/runs/{run_id}/runlog")
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "RUN_NOT_FOUND"


# ======================================================================
# Phase 2: GET /runs/{rid}/grounding 端点（TrustCard 数据源）
# ======================================================================

@pytest.mark.asyncio
async def test_grounding_endpoint_scoreable(agent_client):
    """有 green/yellow 证据的 run → 200 + manifest.chainHead 非空 + metrics 可评分。"""
    from app.harness.engine import LoopState
    from app.repositories.agent_run import (
        append_event_chained,
        create_run,
        save_state,
    )

    client, ctrl, session_factory, publisher = agent_client
    pid = await _make_project(session_factory)

    async with session_factory() as s:
        run = await create_run(s, project_id=pid)
        run_id = run.id
        # 写真实哈希链事件 → chain_head 非空
        await append_event_chained(s, run_id, "run_start", {"a": 1})
        await append_event_chained(s, run_id, "run_complete", {"final": "x"})
        # 通过 state 单源写入 evidence_refs（含 green/yellow）+ validation_summary
        state = LoopState(
            messages=[{"role": "user", "content": "综述主题 X"}],
            evidence_refs=[
                {"match_quality": "green", "source_content_sha256": "h1"},
                {"match_quality": "yellow", "source_content_sha256": "h2"},
            ],
            validation_summary={"fabricated_citations": 0, "fabricated_spans": []},
        )
        await save_state(s, run_id, state)

    resp = await client.get(f"/projects/{pid}/agent/runs/{run_id}/grounding")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["runId"] == run_id
    # manifest 哈希链头非空（哈希链完整性恒可验证）
    assert body["manifest"]["chainHead"]
    assert body["manifest"]["eventCount"] == 2
    # metrics 字段齐全
    m = body["metrics"]
    assert m["scoreable"] is True
    assert m["insufficientEvidence"] is False
    assert m["evidenceCount"] == 2
    assert m["zeroFabricationRate"] == 1.0
    assert m["groundingAccuracy"] == 1.0
    # corpus 无附件 → provenanceHitRate 0.0（有 evidence 但未命中），非 None
    assert m["provenanceHitRate"] == 0.0
    assert "verifyHint" in body


@pytest.mark.asyncio
async def test_grounding_endpoint_not_scoreable_no_fake_100(agent_client):
    """无引用的 run → scoreable False、三率 None（不可评分，不伪装满分）。"""
    from app.repositories.agent_run import append_event_chained, create_run

    client, ctrl, session_factory, publisher = agent_client
    pid = await _make_project(session_factory)

    async with session_factory() as s:
        run = await create_run(s, project_id=pid)
        run_id = run.id
        await append_event_chained(s, run_id, "run_start", {"a": 1})

    resp = await client.get(f"/projects/{pid}/agent/runs/{run_id}/grounding")
    assert resp.status_code == 200, resp.text
    m = resp.json()["metrics"]
    assert m["scoreable"] is False
    assert m["insufficientEvidence"] is True
    # 三率均为 None（JSON null）——不可评分，绝不伪装 100%
    assert m["groundingAccuracy"] is None
    assert m["zeroFabricationRate"] is None
    assert m["provenanceHitRate"] is None
    assert m["evidenceCount"] == 0


@pytest.mark.asyncio
async def test_grounding_endpoint_run_not_found(agent_client):
    """GET grounding 用不存在的 rid → 404 RUN_NOT_FOUND。"""
    client, ctrl, session_factory, publisher = agent_client
    pid = await _make_project(session_factory)

    resp = await client.get(f"/projects/{pid}/agent/runs/999999/grounding")
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "RUN_NOT_FOUND"


@pytest.mark.asyncio
async def test_grounding_endpoint_wrong_project_404(agent_client):
    """GET grounding 用错配的 pid（run 不属于该 project）→ 404。"""
    from app.repositories.agent_run import create_run

    client, ctrl, session_factory, publisher = agent_client
    pid = await _make_project(session_factory)
    other_pid = await _make_project(session_factory, name="OtherProj")

    async with session_factory() as s:
        run = await create_run(s, project_id=pid)
        run_id = run.id

    resp = await client.get(f"/projects/{other_pid}/agent/runs/{run_id}/grounding")
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "RUN_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_run_wrong_project_404(agent_client):
    """修复3: GET run 用不属于 pid 的 rid → 404 RUN_NOT_FOUND。"""
    client, ctrl, session_factory, publisher = agent_client
    pid_a = await _make_project(session_factory, name="A")
    pid_b = await _make_project(session_factory, name="B")

    async def fake_llm(router, model_names, messages, tools=None, **kwargs):
        return _llm_response(_assistant_message("结论"))

    with patch("app.harness.engine.call_llm_with_fallback", new=fake_llm):
        run_id = await ctrl.create(project_id=pid_a, user_prompt="归属测试")

    # 用属于 A 的 run，配 B 的 pid 查 → 404
    resp = await client.get(f"/projects/{pid_b}/agent/runs/{run_id}")
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "RUN_NOT_FOUND"


@pytest.mark.asyncio
async def test_events_wrong_project_404(agent_client):
    """修复3: events 端点用不属于 pid 的 rid → SSE 开始前 404，不进无限 heartbeat。"""
    client, ctrl, session_factory, publisher = agent_client
    pid_a = await _make_project(session_factory, name="A")
    pid_b = await _make_project(session_factory, name="B")

    async def fake_llm(router, model_names, messages, tools=None, **kwargs):
        return _llm_response(_assistant_message("结论"))

    with patch("app.harness.engine.call_llm_with_fallback", new=fake_llm):
        run_id = await ctrl.create(project_id=pid_a, user_prompt="events 归属")

    resp = await client.get(f"/projects/{pid_b}/agent/runs/{run_id}/events")
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "RUN_NOT_FOUND"


@pytest.mark.asyncio
async def test_events_unknown_run_404(agent_client):
    """修复3: events 端点用不存在的 rid → 404（非 SSE heartbeat）。"""
    client, ctrl, session_factory, publisher = agent_client
    pid = await _make_project(session_factory)

    resp = await client.get(f"/projects/{pid}/agent/runs/424242/events")
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "RUN_NOT_FOUND"


# ======================================================================
# 修复2 (codex P1-4): SSE 历史事件 data 含 seq + 每帧带 id 行
# ======================================================================

@pytest.mark.asyncio
async def test_sse_history_events_carry_seq(agent_client):
    """修复2: events 端点补发的历史事件，每条 SSE data 含 seq、每帧有 id 行。

    构造一个含终态事件（run_complete）的历史 → gen 发完历史直接 return（不进
    实时 heartbeat 循环），便于完整读取响应体并断言 seq/id 契约。
    """
    client, ctrl, session_factory, publisher = agent_client
    pid = await _make_project(session_factory)

    async def fake_llm(router, model_names, messages, tools=None, **kwargs):
        return _llm_response(_assistant_message("结论"))

    # 用 _drive 跑完整一个 run，确保 DB 落了一串事件（含终态 run_complete）。
    with patch("app.harness.engine.call_llm_with_fallback", new=fake_llm):
        run_id = await ctrl.create(project_id=pid, user_prompt="历史 seq 测试")
        await ctrl._drive(run_id, None)

    # 取 DB 权威历史用于交叉校验
    async with session_factory() as s:
        events = await repo.list_events(s, run_id)
    assert events, "应有历史事件"
    expected_seqs = [e.seq for e in events]

    resp = await client.get(f"/projects/{pid}/agent/runs/{run_id}/events")
    assert resp.status_code == 200, resp.text
    body = resp.text

    # 解析 SSE 帧：每个事件块以空行分隔
    blocks = [b for b in body.split("\n\n") if b.strip() and not b.startswith(":")]
    parsed = []
    for blk in blocks:
        lines = blk.split("\n")
        frame = {}
        for ln in lines:
            if ln.startswith("id: "):
                frame["id"] = int(ln[len("id: "):])
            elif ln.startswith("event: "):
                frame["event"] = ln[len("event: "):]
            elif ln.startswith("data: "):
                frame["data"] = json.loads(ln[len("data: "):])
        if frame:
            parsed.append(frame)

    assert len(parsed) == len(events), f"帧数={len(parsed)} 应等于历史事件数={len(events)}"
    seen_seqs = []
    for frame in parsed:
        # 契约：每帧都有 id 行
        assert "id" in frame, f"帧缺少 id 行: {frame}"
        # 契约：data 里含 seq
        assert "seq" in frame["data"], f"data 缺少 seq: {frame}"
        # id 行与 data.seq 一致
        assert frame["id"] == frame["data"]["seq"], frame
        seen_seqs.append(frame["data"]["seq"])

    assert seen_seqs == expected_seqs, f"seq 序列不匹配: {seen_seqs} vs {expected_seqs}"
