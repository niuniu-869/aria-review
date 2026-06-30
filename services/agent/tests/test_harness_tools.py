"""回归测试 — harness tools 层

覆盖：
1. ToolRegistry / BaseTool 注册与 to_function_definitions（命名格式 + 合法 schema）
2. execute_tool_calls 对坏 JSON 入参返回错误型 ToolResult 而非抛异常
3. is_write_tool 判定（tag=write 自动标记）
4. 写工具串行执行（时序断言）
5. 读工具并发（Semaphore 正常运作）
"""
from __future__ import annotations

import asyncio
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from typing import Any

from app.harness.config import EngineConfig, set_config
from app.harness.tools import BaseTool, ToolRegistry, ToolResult
from app.harness.engine import execute_tool_calls


# ======================================================================
# 测试工具定义
# ======================================================================

class ReadTool(BaseTool):
    tool_id = "search"
    tool_name = "Search Tool"
    description = "Search something"
    actions = ["query", "detail"]
    action_schemas = {
        "query": {
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
        },
        "detail": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    }
    tags = ["read"]

    async def _execute(self, action: str, params: dict, context: Any = None) -> ToolResult:
        return ToolResult(
            tool_id=self.tool_id, action=action, success=True,
            data=[{"result": "ok"}], summary="found", data_source="api",
        )


class SlowWriteTool(BaseTool):
    """写工具，每次调用记录时间戳（用于断言串行）"""
    tool_id = "slow_writer"
    tool_name = "Slow Write Tool"
    description = "Slow write"
    actions = ["save"]
    action_schemas = {}
    tags = ["write"]

    def __init__(self):
        self.start_times: list[float] = []
        self.end_times: list[float] = []

    async def _execute(self, action: str, params: dict, context: Any = None) -> ToolResult:
        self.start_times.append(time.monotonic())
        await asyncio.sleep(0.05)  # 50ms 模拟 IO
        self.end_times.append(time.monotonic())
        return ToolResult(
            tool_id=self.tool_id, action=action, success=True,
            data=[], summary="saved", data_source="db",
        )


class FastReadTool(BaseTool):
    """快速读工具（用于并发测试）"""
    tool_id = "fast_reader"
    tool_name = "Fast Reader"
    description = "Fast read"
    actions = ["get"]
    action_schemas = {}
    tags = ["read"]

    def __init__(self):
        self.concurrent_peak = 0
        self._running = 0

    async def _execute(self, action: str, params: dict, context: Any = None) -> ToolResult:
        self._running += 1
        self.concurrent_peak = max(self.concurrent_peak, self._running)
        await asyncio.sleep(0.02)
        self._running -= 1
        return ToolResult(
            tool_id=self.tool_id, action=action, success=True,
            data=[], summary="read", data_source="cache",
        )


# ======================================================================
# Fixtures
# ======================================================================

@pytest.fixture(autouse=True)
def patch_config():
    set_config(EngineConfig(tool_concurrency=8, tool_timeout=30, tool_result_max_chars=4000))
    yield
    set_config(None)


# ======================================================================
# Test 1: ToolRegistry 注册 + to_function_definitions
# ======================================================================

def test_tool_registry_register_and_list():
    """注册工具后可查找和列出"""
    registry = ToolRegistry()
    tool = ReadTool()
    registry.register(tool)

    assert registry.get("search") is tool
    schemas = registry.list_tools()
    assert len(schemas) == 1
    assert schemas[0]["tool_id"] == "search"


def test_to_function_definitions_naming():
    """to_function_definitions 命名为 {tool_id}__{action}"""
    tool = ReadTool()
    defs = tool.to_function_definitions()

    assert len(defs) == 2  # query + detail
    names = {d["function"]["name"] for d in defs}
    assert "search__query" in names
    assert "search__detail" in names


def test_to_function_definitions_valid_schema():
    """每个 function definition 是合法的 OpenAI function schema"""
    tool = ReadTool()
    defs = tool.to_function_definitions()

    for fd in defs:
        # 顶层结构
        assert fd["type"] == "function"
        func = fd["function"]
        assert "name" in func
        assert "description" in func
        assert "parameters" in func

        # parameters 是合法的 JSON Schema object
        params = func["parameters"]
        assert params["type"] == "object"
        assert "properties" in params


def test_registry_get_function_definitions():
    """ToolRegistry.get_function_definitions 正确聚合"""
    registry = ToolRegistry()
    registry.register(ReadTool())

    defs = registry.get_function_definitions()
    assert len(defs) == 2  # query + detail

    defs_filtered = registry.get_function_definitions({"search"})
    assert len(defs_filtered) == 2

    defs_empty = registry.get_function_definitions({"nonexistent"})
    assert defs_empty == []


# ======================================================================
# Test 2: execute_tool_calls 对坏 JSON 返回错误 ToolResult
# ======================================================================

@pytest.mark.asyncio
async def test_execute_tool_calls_bad_json_args():
    """坏 JSON 入参 → 返回错误型 tool 消息，不抛异常"""
    registry = ToolRegistry()
    registry.register(ReadTool())

    tool_calls = [
        {
            "id": "bad-001",
            "type": "function",
            "function": {"name": "search__query", "arguments": "this is not json {{{"},
        }
    ]

    results = await execute_tool_calls(registry=registry, tool_calls=tool_calls)
    assert len(results) == 1
    result = results[0]
    assert result["role"] == "tool"
    assert result["tool_call_id"] == "bad-001"
    # 内容应包含错误信息，不是正常结果
    assert "parse error" in result["content"].lower() or "argument" in result["content"].lower()


@pytest.mark.asyncio
async def test_execute_tool_calls_unknown_tool():
    """未知工具名 → 返回错误型 tool 消息"""
    registry = ToolRegistry()  # 空注册表

    tool_calls = [
        {
            "id": "unkn-001",
            "type": "function",
            "function": {"name": "nonexistent__action", "arguments": "{}"},
        }
    ]

    results = await execute_tool_calls(registry=registry, tool_calls=tool_calls)
    assert len(results) == 1
    assert results[0]["role"] == "tool"
    # 内容包含 error 信息
    assert "error" in results[0]["content"].lower() or "unknown" in results[0]["content"].lower()


# ======================================================================
# Test 3: is_write_tool 判定
# ======================================================================

def test_is_write_tool_from_tag():
    """tag=write 的工具自动被标记为写工具"""
    registry = ToolRegistry()
    registry.register(SlowWriteTool())

    assert registry.is_write_tool("slow_writer") is True


def test_is_write_tool_manual_mark():
    """mark_write_tools 手动标记"""
    registry = ToolRegistry()
    registry.register(ReadTool())
    assert registry.is_write_tool("search") is False

    registry.mark_write_tools("search")
    assert registry.is_write_tool("search") is True


def test_is_write_tool_via_alias():
    """通过别名解析写工具标记"""
    registry = ToolRegistry()
    registry.register(SlowWriteTool())
    registry.register_alias("writer_alias", "slow_writer")

    assert registry.is_write_tool("writer_alias") is True


# ======================================================================
# Test 4: 写工具串行执行
# ======================================================================

@pytest.mark.asyncio
async def test_write_tools_execute_serially():
    """多个写工具调用必须串行（后一个 start > 前一个 end）"""
    writer = SlowWriteTool()
    registry = ToolRegistry()
    registry.register(writer)

    # 3 个写工具调用
    tool_calls = [
        {
            "id": f"w-{i:03d}",
            "type": "function",
            "function": {"name": "slow_writer__save", "arguments": "{}"},
        }
        for i in range(3)
    ]

    await execute_tool_calls(registry=registry, tool_calls=tool_calls)

    assert len(writer.start_times) == 3
    assert len(writer.end_times) == 3

    # 验证串行：第 i+1 个的开始时间 >= 第 i 个的结束时间（有少量浮点余量）
    for i in range(len(writer.start_times) - 1):
        assert writer.start_times[i + 1] >= writer.end_times[i] - 1e-4, (
            f"写工具 {i} 和 {i+1} 不是串行执行: "
            f"end[{i}]={writer.end_times[i]:.4f}, start[{i+1}]={writer.start_times[i+1]:.4f}"
        )


# ======================================================================
# Test 5: 读工具并发（Semaphore 正常运作）
# ======================================================================

@pytest.mark.asyncio
async def test_read_tools_execute_concurrently():
    """多个读工具调用应并发（峰值并发 > 1）"""
    reader = FastReadTool()
    registry = ToolRegistry()
    registry.register(reader)

    # 4 个读工具调用
    tool_calls = [
        {
            "id": f"r-{i:03d}",
            "type": "function",
            "function": {"name": "fast_reader__get", "arguments": "{}"},
        }
        for i in range(4)
    ]

    await execute_tool_calls(registry=registry, tool_calls=tool_calls, concurrency=8)

    # 并发峰值应 > 1（所有读工具同时运行）
    assert reader.concurrent_peak > 1, (
        f"读工具未并发执行，峰值并发数={reader.concurrent_peak}"
    )


# ======================================================================
# Test 6: ToolResult.to_prompt_text
# ======================================================================

def test_tool_result_success_prompt_text():
    tr = ToolResult(
        tool_id="search", action="query", success=True,
        summary="找到 3 条结果", data_source="api",
    )
    text = tr.to_prompt_text()
    assert "search.query" in text
    assert "找到 3 条结果" in text


def test_tool_result_failure_prompt_text():
    tr = ToolResult(
        tool_id="search", action="query", success=False,
        error="Connection timeout",
    )
    text = tr.to_prompt_text()
    assert "Error" in text
    assert "Connection timeout" in text
