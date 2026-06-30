"""写工具确认协议 + 幂等 + step_once 挂起测试（P2-1）。

覆盖：
1. make_idempotency_key 稳定且对 args 内容敏感（键序无关）。
2. needs_confirmation 尊重 写工具标记 与 auto_confirm。
3. get_invocation / record_invocation 执行前查 + 执行后记 + 并发撞约束。
4. step_once 写工具确认挂起：[读, 写, 读] → 读先执行、写处挂起、队列保序、
   本轮 assistant 消息不写入 state.messages（延迟 append）。
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from app.agent.confirm import (
    get_invocation,
    make_idempotency_key,
    needs_confirmation,
    record_invocation,
)


# ======================================================================
# 1) idempotency key
# ======================================================================

def test_idempotency_key_stable_and_args_sensitive():
    k1 = make_idempotency_key(1, 0, "project", "set_inclusion", {"paper_id": 5, "status": "included"})
    k2 = make_idempotency_key(1, 0, "project", "set_inclusion", {"status": "included", "paper_id": 5})
    k3 = make_idempotency_key(1, 0, "project", "set_inclusion", {"paper_id": 6, "status": "included"})
    assert k1 == k2 and k1 != k3 and len(k1) == 32


# ======================================================================
# 2) needs_confirmation
# ======================================================================

def test_needs_confirmation_respects_write_and_auto_confirm():
    from app.harness.tools import ToolRegistry
    reg = ToolRegistry()
    reg.mark_write_tools("project")
    assert needs_confirmation(reg, "project", auto_confirm=False) is True
    assert needs_confirmation(reg, "project", auto_confirm=True) is False
    assert needs_confirmation(reg, "analysis", auto_confirm=False) is False


# ======================================================================
# 3) ToolInvocation 执行前查 + 执行后记
# ======================================================================

@pytest.mark.asyncio
async def test_invocation_precheck_and_record(session):
    from app.repositories.project import create_project
    from app.repositories.agent_run import create_run
    p = await create_project(session, {"name": "P"})
    r = await create_run(session, project_id=p.id)
    assert await get_invocation(session, r.id, "k1") is None
    _, c1 = await record_invocation(session, r.id, "k1", "project", "set_inclusion", {"n": 1})
    assert c1 is True
    assert await get_invocation(session, r.id, "k1") == {"n": 1}
    _, c2 = await record_invocation(session, r.id, "k1", "project", "set_inclusion", {"n": 999})
    assert c2 is False


# ======================================================================
# 4) step_once 写确认挂起（in-order suspend + delayed append）
# ======================================================================

def _make_config():
    from app.harness.config import EngineConfig
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


@pytest.fixture
def patch_config():
    from app.harness.config import set_config
    set_config(_make_config())
    yield
    set_config(None)


class _ReadTool:
    """只读工具（无 write tag）。"""
    from app.harness.tools import ToolResult as _TR

    def __init__(self, tool_id="reader"):
        self.tool_id = tool_id
        self.tool_name = tool_id
        self.description = "read"
        self.actions = ["fetch"]
        self.action_schemas = {"fetch": {"type": "object", "properties": {}}}
        self.tags = []
        self.call_count = 0

    def to_function_definitions(self):
        return [{
            "type": "function",
            "function": {"name": f"{self.tool_id}__fetch", "description": "read", "parameters": {}},
        }]

    async def execute(self, action, params, context=None):
        from app.harness.tools import ToolResult
        self.call_count += 1
        return ToolResult(tool_id=self.tool_id, action=action, success=True,
                          data=[{"v": 1}], summary="read ok", data_source="stub")


class _WriteTool:
    """写工具（write tag）。"""
    def __init__(self, tool_id="writer"):
        self.tool_id = tool_id
        self.tool_name = tool_id
        self.description = "write"
        self.actions = ["save"]
        self.action_schemas = {"save": {"type": "object", "properties": {}}}
        self.tags = ["write"]
        self.call_count = 0

    def to_function_definitions(self):
        return [{
            "type": "function",
            "function": {"name": f"{self.tool_id}__save", "description": "write", "parameters": {}},
        }]

    async def execute(self, action, params, context=None):
        from app.harness.tools import ToolResult
        self.call_count += 1
        return ToolResult(tool_id=self.tool_id, action=action, success=True,
                          data=[{"saved": True}], summary="write ok", data_source="db")


def _tc(call_id: str, name: str, args: str = "{}") -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}


@pytest.mark.asyncio
async def test_step_once_suspends_at_write_in_order(patch_config):
    """assistant 轮 = [read, write, read] → read 执行、write 处挂起、
    队列含 write+尾随 read（保序），本轮 assistant 消息未写入 state.messages。"""
    from app.agent.context import AgentContext
    from app.harness.engine import LoopState, step_once
    from app.harness.events import EventType
    from app.harness.llm import LLMRouter
    from app.harness.tools import ToolRegistry

    reader = _ReadTool("reader")
    writer = _WriteTool("writer")
    registry = ToolRegistry()
    registry.register(reader)  # 注：自定义 stub，register 仅存入字典
    registry._tools["reader"] = reader  # 直接放入，绕过 isinstance（stub 不是 BaseTool 子类）
    registry._tools["writer"] = writer
    registry.mark_write_tools("writer")

    router = LLMRouter()
    router.add_provider(name="stub", api_key="k", base_url="http://stub/v1", models=["stub-model"])

    ctx = AgentContext(
        registry=registry,
        llm_router=router,
        model_names=["stub-model"],
        system_prompt="sys",
        tool_ids=None,
        max_rounds=5,
        wrap_up_prompt="",
    )
    state = LoopState(messages=[
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "go"},
    ])
    messages_before = len(state.messages)

    assistant_msg = {
        "role": "assistant",
        "content": "calling tools",
        "tool_calls": [
            _tc("c1", "reader__fetch"),
            _tc("c2", "writer__save"),
            _tc("c3", "reader__fetch"),
        ],
    }

    async def fake_llm(router, model_names, messages, tools=None, **kwargs):
        return ({"choices": [{"message": assistant_msg, "finish_reason": "tool_calls"}]}, "stub-model")

    events: list[dict] = []

    async def emit(ev):
        events.append(ev)

    # confirm_check: 对写工具恒要求确认
    def confirm_check(call: dict) -> bool:
        return call["tool_id"] == "writer"

    with patch("app.harness.engine.call_llm_with_fallback", new=fake_llm):
        state = await step_once(state, ctx, emit=emit, confirm_check=confirm_check)

    # 状态挂起
    assert state.status == "awaiting_confirmation"
    # 第一个 read 执行了一次；尾随 read 不应在挂起前执行
    assert reader.call_count == 1
    assert writer.call_count == 0
    # 本轮 assistant 消息未写入 state.messages（延迟 append）
    assert len(state.messages) == messages_before
    assert all(m.get("content") != "calling tools" for m in state.messages)
    # pending_round 结构
    pr = state.pending_round
    assert pr is not None
    assert pr["assistant_message"]["content"] == "calling tools"
    assert len(pr["completed_tool_msgs"]) == 1
    assert pr["completed_tool_msgs"][0]["tool_call_id"] == "c1"
    # 队列保序：write 在前、尾随 read 在后
    queue = pr["queue"]
    assert [q["tool_call_id"] for q in queue] == ["c2", "c3"]
    assert queue[0]["tool_id"] == "writer" and queue[0]["needs_confirm"] is True
    assert queue[1]["tool_id"] == "reader"
    assert "idempotency_key" in queue[0]
    # 发出了 tool_confirm_required 事件
    types = [e.get("type") for e in events]
    assert "tool_confirm_required" in types
    confirm_ev = next(e for e in events if e.get("type") == "tool_confirm_required")
    assert confirm_ev["toolCallId"] == "c2"
    assert confirm_ev["toolId"] == "writer"
    assert confirm_ev["action"] == "save"


@pytest.mark.asyncio
async def test_step_once_no_confirm_check_executes_all(patch_config):
    """confirm_check=None（M1/auto_confirm）→ 写工具直接执行，与今天行为一致。"""
    from app.agent.context import AgentContext
    from app.harness.engine import LoopState, step_once
    from app.harness.llm import LLMRouter
    from app.harness.tools import ToolRegistry

    writer = _WriteTool("writer")
    registry = ToolRegistry()
    registry._tools["writer"] = writer
    registry.mark_write_tools("writer")

    router = LLMRouter()
    router.add_provider(name="stub", api_key="k", base_url="http://stub/v1", models=["stub-model"])
    ctx = AgentContext(
        registry=registry, llm_router=router, model_names=["stub-model"],
        system_prompt="sys", tool_ids=None, max_rounds=5, wrap_up_prompt="",
    )
    state = LoopState(messages=[
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "go"},
    ])

    assistant_msg = {
        "role": "assistant", "content": "writing",
        "tool_calls": [_tc("c1", "writer__save")],
    }

    async def fake_llm(router, model_names, messages, tools=None, **kwargs):
        return ({"choices": [{"message": assistant_msg, "finish_reason": "tool_calls"}]}, "stub-model")

    async def emit(ev):
        pass

    with patch("app.harness.engine.call_llm_with_fallback", new=fake_llm):
        state = await step_once(state, ctx, emit=emit, confirm_check=None)

    assert state.status == "running"
    assert writer.call_count == 1
    # assistant + tool 消息已写入
    assert any(m.get("content") == "writing" for m in state.messages)
    assert any(m.get("role") == "tool" and m.get("tool_call_id") == "c1" for m in state.messages)


# ======================================================================
# 5) apply_confirmation 引擎助手（approve/reject/多写工具再挂起）
# ======================================================================

def _build_confirm_ctx():
    """构造含 reader/writer 的 ctx + registry（run_id/session_factory 留空 → 不走幂等审计）。"""
    from app.agent.context import AgentContext
    from app.harness.llm import LLMRouter
    from app.harness.tools import ToolRegistry

    reader = _ReadTool("reader")
    writer = _WriteTool("writer")
    registry = ToolRegistry()
    registry._tools["reader"] = reader
    registry._tools["writer"] = writer
    registry.mark_write_tools("writer")
    router = LLMRouter()
    router.add_provider(name="stub", api_key="k", base_url="http://stub/v1", models=["stub-model"])
    ctx = AgentContext(
        registry=registry, llm_router=router, model_names=["stub-model"],
        system_prompt="sys", tool_ids=None, max_rounds=5, wrap_up_prompt="",
    )
    return ctx, reader, writer


def _awaiting_state(queue: list[dict], completed: list[dict] | None = None):
    """构造一个 awaiting_confirmation 的 LoopState（pending_round 含 assistant+队列）。"""
    from app.harness.engine import LoopState
    st = LoopState(messages=[
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "go"},
    ])
    st.status = "awaiting_confirmation"
    st.pending_round = {
        "assistant_message": {"role": "assistant", "content": "calling", "tool_calls": []},
        "completed_tool_msgs": completed or [],
        "queue": queue,
    }
    return st


def _qitem(call_id, tool_id, action, *, needs_confirm):
    return {
        "tool_call_id": call_id, "tool_id": tool_id, "action": action,
        "args": {}, "idempotency_key": None, "needs_confirm": needs_confirm,
    }


@pytest.mark.asyncio
async def test_apply_confirmation_approve_drains_queue(patch_config):
    """approve 队首写 + 尾随读 → 写执行、读执行、队列清空、status=running、协议落库。"""
    from app.harness.engine import apply_confirmation

    ctx, reader, writer = _build_confirm_ctx()
    queue = [
        _qitem("c2", "writer", "save", needs_confirm=True),
        _qitem("c3", "reader", "fetch", needs_confirm=False),
    ]
    completed = [{"role": "tool", "tool_call_id": "c1", "content": "read ok"}]
    st = _awaiting_state(queue, completed)

    async def emit(ev):
        pass

    st = await apply_confirmation(st, ctx, "c2", "approve", emit=emit)

    assert st.status == "running"
    assert writer.call_count == 1   # 写工具执行一次
    assert reader.call_count == 1   # 尾随读执行
    assert st.pending_round is None
    # assistant + 三条 tool（c1 既有 + c2 写 + c3 读）落进 messages
    tool_ids = [m.get("tool_call_id") for m in st.messages if m.get("role") == "tool"]
    assert tool_ids == ["c1", "c2", "c3"]
    assert st.round_idx == 1


@pytest.mark.asyncio
async def test_apply_confirmation_reject_skips_write(patch_config):
    """reject 队首写 → 写不执行、追加「用户拒绝执行」、队列清空续跑。"""
    from app.harness.engine import apply_confirmation

    ctx, reader, writer = _build_confirm_ctx()
    queue = [_qitem("c2", "writer", "save", needs_confirm=True)]
    st = _awaiting_state(queue)

    async def emit(ev):
        pass

    st = await apply_confirmation(st, ctx, "c2", "reject", emit=emit)

    assert st.status == "running"
    assert writer.call_count == 0
    rej = [m for m in st.messages if m.get("tool_call_id") == "c2"]
    assert rej and rej[0]["content"] == "用户拒绝执行"


@pytest.mark.asyncio
async def test_apply_confirmation_resuspends_at_next_write(patch_config):
    """approve 第一个写 → 队列还有第二个 needs_confirm 写 → 再次挂起、发事件。"""
    from app.harness.engine import apply_confirmation
    from app.harness.events import EventType

    ctx, reader, writer = _build_confirm_ctx()
    queue = [
        _qitem("c1", "writer", "save", needs_confirm=True),
        _qitem("c2", "writer", "save", needs_confirm=True),
    ]
    st = _awaiting_state(queue)

    events: list[dict] = []

    async def emit(ev):
        events.append(ev)

    st = await apply_confirmation(st, ctx, "c1", "approve", emit=emit)

    # 第一个写执行了；第二个写仍待确认 → 再次挂起
    assert writer.call_count == 1
    assert st.status == "awaiting_confirmation"
    assert st.pending_round is not None
    assert st.pending_round["queue"][0]["tool_call_id"] == "c2"
    # completed 累积了 c1 的执行结果
    cm = st.pending_round["completed_tool_msgs"]
    assert any(m.get("tool_call_id") == "c1" for m in cm)
    # 发出针对 c2 的 tool_confirm_required
    confirm_evs = [e for e in events if e.get("type") == EventType.TOOL_CONFIRM_REQUIRED]
    assert confirm_evs and confirm_evs[-1]["toolCallId"] == "c2"
