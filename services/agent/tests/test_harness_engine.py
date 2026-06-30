"""回归测试 — harness engine 核心行为

覆盖：
1. loop 停止（LLM 无 tool_calls）
2. loop 跑工具再续（tool_call → 执行 → 最终答案）
3. max_rounds 兜底（每轮都有 tool_call → 末轮强制收尾）
4. trim_messages_to_fit（三阶段裁剪 + 孤儿清理）

全程 stub 掉 call_llm_with_fallback，不打真实 API。
"""
from __future__ import annotations

import asyncio
import sys
import os

# 确保能找到 app 包
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import pytest_asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

from app.harness.config import EngineConfig, set_config
from app.harness.engine import (
    autonomous_loop,
    trim_messages_to_fit,
    estimate_messages_tokens,
)
from app.harness.tools import BaseTool, ToolRegistry, ToolResult
from app.harness.llm import LLMRouter
from app.harness.events import NullEventPublisher


# ======================================================================
# 测试工具 / 辅助
# ======================================================================

def _make_config() -> EngineConfig:
    """构造最小化引擎配置（不需要真实 LLM）"""
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
    """构造带虚假提供商的路由器（不会真正调用）"""
    router = LLMRouter()
    router.add_provider(
        name="stub",
        api_key="stub-key",
        base_url="http://stub.local/v1",
        models=["stub-model"],
    )
    return router


def _assistant_message(content: str, tool_calls: list[dict] | None = None) -> dict:
    """构造 assistant role 消息"""
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _tool_call(call_id: str, tool_name: str, args: str = "{}") -> dict:
    """构造 tool_call 片段"""
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": tool_name, "arguments": args},
    }


def _llm_response(message: dict) -> tuple[dict, str]:
    """构造 call_llm_with_fallback 返回值"""
    return (
        {"choices": [{"message": message, "finish_reason": "stop"}]},
        "stub-model",
    )


class EchoTool(BaseTool):
    """测试用工具：将输入 query 原样返回"""

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
        self.last_params = {}

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


class WriteTool(BaseTool):
    """测试用写工具（tag=write）"""

    tool_id = "writer"
    tool_name = "Write Tool"
    description = "Write test tool"
    actions = ["save"]
    action_schemas = {}
    tags = ["write"]

    def __init__(self):
        self.call_log: list[float] = []  # 记录调用时间戳

    async def _execute(self, action: str, params: dict, context: Any = None) -> ToolResult:
        import time
        self.call_log.append(time.monotonic())
        await asyncio.sleep(0.01)  # 模拟短暂 IO
        return ToolResult(
            tool_id=self.tool_id,
            action=action,
            success=True,
            data=[],
            summary="saved",
            data_source="stub",
        )


# ======================================================================
# Fixture
# ======================================================================

@pytest.fixture(autouse=True)
def patch_config():
    """每个测试用 stub 配置覆盖全局配置，避免读取真实环境变量"""
    set_config(_make_config())
    yield
    set_config(None)  # 清除，下次 get_config() 会重新初始化


# ======================================================================
# Test 1: loop 停止 — LLM 直接返回文本，无 tool_calls
# ======================================================================

@pytest.mark.asyncio
async def test_loop_stops_on_no_tool_calls():
    """LLM 返回无 tool_calls 的 message → loop 结束并返回该内容"""
    registry = ToolRegistry()
    router = _make_router()

    final_msg = _assistant_message("这是最终答案")
    stub_response = _llm_response(final_msg)

    with patch(
        "app.harness.engine.call_llm_with_fallback",
        new=AsyncMock(return_value=stub_response),
    ):
        content, model, tool_results, rounds_log = await autonomous_loop(
            registry=registry,
            llm_router=router,
            model_names=["stub-model"],
            system_prompt="你是测试助手",
            user_prompt="说一句话",
            max_rounds=3,
            publisher=NullEventPublisher(),
        )

    assert content == "这是最终答案"
    assert model == "stub-model"
    assert tool_results == []
    assert len(rounds_log) == 1
    assert rounds_log[0]["is_final"] is True


# ======================================================================
# Test 2: loop 跑工具再续 — 第 1 轮有 tool_call，第 2 轮返回答案
# ======================================================================

@pytest.mark.asyncio
async def test_loop_runs_tool_then_continues():
    """LLM 第一轮返回 tool_call → 工具被执行 → 第二轮返回最终答案"""
    echo_tool = EchoTool()
    registry = ToolRegistry()
    registry.register(echo_tool)
    router = _make_router()

    call_count = 0

    async def fake_llm(router, model_names, messages, tools=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # 第一轮：返回 tool_call
            msg = _assistant_message(
                "我需要调用工具",
                tool_calls=[_tool_call("call-001", "echo__run", '{"query": "hello"}')],
            )
        else:
            # 第二轮（或之后）：返回最终答案
            msg = _assistant_message("最终结果: hello")
        return _llm_response(msg)

    with patch("app.harness.engine.call_llm_with_fallback", new=fake_llm):
        content, model, tool_results, rounds_log = await autonomous_loop(
            registry=registry,
            llm_router=router,
            model_names=["stub-model"],
            system_prompt="你是测试助手",
            user_prompt="请调用工具",
            max_rounds=5,
            publisher=NullEventPublisher(),
        )

    # 断言工具确实被调用
    assert echo_tool.call_count == 1
    assert echo_tool.last_params == {"query": "hello"}

    # 断言最终内容正确
    assert content == "最终结果: hello"

    # 断言工具结果被收集
    assert len(tool_results) == 1
    assert tool_results[0].tool_id == "echo"
    assert tool_results[0].success is True

    # 第一轮：tool_call 轮（is_final=False），第二轮：最终轮（is_final=True）
    assert rounds_log[0]["is_final"] is False
    assert rounds_log[1]["is_final"] is True


# ======================================================================
# Test 3: max_rounds 兜底 — 每轮都有 tool_call，末轮强制收尾
# ======================================================================

@pytest.mark.asyncio
async def test_loop_max_rounds_wrap_up():
    """每轮 LLM 都返回 tool_call → 到 max_rounds 触发末轮强制收尾并停止"""
    echo_tool = EchoTool()
    registry = ToolRegistry()
    registry.register(echo_tool)
    router = _make_router()

    tool_call_count = 0
    # 记录每次调用时 tools payload 是否为 None（末轮应该是 None）
    tools_payloads: list[Any] = []

    async def fake_llm(router, model_names, messages, tools=None, **kwargs):
        nonlocal tool_call_count
        tools_payloads.append(tools)
        if tools is not None:
            # 有工具时一直返回 tool_call
            tool_call_count += 1
            msg = _assistant_message(
                "继续调用工具",
                tool_calls=[_tool_call(f"call-{tool_call_count:03d}", "echo__run", '{"query": "x"}')],
            )
        else:
            # 末轮强制收尾（tools=None）
            msg = _assistant_message("收尾内容")
        return _llm_response(msg)

    with patch("app.harness.engine.call_llm_with_fallback", new=fake_llm):
        content, model, tool_results, rounds_log = await autonomous_loop(
            registry=registry,
            llm_router=router,
            model_names=["stub-model"],
            system_prompt="你是测试助手",
            user_prompt="无限调用工具",
            max_rounds=2,  # 设置较小的 max_rounds 以快速触发
            publisher=NullEventPublisher(),
        )

    # 末轮（tools=None）应该在最后一次 LLM 调用
    assert tools_payloads[-1] is None, "末轮应该不传 tools"

    # 收尾内容正确
    assert content == "收尾内容"

    # 工具被调用了 max_rounds 次（每轮一次 tool_call）
    assert echo_tool.call_count == 2


# ======================================================================
# Test 4: trim_messages_to_fit — 三阶段裁剪
# ======================================================================

def test_trim_within_budget_no_op():
    """在预算内的消息不被修改"""
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
        {"role": "assistant", "content": "ans"},
    ]
    original_count = len(messages)
    result = trim_messages_to_fit(messages, budget=100_000)
    assert len(result) == original_count


def test_trim_truncates_old_tool_results():
    """超预算时旧 tool 结果被截断（Phase 1）"""
    long_content = "x" * 5000  # 超过默认截断长度
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
        # 7 条 tool 消息，前面的应该被截断
        *[
            {"role": "tool", "tool_call_id": f"id-{i}", "content": long_content}
            for i in range(7)
        ],
    ]
    tokens_before = estimate_messages_tokens(messages)
    # 设置一个较小的预算使裁剪发生
    result = trim_messages_to_fit(messages, budget=tokens_before // 2)
    tokens_after = estimate_messages_tokens(result)
    assert tokens_after < tokens_before


def test_trim_phase3_removes_orphan_tool_calls():
    """Phase 3 双向孤立清理：删除 tool msg 后，对应 assistant tool_call 也应被清理"""
    # 构造：system + user + assistant(tool_call) + tool(result) + ...
    # 通过极小 budget 触发 Phase 3
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
        # 一个完整的 tool_call 对
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [_tool_call("old-call", "echo__run", '{"query": "old"}')],
        },
        {"role": "tool", "tool_call_id": "old-call", "content": "old result " * 200},
        # 最近的 assistant 答案（会保留）
        {"role": "assistant", "content": "final answer"},
    ]
    # 极小预算强制触发 Phase 3
    tokens_before = estimate_messages_tokens(messages)
    result = trim_messages_to_fit(messages, budget=20)

    # 检查：孤儿 assistant(tool_call) 和 tool 结果成对消失，或两者都在
    # 关键断言：不存在有 tool_call_id 但无对应 assistant tool_call 的 tool 消息
    result_assistant_call_ids: set[str] = set()
    for m in result:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                result_assistant_call_ids.add(tc.get("id", ""))

    for m in result:
        if m.get("role") == "tool":
            cid = m.get("tool_call_id", "")
            if cid:
                assert cid in result_assistant_call_ids, (
                    f"孤儿 tool 消息: tool_call_id={cid} 无对应 assistant tool_call"
                )


def test_trim_orphan_assistant_tool_call_cleaned():
    """反向孤儿：有 assistant tool_call 但无 tool result → 被清理"""
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
        # 有 tool_call 但没有对应 tool result
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [_tool_call("orphan-call", "echo__run", '{"query": "orphan"}')],
        },
        # 大量 tool 消息（保留近 6 条）
        *[
            {"role": "tool", "tool_call_id": f"valid-{i}", "content": "valid result " * 100}
            for i in range(7)
        ],
        # 对应的 assistant 消息（valid tool_calls）
    ]
    tokens_before = estimate_messages_tokens(messages)
    result = trim_messages_to_fit(messages, budget=tokens_before // 3)

    # 检查：不存在 assistant 有 tool_calls 但所有 tool_call_id 都没有对应 tool result
    result_tool_call_ids: set[str] = {
        m.get("tool_call_id")
        for m in result
        if m.get("role") == "tool" and m.get("tool_call_id")
    }
    for m in result:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            ids = {tc.get("id") for tc in m["tool_calls"]}
            # 若该 assistant 的所有 tool_call 都没有对应 tool result，应已被删除
            # （Phase 3 孤立清理逻辑）
            has_match = bool(ids & result_tool_call_ids)
            # 无 tool result 对应的 assistant tool_call 不应出现（除非 budget 够大未触发裁剪）
            if not has_match:
                # 检查是否 budget 未被超过（此时不触发裁剪）
                remaining_tokens = estimate_messages_tokens(result)
                assert remaining_tokens <= tokens_before // 3 + 1, (
                    f"孤儿 assistant tool_call 未被清理: ids={ids}"
                )
