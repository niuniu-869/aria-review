"""单步推进测试 — LoopState + step_once（P1-3 拆分的核心地基）

覆盖：
1. step_once 推进一只读工具轮 → status 仍 running、all_tool_results 增长；
   再推进一轮 LLM 返回最终文本 → status=done、final_output 非空。
2. LoopState round-trip：to_json → from_json 等价。
3. step_once 发出的事件序列含 llm_start / tools_start / round_complete。

全程 stub 掉 call_llm_with_fallback，不打真实 API。
"""
from __future__ import annotations

import asyncio
import os
import sys

# 确保能找到 app 包
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from typing import Any
from unittest.mock import patch

import pytest

from app.agent.context import AgentContext
from app.harness.config import EngineConfig, set_config
from app.harness.engine import LoopState, step_once
from app.harness.events import EventType
from app.harness.llm import LLMRouter
from app.harness.tools import BaseTool, ToolRegistry, ToolResult


# ======================================================================
# 测试工具 / 辅助
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


def _assistant_message(content: str, tool_calls: list[dict] | None = None) -> dict:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _tool_call(call_id: str, tool_name: str, args: str = "{}") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": tool_name, "arguments": args},
    }


def _llm_response(message: dict) -> tuple[dict, str]:
    return (
        {"choices": [{"message": message, "finish_reason": "stop"}]},
        "stub-model",
    )


class EchoTool(BaseTool):
    """只读 echo 工具（无 write tag）"""

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
    tags = []

    def __init__(self):
        self.call_count = 0
        self.last_params: dict = {}

    async def _execute(self, action: str, params: dict, context: Any = None) -> ToolResult:
        self.call_count += 1
        self.last_params = params
        return ToolResult(
            tool_id=self.tool_id,
            action=action,
            success=True,
            data=[{"result": params.get("query", "")}],
            summary=f"Echo: {params.get('query', '')}",
            data_source="stub",
        )


def _make_ctx(registry: ToolRegistry, router: LLMRouter, max_rounds: int = 5) -> AgentContext:
    return AgentContext(
        registry=registry,
        llm_router=router,
        model_names=["stub-model"],
        system_prompt="你是测试助手",
        tool_ids=None,
        max_rounds=max_rounds,
        wrap_up_prompt="",
    )


def _initial_state(user_prompt: str = "请调用工具") -> LoopState:
    return LoopState(
        messages=[
            {"role": "system", "content": "你是测试助手"},
            {"role": "user", "content": user_prompt},
        ],
    )


async def _noop_emit(ev: dict) -> None:
    pass


# ======================================================================
# Fixture
# ======================================================================

@pytest.fixture(autouse=True)
def patch_config():
    set_config(_make_config())
    yield
    set_config(None)


# ======================================================================
# Test 1: step_once 只读工具推进
# ======================================================================

@pytest.mark.asyncio
async def test_step_once_readonly_advances():
    """step1: 工具轮 → running + all_tool_results 增长；step2: 最终文本 → done + final_output"""
    echo_tool = EchoTool()
    registry = ToolRegistry()
    registry.register(echo_tool)
    router = _make_router()
    ctx = _make_ctx(registry, router, max_rounds=5)
    state = _initial_state()

    call_count = 0

    async def fake_llm(router, model_names, messages, tools=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            msg = _assistant_message(
                "我需要调用工具",
                tool_calls=[_tool_call("call-001", "echo__run", '{"query": "hello"}')],
            )
        else:
            msg = _assistant_message("最终结果: hello")
        return _llm_response(msg)

    with patch("app.harness.engine.call_llm_with_fallback", new=fake_llm):
        # ---- step 1: 工具轮 ----
        state = await step_once(state, ctx, emit=_noop_emit)
        assert state.status == "running"
        assert len(state.all_tool_results) == 1
        assert state.all_tool_results[0]["tool_id"] == "echo"
        assert state.all_tool_results[0]["success"] is True
        assert state.round_idx == 1
        assert echo_tool.call_count == 1
        assert echo_tool.last_params == {"query": "hello"}
        assert state.final_output is None

        # ---- step 2: 最终文本轮 ----
        state = await step_once(state, ctx, emit=_noop_emit)
        assert state.status == "done"
        assert state.final_output == "最终结果: hello"
        # 工具轮的结果应保留
        assert len(state.all_tool_results) == 1


# ======================================================================
# Test 2: LoopState round-trip
# ======================================================================

def test_loopstate_roundtrip():
    """LoopState → to_json → from_json 等价"""
    state = LoopState(
        messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "usr"},
            {
                "role": "assistant",
                "content": "thinking",
                "tool_calls": [_tool_call("c1", "echo__run", '{"query": "q"}')],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "Echo: q"},
        ],
        round_idx=2,
        tool_rounds=1,
        last_memo_idx=4,
        all_tool_results=[
            ToolResult(
                tool_id="echo", action="run", success=True,
                data=[{"result": "q"}], summary="Echo: q", data_source="stub",
            ).to_dict()
        ],
        rounds_log=[{"round": 1, "is_final": False}],
        model_used="stub-model",
        status="running",
        pending_round=None,
        final_output=None,
    )

    d = state.to_json()
    restored = LoopState.from_json(d)

    assert restored.messages == state.messages
    assert restored.round_idx == state.round_idx
    assert restored.tool_rounds == state.tool_rounds
    assert restored.last_memo_idx == state.last_memo_idx
    assert restored.all_tool_results == state.all_tool_results
    assert restored.rounds_log == state.rounds_log
    assert restored.model_used == state.model_used
    assert restored.status == state.status
    assert restored.pending_round == state.pending_round
    assert restored.final_output == state.final_output
    # 再次序列化应得到相同 dict（JSON-able 闭环）
    assert restored.to_json() == d

    # 全字段确实 JSON 可序列化
    import json
    json.loads(json.dumps(d))


# ======================================================================
# Test 3: step_once 事件序列
# ======================================================================

@pytest.mark.asyncio
async def test_step_once_emits_events():
    """emit 收到的事件序列含 llm_start / tools_start / round_complete"""
    echo_tool = EchoTool()
    registry = ToolRegistry()
    registry.register(echo_tool)
    router = _make_router()
    ctx = _make_ctx(registry, router, max_rounds=5)
    state = _initial_state()

    events: list[dict] = []

    async def collect_emit(ev: dict) -> None:
        events.append(ev)

    async def fake_llm(router, model_names, messages, tools=None, **kwargs):
        msg = _assistant_message(
            "调用工具",
            tool_calls=[_tool_call("call-001", "echo__run", '{"query": "hi"}')],
        )
        return _llm_response(msg)

    with patch("app.harness.engine.call_llm_with_fallback", new=fake_llm):
        await step_once(state, ctx, emit=collect_emit)

    types = [e["type"] for e in events]
    assert EventType.LLM_START in types
    assert EventType.TOOLS_START in types
    assert EventType.ROUND_COMPLETE in types
    # 顺序：llm_start 在 tools_start 之前，tools_start 在 round_complete 之前
    assert types.index(EventType.LLM_START) < types.index(EventType.TOOLS_START)
    assert types.index(EventType.TOOLS_START) < types.index(EventType.ROUND_COMPLETE)
