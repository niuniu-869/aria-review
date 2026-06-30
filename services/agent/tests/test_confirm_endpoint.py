"""写工具确认协议端到端测试 (Task P2-2)。

覆盖（用真实 RunController + DB session_factory + FakeLLM 罐头 tool_call）：
- test_auto_confirm_skips_gate: autoConfirm=True 的 run，LLM 发写工具 → 不进
  awaiting_confirmation（直达 done，无 pending_round），写工具被执行。
- test_confirm_approve_resumes: autoConfirm=False，LLM 发写工具 → 进
  awaiting_confirmation → POST confirm approve → 状态推进 + 写工具效果只生效一次。
- test_confirm_reject_skips_write: 同上但 reject → 写工具不执行、协议仍完成续跑。
- test_confirm_out_of_order_409: confirm 带错误 toolCallId → 409 CONFIRM_OUT_OF_ORDER。
- test_confirm_not_awaiting_409: 对非待确认 run confirm → 409 CONFIRM_NOT_AWAITING。

集成方案沿用 test_agent_endpoints.py：httpx.AsyncClient + ASGITransport +
注入测试 RunController；patch app.harness.engine.call_llm_with_fallback stub LLM。
"""
from __future__ import annotations

import asyncio
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
from app.main import app
from app.repositories import agent_run as repo
from app.repositories.project import create_project


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
        name="stub", api_key="stub-key",
        base_url="http://stub.local/v1", models=["stub-model"],
    )
    return router


class SaveTool(BaseTool):
    """测试用写工具：每次执行计数（验证「只生效一次」）。"""

    tool_id = "saver"
    tool_name = "Save Tool"
    description = "save test tool"
    actions = ["save"]
    action_schemas = {
        "save": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        }
    }
    tags = ["write"]

    def __init__(self):
        self.call_count = 0
        self.saved: list[str] = []

    async def _execute(self, action: str, params: dict, context: Any = None) -> ToolResult:
        self.call_count += 1
        self.saved.append(params.get("value", ""))
        return ToolResult(
            tool_id=self.tool_id, action=action, success=True,
            data=[{"saved": params.get("value", "")}],
            summary=f"saved {params.get('value', '')}", data_source="db",
        )


def _build_registry(saver: SaveTool) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(saver)  # tags=["write"] → 自动标记为写工具
    return reg


def _make_build_ctx(registry: ToolRegistry, max_rounds: int = 3):
    async def build_ctx(project_id: int) -> AgentContext:
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


def _assistant(content: str, tool_calls: list[dict] | None = None) -> dict:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _tool_call(call_id: str, name: str, args: str = "{}") -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}


def _resp(message: dict) -> tuple[dict, str]:
    return ({"choices": [{"message": message, "finish_reason": "stop"}]}, "stub-model")


async def _make_project(session_factory, name: str = "ConfirmProj") -> int:
    async with session_factory() as s:
        proj = await create_project(s, {"name": name})
        return proj.id


async def _wait_task_done(ctrl: RunController, run_id: int, timeout: float = 10.0) -> None:
    """等后台 _drive/resume task 跑完（轮询，给 event loop 让步）。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.02)
        task = ctrl._tasks.get(run_id)
        if task is None or task.done():
            return


# ======================================================================
# Fixtures
# ======================================================================

@pytest.fixture(autouse=True)
def patch_config():
    set_config(_make_config())
    yield
    set_config(None)


@pytest_asyncio.fixture
async def confirm_client(session_factory):
    """AsyncClient + ASGI + 注入带写工具(saver)的测试 RunController。"""
    saver = SaveTool()
    publisher = SubscribableEventPublisher()
    registry = _build_registry(saver)
    ctrl = RunController(
        session_factory=session_factory,
        publisher=publisher,
        build_ctx=_make_build_ctx(registry),
    )

    app.state.publisher = publisher
    app.state.run_controller = ctrl

    async def _test_session():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _test_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, ctrl, session_factory, saver

    app.dependency_overrides.pop(get_session, None)


# ======================================================================
# 测试
# ======================================================================

@pytest.mark.asyncio
async def test_auto_confirm_skips_gate(confirm_client):
    """autoConfirm=True：写工具直接执行，不进 awaiting_confirmation。"""
    client, ctrl, session_factory, saver = confirm_client
    pid = await _make_project(session_factory)

    call_count = 0

    async def fake_llm(router, model_names, messages, tools=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _resp(_assistant(
                "写一下", tool_calls=[_tool_call("w-1", "saver__save", '{"value": "x"}')],
            ))
        return _resp(_assistant("完成"))

    with patch("app.harness.engine.call_llm_with_fallback", new=fake_llm):
        resp = await client.post(
            f"/projects/{pid}/agent/runs",
            json={"prompt": "存一下", "autoConfirm": True},
        )
        assert resp.status_code == 200, resp.text
        run_id = resp.json()["runId"]
        await _wait_task_done(ctrl, run_id)

    async with session_factory() as s:
        run = await repo.get_run(s, run_id)
    # 不进待确认；直达 done；无 pending_round；写工具被执行一次
    assert run.status == "done", run.status
    assert run.pending_round is None
    assert saver.call_count == 1


@pytest.mark.asyncio
async def test_confirm_approve_resumes(confirm_client):
    """autoConfirm=False：写工具挂起 → approve → 续跑到 done，写工具只生效一次。"""
    client, ctrl, session_factory, saver = confirm_client
    pid = await _make_project(session_factory)

    call_count = 0

    async def fake_llm(router, model_names, messages, tools=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _resp(_assistant(
                "需要写", tool_calls=[_tool_call("w-1", "saver__save", '{"value": "v1"}')],
            ))
        return _resp(_assistant("已完成: v1"))

    with patch("app.harness.engine.call_llm_with_fallback", new=fake_llm):
        resp = await client.post(
            f"/projects/{pid}/agent/runs",
            json={"prompt": "存 v1", "autoConfirm": False},
        )
        assert resp.status_code == 200
        run_id = resp.json()["runId"]
        # 等 _drive 跑到挂起
        await _wait_task_done(ctrl, run_id)

        async with session_factory() as s:
            run = await repo.get_run(s, run_id)
        assert run.status == "awaiting_confirmation", run.status
        assert run.pending_round is not None
        assert run.pending_round["queue"][0]["tool_call_id"] == "w-1"
        # 挂起时写工具尚未执行
        assert saver.call_count == 0

        # approve → 续跑
        cresp = await client.post(
            f"/projects/{pid}/agent/runs/{run_id}/confirm",
            json={"toolCallId": "w-1", "decision": "approve"},
        )
        assert cresp.status_code == 200, cresp.text
        assert cresp.json()["status"] in ("running", "done")

        # 等 resume task 跑完
        await _wait_task_done(ctrl, run_id)

    async with session_factory() as s:
        run = await repo.get_run(s, run_id)
    assert run.status == "done", run.status
    assert run.pending_round is None
    # 写工具效果只生效一次（approve 执行 + resume 不重复）
    assert saver.call_count == 1
    assert saver.saved == ["v1"]
    assert run.final_output == "已完成: v1"


@pytest.mark.asyncio
async def test_confirm_reject_skips_write(confirm_client):
    """autoConfirm=False：写工具挂起 → reject → 写工具不执行、协议仍完成续跑到 done。"""
    client, ctrl, session_factory, saver = confirm_client
    pid = await _make_project(session_factory)

    call_count = 0

    async def fake_llm(router, model_names, messages, tools=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _resp(_assistant(
                "想写", tool_calls=[_tool_call("w-1", "saver__save", '{"value": "v1"}')],
            ))
        return _resp(_assistant("用户拒绝后收尾"))

    with patch("app.harness.engine.call_llm_with_fallback", new=fake_llm):
        resp = await client.post(
            f"/projects/{pid}/agent/runs",
            json={"prompt": "存 v1", "autoConfirm": False},
        )
        run_id = resp.json()["runId"]
        await _wait_task_done(ctrl, run_id)

        cresp = await client.post(
            f"/projects/{pid}/agent/runs/{run_id}/confirm",
            json={"toolCallId": "w-1", "decision": "reject"},
        )
        assert cresp.status_code == 200, cresp.text
        await _wait_task_done(ctrl, run_id)

    async with session_factory() as s:
        run = await repo.get_run(s, run_id)
    assert run.status == "done", run.status
    assert run.pending_round is None
    # 拒绝 → 写工具从未执行
    assert saver.call_count == 0
    # messages 含拒绝标记
    msgs = run.messages_snapshot["messages"]
    assert any(m.get("content") == "用户拒绝执行" for m in msgs)


@pytest.mark.asyncio
async def test_confirm_out_of_order_409(confirm_client):
    """confirm 带错误 toolCallId → 409 CONFIRM_OUT_OF_ORDER。"""
    client, ctrl, session_factory, saver = confirm_client
    pid = await _make_project(session_factory)

    async def fake_llm(router, model_names, messages, tools=None, **kwargs):
        return _resp(_assistant(
            "需要写", tool_calls=[_tool_call("w-1", "saver__save", '{"value": "v1"}')],
        ))

    with patch("app.harness.engine.call_llm_with_fallback", new=fake_llm):
        resp = await client.post(
            f"/projects/{pid}/agent/runs",
            json={"prompt": "存 v1", "autoConfirm": False},
        )
        run_id = resp.json()["runId"]
        await _wait_task_done(ctrl, run_id)

        cresp = await client.post(
            f"/projects/{pid}/agent/runs/{run_id}/confirm",
            json={"toolCallId": "WRONG-ID", "decision": "approve"},
        )

    assert cresp.status_code == 409, cresp.text
    assert cresp.json()["code"] == "CONFIRM_OUT_OF_ORDER"
    # 写工具未被执行
    assert saver.call_count == 0


@pytest.mark.asyncio
async def test_confirm_not_awaiting_409(confirm_client):
    """对非待确认（已 done）的 run confirm → 409 CONFIRM_NOT_AWAITING。"""
    client, ctrl, session_factory, saver = confirm_client
    pid = await _make_project(session_factory)

    async def fake_llm(router, model_names, messages, tools=None, **kwargs):
        return _resp(_assistant("直接给答案"))  # 无工具 → 直达 done

    with patch("app.harness.engine.call_llm_with_fallback", new=fake_llm):
        resp = await client.post(
            f"/projects/{pid}/agent/runs",
            json={"prompt": "聊天", "autoConfirm": False},
        )
        run_id = resp.json()["runId"]
        await _wait_task_done(ctrl, run_id)

        cresp = await client.post(
            f"/projects/{pid}/agent/runs/{run_id}/confirm",
            json={"toolCallId": "x", "decision": "approve"},
        )

    assert cresp.status_code == 409, cresp.text
    assert cresp.json()["code"] == "CONFIRM_NOT_AWAITING"


@pytest.mark.asyncio
async def test_confirm_run_not_found_404(confirm_client):
    """confirm 不存在的 run → 404 RUN_NOT_FOUND。"""
    client, ctrl, session_factory, saver = confirm_client
    pid = await _make_project(session_factory)

    cresp = await client.post(
        f"/projects/{pid}/agent/runs/999999/confirm",
        json={"toolCallId": "x", "decision": "approve"},
    )
    assert cresp.status_code == 404, cresp.text
    assert cresp.json()["code"] == "RUN_NOT_FOUND"
