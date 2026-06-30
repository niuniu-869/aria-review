"""RunController + agent_run 仓储 + registry_factory 集成测试 (Task P1-5)。

覆盖：
- create_run/get_run/save_state/next_seq/append_event/list_events/list_runs 仓储
- RunController.create 落库 + 初始 messages 入 state
- RunController._drive 只读工具跑到 done：status/事件 seq 连续/final_output
- publisher 订阅收到带 seq 的事件
- start 防重复 task
- _drive 异常 → status=failed

全程 stub call_llm_with_fallback，不打真实 API；用 session_factory fixture（真实测试库）。
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any
from unittest.mock import patch

import pytest

# 确保能找到 app 包
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.agent.context import AgentContext
from app.agent.run_controller import RunController
from app.harness.config import EngineConfig, set_config
from app.harness.events import SubscribableEventPublisher
from app.harness.llm import LLMRouter
from app.harness.tools import BaseTool, ToolRegistry, ToolResult
from app.repositories import agent_run as repo
from app.repositories.project import create_project


async def _new_project(session_factory, name: str = "T") -> int:
    """建一个真实 Project（agent_run.project_id 有外键约束），返回其 id。"""
    async with session_factory() as s:
        proj = await create_project(s, {"name": name})
        return proj.id


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
    """测试用只读工具：原样回显 query。"""

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

    def __init__(self):
        self.call_count = 0

    async def _execute(self, action: str, params: dict, context: Any = None) -> ToolResult:
        self.call_count += 1
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


def _make_build_ctx(registry: ToolRegistry, max_rounds: int = 5):
    """返回一个 build_ctx 协程工厂：忽略 project_id，返回固定 AgentContext。"""

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


# ======================================================================
# Fixtures
# ======================================================================

@pytest.fixture(autouse=True)
def patch_config():
    set_config(_make_config())
    yield
    set_config(None)


# ======================================================================
# 仓储测试
# ======================================================================

@pytest.mark.asyncio
async def test_create_persists_run(session_factory):
    pid = await _new_project(session_factory)
    async with session_factory() as s:
        run = await repo.create_run(s, project_id=pid, plan="测试计划")
    run_id = run.id
    assert run_id is not None
    assert run.status == "running"

    async with session_factory() as s:
        got = await repo.get_run(s, run_id)
    assert got is not None
    assert got.id == run_id
    assert got.status == "running"
    assert got.plan == "测试计划"


@pytest.mark.asyncio
async def test_next_seq_and_append_events(session_factory):
    pid = await _new_project(session_factory)
    async with session_factory() as s:
        run = await repo.create_run(s, project_id=pid)
    run_id = run.id

    async with session_factory() as s:
        seq1 = await repo.next_seq(s, run_id)
        assert seq1 == 1
        await repo.append_event(s, run_id, seq1, "llm_start", {"type": "llm_start", "round": 1})

    async with session_factory() as s:
        seq2 = await repo.next_seq(s, run_id)
        assert seq2 == 2
        await repo.append_event(s, run_id, seq2, "round_complete", {"type": "round_complete"})

    async with session_factory() as s:
        events = await repo.list_events(s, run_id)
    assert [e.seq for e in events] == [1, 2]
    # after_seq 过滤
    async with session_factory() as s:
        tail = await repo.list_events(s, run_id, after_seq=1)
    assert [e.seq for e in tail] == [2]


@pytest.mark.asyncio
async def test_list_runs_desc(session_factory):
    pid = await _new_project(session_factory, "A")
    other = await _new_project(session_factory, "B")
    async with session_factory() as s:
        r1 = await repo.create_run(s, project_id=pid)
        r2 = await repo.create_run(s, project_id=pid)
        await repo.create_run(s, project_id=other)  # 别的 project
    async with session_factory() as s:
        runs = await repo.list_runs(s, project_id=pid)
    ids = [r.id for r in runs]
    assert set(ids) == {r1.id, r2.id}
    # created_at 倒序：后建的 r2 在前（id 也更大）
    assert ids[0] == r2.id


# ======================================================================
# RunController 测试
# ======================================================================

@pytest.mark.asyncio
async def test_controller_create_persists_run(session_factory):
    registry = _build_registry()
    publisher = SubscribableEventPublisher()
    controller = RunController(
        session_factory=session_factory,
        publisher=publisher,
        build_ctx=_make_build_ctx(registry),
    )
    pid = await _new_project(session_factory)
    run_id = await controller.create(project_id=pid, user_prompt="请帮我综述")

    async with session_factory() as s:
        run = await repo.get_run(s, run_id)
    assert run is not None
    assert run.status == "running"
    # 初始 messages：system + user（messages_snapshot 现存完整 LoopState 快照 dict）
    assert run.messages_snapshot is not None
    snapshot_messages = run.messages_snapshot["messages"]
    roles = [m["role"] for m in snapshot_messages]
    assert roles == ["system", "user"]
    assert snapshot_messages[1]["content"] == "请帮我综述"


@pytest.mark.asyncio
async def test_drive_readonly_to_done(session_factory):
    registry = _build_registry()
    publisher = SubscribableEventPublisher()
    controller = RunController(
        session_factory=session_factory,
        publisher=publisher,
        build_ctx=_make_build_ctx(registry),
    )
    pid = await _new_project(session_factory)
    run_id = await controller.create(project_id=pid, user_prompt="请调用工具")

    call_count = 0

    async def fake_llm(router, model_names, messages, tools=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            msg = _assistant_message(
                "我需要调用工具",
                tool_calls=[_tool_call("c-001", "echo__run", '{"query": "hello"}')],
            )
        else:
            msg = _assistant_message("最终结果: hello")
        return _llm_response(msg)

    with patch("app.harness.engine.call_llm_with_fallback", new=fake_llm):
        await controller._drive(run_id, None)

    async with session_factory() as s:
        run = await repo.get_run(s, run_id)
        events = await repo.list_events(s, run_id)

    assert run.status == "done"
    assert run.final_output == "最终结果: hello"
    # 事件 seq 连续 1..N
    seqs = [e.seq for e in events]
    assert seqs == list(range(1, len(seqs) + 1))
    assert len(seqs) >= 1
    # cursor == round_idx（已推进 ≥1 轮）
    assert run.cursor >= 1


@pytest.mark.asyncio
async def test_publisher_receives_events(session_factory):
    registry = _build_registry()
    publisher = SubscribableEventPublisher()
    controller = RunController(
        session_factory=session_factory,
        publisher=publisher,
        build_ctx=_make_build_ctx(registry),
    )
    pid = await _new_project(session_factory)
    run_id = await controller.create(project_id=pid, user_prompt="说一句话")
    channel = controller.channel(run_id)
    q = publisher.subscribe(channel)

    async def fake_llm(router, model_names, messages, tools=None, **kwargs):
        return _llm_response(_assistant_message("直接给答案"))

    with patch("app.harness.engine.call_llm_with_fallback", new=fake_llm):
        await controller._drive(run_id, None)

    received: list[dict] = []
    while not q.empty():
        received.append(q.get_nowait())

    assert received, "订阅者应至少收到一个事件"
    # 每个事件都带 seq
    for ev in received:
        assert "seq" in ev
    seqs = [ev["seq"] for ev in received]
    assert seqs == sorted(seqs)
    assert seqs[0] == 1


@pytest.mark.asyncio
async def test_start_no_duplicate_task(session_factory):
    registry = _build_registry()
    publisher = SubscribableEventPublisher()
    controller = RunController(
        session_factory=session_factory,
        publisher=publisher,
        build_ctx=_make_build_ctx(registry),
    )
    pid = await _new_project(session_factory)
    run_id = await controller.create(project_id=pid, user_prompt="hi")

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_llm(router, model_names, messages, tools=None, **kwargs):
        started.set()
        await release.wait()
        return _llm_response(_assistant_message("done"))

    with patch("app.harness.engine.call_llm_with_fallback", new=slow_llm):
        controller.start(run_id)
        await started.wait()
        first_task = controller._tasks.get(run_id)
        # 第二次 start：不应起新 task
        controller.start(run_id)
        second_task = controller._tasks.get(run_id)
        assert first_task is second_task
        release.set()
        await first_task

    assert run_id not in controller._tasks  # done callback 清理


@pytest.mark.asyncio
async def test_drive_exception_marks_failed(session_factory):
    registry = _build_registry()
    publisher = SubscribableEventPublisher()

    async def boom_build_ctx(project_id: int) -> AgentContext:
        raise RuntimeError("build_ctx 故意炸")

    controller = RunController(
        session_factory=session_factory,
        publisher=publisher,
        build_ctx=boom_build_ctx,
    )
    pid = await _new_project(session_factory)
    run_id = await controller.create(project_id=pid, user_prompt="hi")

    # _drive 不应向外抛（内部 try/except 标 failed）
    await controller._drive(run_id, None)

    async with session_factory() as s:
        run = await repo.get_run(s, run_id)
    assert run.status == "failed"


@pytest.mark.asyncio
async def test_recover_orphans_marks_failed_and_emits_terminal(session_factory):
    """codex P1：recover_orphans 把残留 running 的 orphan run 标 failed 时，必须**同时**
    追加一条终态事件（type 在 SSE terminal_types 内，如 error），否则连上该 orphan run 的
    SSE 历史无终态事件、又无活跃 task → 流永久 heartbeat 空等。

    断言：① status 变 failed；② 该 run 现在有一条终态事件（type ∈ {run_complete,error,
    cancelled}）；③ 终态事件经哈希链落库（prev_hash/event_hash 链完整、不断）。"""
    from app.harness.events import EventType

    registry = _build_registry()
    publisher = SubscribableEventPublisher()
    controller = RunController(
        session_factory=session_factory,
        publisher=publisher,
        build_ctx=_make_build_ctx(registry),
    )
    pid = await _new_project(session_factory)
    # 造一个残留 running 的 orphan run（create_run 默认 status=running，无活跃 task）。
    async with session_factory() as s:
        run = await repo.create_run(s, project_id=pid)
        orphan_id = run.id

    async with session_factory() as s:
        n = await controller.recover_orphans(s)
    assert n >= 1

    async with session_factory() as s:
        run = await repo.get_run(s, orphan_id)
        events = await repo.list_events(s, orphan_id)

    # ① status 变 failed
    assert run.status == "failed", run.status

    # ② 有终态事件（SSE 据此收敛）
    terminal_types = {EventType.RUN_COMPLETE, EventType.ERROR, EventType.CANCELLED}
    terminal_events = [e for e in events if e.type in terminal_types]
    assert terminal_events, f"recover_orphans 应追加终态事件，实有事件 {[e.type for e in events]}"

    # ③ 哈希链完整：seq 连续、每条 event_hash 非空、prev_hash 串得上一条。
    events_sorted = sorted(events, key=lambda e: e.seq)
    assert [e.seq for e in events_sorted] == list(range(1, len(events_sorted) + 1))
    prev = ""
    for e in events_sorted:
        assert e.event_hash, f"事件 seq={e.seq} 缺 event_hash（哈希链断）"
        assert e.prev_hash == prev, f"事件 seq={e.seq} prev_hash 不匹配（哈希链断）"
        prev = e.event_hash
