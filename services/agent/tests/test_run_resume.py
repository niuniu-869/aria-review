"""运行生命周期测试（P3-1）：trim Phase3 / pause-resume / cancel SSE 终态 / resume 不重放写。

覆盖：
1. trim Phase3 按完整 assistant 轮删 —— 删最早的「带 tool_calls 的 assistant + 其全部
   tool 响应」，产出仍是合法 OpenAI 序列（无 tool_calls 缺 tool、无孤立 tool）。
2. pause → resume → done：pause 让 _drive while 循环自然退出（status=paused，非终态、
   不发终态事件）；resume 拉回 running + 后台续跑直到 done。
3. cancel 是 SSE 终态：cancel 后 SSE 收到 cancelled 终态并结束（不再 heartbeat 空等）。
4. resume 不重放写：resume 重建 LoopState（get_state）→ LLM 重生成同写工具调用 →
   ToolInvocation 命中 + 业务唯一约束（Paper.dedup_key）→ Paper 行数不增。

集成方案沿用 test_confirm_endpoint.py：httpx.AsyncClient + ASGITransport + 注入测试
RunController；patch app.harness.engine.call_llm_with_fallback stub LLM；session_factory
fixture（真实测试库）。
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
# 1) trim Phase3：按完整 assistant 轮删
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


def _assert_valid_openai_sequence(messages: list[dict]) -> None:
    """断言 messages 是合法 OpenAI 序列（codex P1：逐 assistant 校验，覆盖"部分缺失"）：

    - 逐个带 tool_calls 的 assistant：其**每一个** tool_call_id 都必须能在该 assistant
      **之后**找到对应的 tool 响应（不是全局 set 差集——那会漏掉"同一 assistant 内一部分
      tool_call 有响应、另一部分没有"的非法序列）；
    - 没有孤立 tool 消息（每条 tool 都有一个前序、声明了它 tool_call_id 的 assistant）。
    """
    # ① 逐 assistant：每个 tool_call_id 都要在其后有对应 tool 响应。
    for i, m in enumerate(messages):
        if m.get("role") != "assistant" or not m.get("tool_calls"):
            continue
        for tc in m["tool_calls"]:
            cid = tc.get("id", "")
            found = any(
                mk.get("role") == "tool" and mk.get("tool_call_id", "") == cid
                for mk in messages[i + 1:]
            )
            assert found, (
                f"assistant[{i}] 的 tool_call {cid!r} 在其后缺对应 tool 响应（非法序列）"
            )

    # ② 没有孤立 tool（无任何前序 assistant 声明该 tool_call_id）。
    declared_ids: set[str] = set()
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                declared_ids.add(tc.get("id", ""))
    for m in messages:
        if m.get("role") == "tool":
            cid = m.get("tool_call_id", "")
            assert cid in declared_ids, f"存在孤立 tool 消息（无对应 assistant）: {cid!r}"


def test_trim_deletes_complete_assistant_round():
    """messages 含多个完整 assistant 轮（每轮 assistant.tool_calls=[c1,c2] + tool(c1)+tool(c2)），
    超预算时 trim Phase3 删最早的完整轮，产出仍是合法 OpenAI 序列。"""
    from app.harness.engine import estimate_messages_tokens, trim_messages_to_fit

    # system + user 固定保留
    big = "x" * 4000  # 撑大每条 tool 内容，逼 trim 进入 Phase3
    messages: list[dict] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "go"},
    ]
    # 构造 4 个完整 assistant 轮，每轮 2 个并行 tool_call。
    for r in range(4):
        c1, c2 = f"r{r}-c1", f"r{r}-c2"
        messages.append({
            "role": "assistant",
            "content": f"round {r} thinking",
            "tool_calls": [
                {"id": c1, "type": "function",
                 "function": {"name": "reader__fetch", "arguments": "{}"}},
                {"id": c2, "type": "function",
                 "function": {"name": "reader__fetch", "arguments": "{}"}},
            ],
        })
        messages.append({"role": "tool", "tool_call_id": c1, "content": big})
        messages.append({"role": "tool", "tool_call_id": c2, "content": big})

    total = estimate_messages_tokens(messages)
    # 预算设为足以触发 Phase3 删除（小于总量、但不至于把所有轮删光）
    budget = total // 2

    trimmed = trim_messages_to_fit(messages, budget)

    # 必须真的删掉了至少一整轮（messages 变短）
    assert len(trimmed) < len(messages), "Phase3 应删除最早的完整轮"
    # 预算满足
    assert estimate_messages_tokens(trimmed) <= budget
    # 合法 OpenAI 序列：无缺 tool 的 assistant、无孤立 tool
    _assert_valid_openai_sequence(trimmed)
    # system + user 必保留
    assert trimmed[0]["role"] == "system"
    assert trimmed[1]["role"] == "user"


def test_trim_phase3_no_orphan_tool_when_assistant_removed():
    """边界：删掉某 assistant 轮后，其 tool 响应不能残留为孤立 tool。
    构造「一轮内容很大的 assistant + tool」与「一轮很小的 assistant + tool」，
    预算逼 trim 删大轮，验证大轮的 tool 不会变孤立。"""
    from app.harness.engine import estimate_messages_tokens, trim_messages_to_fit

    big = "y" * 8000
    messages: list[dict] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "go"},
        # 第 0 轮：大
        {"role": "assistant", "content": "big round",
         "tool_calls": [{"id": "a0", "type": "function",
                         "function": {"name": "reader__fetch", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "a0", "content": big},
        # 第 1 轮：小
        {"role": "assistant", "content": "small round",
         "tool_calls": [{"id": "a1", "type": "function",
                         "function": {"name": "reader__fetch", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "a1", "content": "tiny"},
    ]
    budget = estimate_messages_tokens(messages) // 2
    trimmed = trim_messages_to_fit(messages, budget)
    _assert_valid_openai_sequence(trimmed)
    assert estimate_messages_tokens(trimmed) <= budget


def test_trim_phase3_partial_tool_response_makes_legal_sequence():
    """codex P1 反例：assistant.tool_calls=[c1,c2] 但只有 tool(c1)（缺 c2）。
    trim Phase3 的"合法化"不能因为「c1 有响应」就保留该 assistant —— OpenAI 要求每个
    tool_call_id 都有响应。trim 后必须产出合法序列（要么补全、要么把缺响应的 assistant
    连同其残留 tool 一并丢掉），绝不能留下缺 c2 的 assistant。

    复现关键：把部分缺失轮放在**最近**（不会被整轮删），前面放若干大完整轮（会被整轮删，
    使 remove 非空、触发 Phase3 步骤② 的合法化清理 while）。旧逻辑用 `not ids & kept` 判删
    —— 对部分缺失 assistant（c1 有响应、c2 没有），交集非空 → 不删 → 留下缺 c2 的非法序列。"""
    from app.harness.engine import estimate_messages_tokens, trim_messages_to_fit

    big = "z" * 8000
    messages: list[dict] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "go"},
    ]
    # 前面 3 个大完整轮（会被整轮删，触发清理 while）。
    for r in range(3):
        cid = f"old-{r}"
        messages.append({
            "role": "assistant", "content": f"old round {r}",
            "tool_calls": [{"id": cid, "type": "function",
                            "function": {"name": "reader__fetch", "arguments": "{}"}}],
        })
        messages.append({"role": "tool", "tool_call_id": cid, "content": big})
    # 最近的部分缺失轮：声明 p-c1+p-c2，仅 tool(p-c1)，p-c2 无响应（内容小，不会被整轮删）。
    messages.append({
        "role": "assistant", "content": "partial round",
        "tool_calls": [
            {"id": "p-c1", "type": "function",
             "function": {"name": "reader__fetch", "arguments": "{}"}},
            {"id": "p-c2", "type": "function",
             "function": {"name": "reader__fetch", "arguments": "{}"}},
        ],
    })
    messages.append({"role": "tool", "tool_call_id": "p-c1", "content": "small"})

    # 预算：留得下最近的部分缺失轮 + system/user，但删得掉前面的大完整轮。
    budget = estimate_messages_tokens(messages) // 3
    trimmed = trim_messages_to_fit(messages, budget)

    # 核心断言：产出是合法 OpenAI 序列（缺 p-c2 的 assistant 不能残留）。
    _assert_valid_openai_sequence(trimmed)
    assert estimate_messages_tokens(trimmed) <= budget


def test_trim_phase3_partial_missing_all_branches_legal():
    """补充：同一 messages 含「完整轮 / 部分缺失轮 / 全缺失轮」三种，trim 后均合法。"""
    from app.harness.engine import estimate_messages_tokens, trim_messages_to_fit

    big = "w" * 6000
    messages: list[dict] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "go"},
        # 完整轮
        {"role": "assistant", "content": "ok round",
         "tool_calls": [{"id": "ok-1", "type": "function",
                         "function": {"name": "reader__fetch", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "ok-1", "content": big},
        # 部分缺失轮（c2 无响应）
        {"role": "assistant", "content": "partial",
         "tool_calls": [
             {"id": "pm-1", "type": "function",
              "function": {"name": "reader__fetch", "arguments": "{}"}},
             {"id": "pm-2", "type": "function",
              "function": {"name": "reader__fetch", "arguments": "{}"}},
         ]},
        {"role": "tool", "tool_call_id": "pm-1", "content": big},
        # 全缺失轮（assistant 声明但完全无 tool 响应）
        {"role": "assistant", "content": "all missing",
         "tool_calls": [{"id": "am-1", "type": "function",
                         "function": {"name": "reader__fetch", "arguments": "{}"}}]},
    ]
    budget = estimate_messages_tokens(messages) // 2
    trimmed = trim_messages_to_fit(messages, budget)
    _assert_valid_openai_sequence(trimmed)
    assert estimate_messages_tokens(trimmed) <= budget


# ======================================================================
# 测试用工具 / 辅助（pause/resume/cancel/resume-no-replay 共用）
# ======================================================================

def _make_router() -> LLMRouter:
    router = LLMRouter()
    router.add_provider(
        name="stub", api_key="stub-key",
        base_url="http://stub.local/v1", models=["stub-model"],
    )
    return router


class EchoTool(BaseTool):
    """只读工具：原样回显 query（pause/resume/cancel 流程用，可控制阻塞）。"""

    tool_id = "echo"
    tool_name = "Echo Tool"
    description = "echo test tool"
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
            tool_id=self.tool_id, action=action, success=True,
            data=[{"result": params.get("query", "")}],
            summary=f"Echo: {params.get('query', '')}", data_source="stub",
        )


def _assistant(content: str, tool_calls: list[dict] | None = None) -> dict:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _tool_call(call_id: str, name: str, args: str = "{}") -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}


def _resp(message: dict) -> tuple[dict, str]:
    return ({"choices": [{"message": message, "finish_reason": "stop"}]}, "stub-model")


import uuid as _uuid


async def _make_project(session_factory, name: str | None = None) -> int:
    """建一个唯一命名的 Project（避开 uq_project_name；测试间不复用名）。"""
    async with session_factory() as s:
        proj = await create_project(s, {"name": name or f"Lifecycle-{_uuid.uuid4().hex[:8]}"})
        return proj.id


async def _wait(predicate, timeout: float = 10.0, interval: float = 0.02) -> bool:
    """轮询等待 predicate()（可为 async）返回真值；超时返回 False。"""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        res = predicate()
        if asyncio.iscoroutine(res):
            res = await res
        if res:
            return True
        await asyncio.sleep(interval)
    return False


def _make_build_ctx(registry: ToolRegistry, max_rounds: int = 6):
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


@pytest_asyncio.fixture
async def client(session_factory):
    """AsyncClient + ASGI + 注入带只读工具(echo)的测试 RunController。

    返回 (client, ctrl, session_factory, registry)。pause/resume/cancel/SSE 端点测试共用。
    """
    echo = EchoTool()
    registry = ToolRegistry()
    registry.register(echo)
    publisher = SubscribableEventPublisher()
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
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, ctrl, session_factory, echo

    app.dependency_overrides.pop(get_session, None)


# ======================================================================
# 2) pause → resume → done
# ======================================================================

@pytest.mark.asyncio
async def test_pause_resume_to_done(client):
    """pause 让 _drive while 循环自然退出（status=paused，非终态）；resume 拉回 running
    并后台续跑直到 done。用一个可控阻塞的 LLM 卡住第一轮，pause 后该轮收尾即退出。"""
    c, ctrl, session_factory, echo = client
    pid = await _make_project(session_factory)

    gate = asyncio.Event()  # 第一轮 LLM 等这个 gate
    round1_entered = asyncio.Event()
    call_count = 0

    async def fake_llm(router, model_names, messages, tools=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            round1_entered.set()
            await gate.wait()  # 卡住第一轮，给 pause 介入窗口
            return _resp(_assistant(
                "调用工具", tool_calls=[_tool_call("c-1", "echo__run", '{"query": "hi"}')],
            ))
        return _resp(_assistant("最终结果"))

    with patch("app.harness.engine.call_llm_with_fallback", new=fake_llm):
        resp = await c.post(
            f"/projects/{pid}/agent/runs", json={"prompt": "做点事", "autoConfirm": True},
        )
        assert resp.status_code == 200, resp.text
        run_id = resp.json()["runId"]

        # 等第一轮进入（LLM 已被 gate 卡住）
        assert await _wait(lambda: round1_entered.is_set())

        # pause：标 paused（while 循环在本轮结束后自然退出）
        presp = await c.post(f"/projects/{pid}/agent/runs/{run_id}/pause")
        assert presp.status_code == 200, presp.text
        assert presp.json()["status"] == "paused"

        # 放行第一轮 → step_once 收尾、save_state、while 检查 status=paused 退出
        gate.set()

        # 等 run 落到 paused（_drive task 结束、未发终态）
        async def _is_paused():
            async with session_factory() as s:
                run = await repo.get_run(s, run_id)
            return run.status == "paused"

        assert await _wait(_is_paused), "run 应处于 paused"

        # resume → 拉回 running + 后台续跑
        rresp = await c.post(f"/projects/{pid}/agent/runs/{run_id}/resume")
        assert rresp.status_code == 200, rresp.text
        assert rresp.json()["status"] == "running"

        # 等续跑到 done
        async def _is_done():
            async with session_factory() as s:
                run = await repo.get_run(s, run_id)
            return run.status == "done"

        assert await _wait(_is_done), "resume 后应续跑到 done"

    async with session_factory() as s:
        run = await repo.get_run(s, run_id)
    assert run.status == "done"
    assert run.final_output == "最终结果"
    # echo 工具确实被调用（第一轮工具执行）
    assert echo.call_count == 1


# ======================================================================
# 2b) pause/resume 对终态 run 幂等：不覆盖终态、不重启、不重跑（codex P0 竞态）
# ======================================================================

@pytest.mark.asyncio
async def test_pause_does_not_overwrite_terminal_run(client):
    """pause 必须是条件更新：对已终态（done）的 run，不能盲写 paused 覆盖终态。

    复现 codex P0 子项：pause() 写 DB=paused 若是无条件盲写，会把已经 done/failed 的
    run 覆盖成 paused。这里直接造 done 终态 run（含 LoopState 快照 status=done），
    调 pause()，断言返回 done 且 DB 仍是 done。"""
    c, ctrl, session_factory, echo = client
    pid = await _make_project(session_factory)

    from app.harness.engine import LoopState
    async with session_factory() as s:
        run = await repo.create_run(s, project_id=pid, auto_confirm=True)
        run_id = run.id
    done_state = LoopState(
        messages=[{"role": "user", "content": "x"}, {"role": "assistant", "content": "最终结果"}],
        status="done", final_output="最终结果", round_idx=1,
    )
    async with session_factory() as s:
        await repo.save_state(s, run_id, done_state)

    pstatus = await ctrl.pause(run_id)
    assert pstatus == "done", f"pause 不应覆盖 done，返回 {pstatus}"
    async with session_factory() as s:
        run = await repo.get_run(s, run_id)
        state = await repo.get_state(s, run_id)
    assert run.status == "done", f"DB status 不应被改成 paused，实为 {run.status}"
    assert state.status == "done", f"LoopState 快照不应被改成 paused，实为 {state.status}"


@pytest.mark.asyncio
async def test_resume_race_does_not_restart_terminal_run(client):
    """复现 codex P0 主竞态：pause 把 DB 写成 paused，但 _drive task 仍在 step_once 内运行。
    resume 通过 status==paused 校验后 `await prev` 等驱动跑完——驱动期间 run 已自然跑到
    done。resume 在 await prev 之后必须重读 status：发现已是 done 就不重启、不 emit resumed、
    不重跑（否则会把已终态 run 再驱动一遍，重复 LLM/工具副作用）。

    构造法：第一轮 LLM 卡在 gate；pause（DB→paused、设 pause 信号）；放行 gate 让本轮收尾。
    关键时序——在「pause 信号被 _drive 消费、while 退出落 paused」之前，让 step_once 把 run
    推到 done（第一轮就返回无 tool_calls 的终态 assistant）。这样 _drive 落的是 done，
    而 DB 此刻已被 pause 写成 paused → resume 进来看到 paused，await prev 后应重读出 done。"""
    c, ctrl, session_factory, echo = client
    pid = await _make_project(session_factory)

    gate = asyncio.Event()
    round1_entered = asyncio.Event()
    llm_calls = 0

    async def fake_llm(router, model_names, messages, tools=None, **kwargs):
        nonlocal llm_calls
        llm_calls += 1
        if llm_calls == 1:
            round1_entered.set()
            await gate.wait()  # 卡住第一轮，给 pause 介入窗口
            # 第一轮直接产出终态（无 tool_calls）→ step_once 把 status 推到 done。
            return _resp(_assistant("最终结果"))
        # 若 resume 误重启续跑，会走到这里 → 标记，便于断言「重跑」发生。
        return _resp(_assistant("不应重跑"))

    with patch("app.harness.engine.call_llm_with_fallback", new=fake_llm):
        resp = await c.post(
            f"/projects/{pid}/agent/runs", json={"prompt": "做点事", "autoConfirm": True},
        )
        run_id = resp.json()["runId"]
        assert await _wait(lambda: round1_entered.is_set())

        # pause：DB→paused、设内存 pause 信号（此刻 _drive 仍卡在 step_once 内）
        pstatus = await ctrl.pause(run_id)
        assert pstatus == "paused", pstatus

        # 放行第一轮：step_once 返回 status=done。_drive while 条件 status==running 不再成立，
        # 直接走终态分支落 done（pause 信号在「status==running」分支才消费，done 时不消费）。
        gate.set()

        # 等 _drive task 真正收束（落 done）。注意：DB 此刻仍是 pause 写下的 paused 或被
        # _drive 终态分支改写——核心是 resume 必须在 await prev 后重读真实 status。
        async def _drive_done():
            t = ctrl._tasks.get(run_id)
            return t is None or t.done()

        # resume：通过的是「pause 时写的 paused」校验路径。即使我们在这里调用，
        # resume 内部 await prev 会等到 _drive 落 done，之后重读应发现 done → 不重启。
        # 为稳定复现「resume 时 DB 仍是 paused」，在 _drive 收束前调用 resume。
        rstatus = await ctrl.resume(run_id)

        # 等所有可能被误起的驱动收束
        await _wait(_drive_done, timeout=10.0)
        await asyncio.sleep(0.1)

    # resume 不应重跑：LLM 只应被调用 1 次（第一轮）。若重启续跑，llm_calls 会 >= 2。
    assert llm_calls == 1, f"resume 不应重跑已终态 run，LLM 调用次数 {llm_calls}"
    assert rstatus in ("done", "failed"), f"resume 应返回终态，实为 {rstatus}"

    async with session_factory() as s:
        run = await repo.get_run(s, run_id)
    assert run.status == "done", f"run 终态应为 done，实为 {run.status}"
    # 没有遗留活跃 _drive task
    task = ctrl._tasks.get(run_id)
    assert task is None or task.done(), "终态 run 不应有活跃驱动 task"


# ======================================================================
# 3) cancel 是 SSE 终态
# ======================================================================

@pytest.mark.asyncio
async def test_cancel_is_terminal_in_sse(client):
    """cancel 后 SSE 应收到 cancelled 终态并结束（不再 heartbeat 空等）。

    用阻塞 LLM 卡住第一轮 → 订阅 SSE → cancel → SSE 流应以 cancelled 事件结束。
    """
    c, ctrl, session_factory, echo = client
    pid = await _make_project(session_factory)

    gate = asyncio.Event()
    round1_entered = asyncio.Event()

    async def fake_llm(router, model_names, messages, tools=None, **kwargs):
        round1_entered.set()
        await gate.wait()  # 一直卡住，直到被 cancel
        return _resp(_assistant("不会到这里"))

    with patch("app.harness.engine.call_llm_with_fallback", new=fake_llm):
        resp = await c.post(
            f"/projects/{pid}/agent/runs", json={"prompt": "做点事", "autoConfirm": True},
        )
        run_id = resp.json()["runId"]
        assert await _wait(lambda: round1_entered.is_set())

        # 开 SSE 流，后台收集事件，直到 cancelled 终态使流结束。
        received_types: list[str] = []

        async def _consume_sse():
            async with c.stream(
                "GET", f"/projects/{pid}/agent/runs/{run_id}/events",
            ) as r:
                async for line in r.aiter_lines():
                    if line.startswith("event:"):
                        received_types.append(line.split(":", 1)[1].strip())
                        if received_types[-1] == "cancelled":
                            return  # 终态 → 流应已结束

        sse_task = asyncio.create_task(_consume_sse())
        # 给 SSE 一点时间订阅
        await asyncio.sleep(0.1)

        # cancel：标 cancelled + 取消 _drive task + emit cancelled 终态
        canresp = await c.post(f"/projects/{pid}/agent/runs/{run_id}/cancel")
        assert canresp.status_code == 200, canresp.text
        assert canresp.json()["status"] == "cancelled"

        # SSE 应在拿到 cancelled 后结束（限时）
        try:
            await asyncio.wait_for(sse_task, timeout=8.0)
        except asyncio.TimeoutError:
            sse_task.cancel()
            pytest.fail("SSE 未在 cancelled 终态后结束（仍在空等 heartbeat）")
        finally:
            gate.set()  # 释放卡住的 LLM（若仍在）

    assert "cancelled" in received_types, f"SSE 应收到 cancelled 终态，实收 {received_types}"

    async with session_factory() as s:
        run = await repo.get_run(s, run_id)
    # cancel 是终态 cancelled，不能被误标 failed
    assert run.status == "cancelled", run.status


# ======================================================================
# 4) resume 不重放写：ToolInvocation 命中 + 业务唯一约束 → Paper 行数不增
# ======================================================================

@pytest.mark.asyncio
async def test_resume_does_not_replay_writes(session_factory):
    """resume 重建 LoopState（get_state）；LLM 重生成同写工具调用 → record_tool_invocation
    命中 + 业务唯一约束（Paper.dedup_key）→ Paper 行数不增。

    用真实 LibraryTool（写工具，add_paper 幂等 + dedup_key 唯一）。流程：
      1) autoConfirm=True 起 run：第一轮 library.add 一篇论文 → 第二轮挂起在 pause；
      2) pause 后 _drive 退出（status=paused）；记此刻 Paper 行数 = 1；
      3) resume：get_state 重建快照后续跑；LLM 第二轮再次发同一 library.add；
      4) 断言 Paper 行数仍为 1（写不重放）。
    """
    from app.tools.library import LibraryTool
    from app.models import Paper
    from sqlalchemy import func, select

    library = LibraryTool(session_factory)
    registry = ToolRegistry()
    registry.register(library)  # tags 含 "write" → 自动标记写工具
    publisher = SubscribableEventPublisher()
    ctrl = RunController(
        session_factory=session_factory,
        publisher=publisher,
        build_ctx=_make_build_ctx(registry),
    )
    pid = await _make_project(session_factory)

    add_args = '{"title": "唯一论文标题 ABC", "doi": "10.1234/abc"}'
    gate = asyncio.Event()
    round_entered = asyncio.Event()
    call_count = 0

    async def fake_llm(router, model_names, messages, tools=None, **kwargs):
        nonlocal call_count
        call_count += 1
        # 每轮都发同一篇论文的 add（模拟「LLM 重生成同写」）
        if call_count == 1:
            return _resp(_assistant(
                "新增论文", tool_calls=[_tool_call("w-1", "library__add", add_args)],
            ))
        # 第二轮（resume 后）：进入并等 gate，给我们检查窗口；之后再发同一 add
        round_entered.set()
        await gate.wait()
        return _resp(_assistant(
            "再次新增同篇", tool_calls=[_tool_call("w-2", "library__add", add_args)],
        ))

    async def _count_papers() -> int:
        async with session_factory() as s:
            return (await s.execute(select(func.count()).select_from(Paper))).scalar()

    with patch("app.harness.engine.call_llm_with_fallback", new=fake_llm):
        run_id = await ctrl.create(project_id=pid, user_prompt="新增论文", auto_confirm=True)
        ctrl.start(run_id)

        # 等第一轮 add 落库（Paper 行数 = 1），且进入第二轮被 gate 卡住
        assert await _wait(lambda: round_entered.is_set())
        papers_after_first = await _count_papers()
        assert papers_after_first == 1, f"第一轮应新增 1 篇，实为 {papers_after_first}"

        # pause：标 paused；放行 gate → 第二轮收尾后 while 退出
        await ctrl.pause(run_id)
        gate.set()

        async def _is_paused():
            async with session_factory() as s:
                run = await repo.get_run(s, run_id)
            return run.status == "paused"

        assert await _wait(_is_paused), "run 应处于 paused"
        # 第二轮 add 也已执行（同篇）→ 但 dedup 不增行
        papers_after_pause = await _count_papers()
        assert papers_after_pause == 1, f"同篇 add 不应增行，实为 {papers_after_pause}"

        # resume：get_state 重建快照 + 后台续跑（max_rounds 后收尾到 done）
        gate2 = asyncio.Event()
        gate2.set()  # resume 后不再卡

        await ctrl.resume(run_id)

        async def _terminal():
            async with session_factory() as s:
                run = await repo.get_run(s, run_id)
            return run.status in ("done", "failed")

        assert await _wait(_terminal, timeout=15.0), "resume 后应跑到终态"

    # 终态后 Paper 行数仍为 1：写从未重放（ToolInvocation 命中 + dedup 唯一约束兜底）
    final_papers = await _count_papers()
    assert final_papers == 1, f"resume 不应重放写，Paper 行数应为 1，实为 {final_papers}"
