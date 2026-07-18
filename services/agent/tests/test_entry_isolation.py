"""P0 三入口隔离测试。

验收（docs/plans/2026-07-05 多源检索Agent引擎设计 v2 · P0）：
  1. entry 助手：normalize / entry_to_db / tool_ids / persona 语义 + 灰度兜底。
  2. create() 把 entry 落库（search/review/gap → 列值；legacy/None/未知 → NULL）。
  3. list_recent_dialog 按 entry 隔离：搜索历史不漏进综述入口，反之亦然；legacy 只见 legacy。
  4. _build_run_ctx 据 run.entry 收窄 tool_ids + 选 persona；legacy → 全工具（tool_ids=None）+ AGENT_SYSTEM。

全程真实测试库（session_factory fixture），stub build_ctx 复用真 entries 配置。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.agent import entries as E
from app.agent.context import AgentContext
from app.agent.prompts import AGENT_SYSTEM, GAP_SYSTEM, REVIEW_SYSTEM, SEARCH_SYSTEM, WRAP_UP
from app.agent.run_controller import RunController
from app.harness.events import SubscribableEventPublisher
from app.harness.llm import LLMRouter
from app.harness.tools import ToolRegistry
from app.repositories import agent_run as repo
from app.repositories.project import create_project


# ======================================================================
# 1. entry 助手语义
# ======================================================================

def test_normalize_entry_gray_fallback():
    assert E.normalize_entry(None) == E.ENTRY_LEGACY
    assert E.normalize_entry("  SEARCH ") == "search"
    assert E.normalize_entry("review") == "review"
    assert E.normalize_entry("gap") == "gap"
    # 未知值绝不被收窄 → legacy（灰度铁律）
    assert E.normalize_entry("bogus") == E.ENTRY_LEGACY
    assert E.normalize_entry("") == E.ENTRY_LEGACY


def test_entry_to_db_legacy_is_null():
    assert E.entry_to_db(None) is None
    assert E.entry_to_db("legacy") is None
    assert E.entry_to_db("bogus") is None
    assert E.entry_to_db("search") == "search"
    assert E.entry_to_db("review") == "review"
    assert E.entry_to_db("gap") == "gap"


def test_entry_tool_ids_scoping():
    # legacy → 全工具
    assert E.entry_tool_ids(None) is None
    assert E.entry_tool_ids("bogus") is None
    # search 能检索建库但看不到 review
    search_ids = E.entry_tool_ids("search")
    assert "search" in search_ids and "review" not in search_ids
    assert {"project", "library", "ingest", "extract", "corpus"} <= search_ids
    # review 只有 review + read_paper，看不到 search/library
    assert E.entry_tool_ids("review") == {"review", "read_paper"}
    # gap 只有 read_paper/scratchpad/search
    assert E.entry_tool_ids("gap") == {"read_paper", "scratchpad", "search"}
    # 返回副本，调用方篡改不影响后续
    ids = E.entry_tool_ids("search")
    ids.add("HACK")
    assert "HACK" not in E.entry_tool_ids("search")


def test_entry_system_prompt_mapping():
    assert E.entry_system_prompt(None) == AGENT_SYSTEM
    assert E.entry_system_prompt("bogus") == AGENT_SYSTEM
    assert E.entry_system_prompt("search") == SEARCH_SYSTEM
    assert E.entry_system_prompt("review") == REVIEW_SYSTEM
    assert E.entry_system_prompt("gap") == GAP_SYSTEM


def test_entry_prompts_enforce_user_facing_boundaries():
    assert "【搜索】入口" in GAP_SYSTEM
    assert "【综述】入口" in GAP_SYSTEM
    assert "不要承诺" in GAP_SYSTEM
    assert "项目语料为空" in GAP_SYSTEM
    assert "不要输出内部思考、计划或自我对话" in WRAP_UP


def test_referenced_tool_ids_are_registered():
    """三入口引用的每个 tool_id 必须真实注册，否则收窄后 agent 无工具可用。"""
    from app.agent.registry_factory import build_registry

    reg = build_registry(None, None)
    registered = set(reg._tools.keys())
    for entry in E.ROUTABLE_ENTRIES:
        ids = E.entry_tool_ids(entry) or set()
        missing = ids - registered
        assert not missing, f"entry={entry} 引用未注册工具: {missing}"


# ======================================================================
# 辅助
# ======================================================================

async def _new_project(session_factory, name: str) -> int:
    async with session_factory() as s:
        proj = await create_project(s, {"name": name})
        return proj.id


def _make_entry_aware_build_ctx(registry: ToolRegistry):
    """build_ctx 复用真 entries 配置（同 main._build_ctx），供 _build_run_ctx 断言收窄。"""

    async def build_ctx(project_id: int, entry: str | None = None) -> AgentContext:
        return AgentContext(
            registry=registry,
            llm_router=LLMRouter(),
            model_names=["stub-model"],
            system_prompt=E.entry_system_prompt(entry),
            tool_ids=E.entry_tool_ids(entry),
            max_rounds=6,
            wrap_up_prompt="收尾",
        )

    return build_ctx


def _make_controller(session_factory) -> RunController:
    return RunController(
        session_factory,
        SubscribableEventPublisher(),
        _make_entry_aware_build_ctx(ToolRegistry()),
    )


async def _mark_done(session_factory, run_id: int, final_output: str) -> None:
    """把一条 run 标 done + 写 final_output，使其可被 list_recent_dialog 取回。"""
    from app.repositories import agent_run as r

    async with session_factory() as s:
        state = await r.get_state(s, run_id)
        state.status = "done"
        state.final_output = final_output
        await r.save_state(s, run_id, state)


# ======================================================================
# 2. create() 落库 entry
# ======================================================================

@pytest.mark.asyncio
async def test_create_persists_entry_column(session_factory):
    pid = await _new_project(session_factory, "entry-persist")
    ctrl = _make_controller(session_factory)

    rid_search = await ctrl.create(pid, "检索文献", entry="search")
    rid_review = await ctrl.create(pid, "写综述", entry="review")
    rid_gap = await ctrl.create(pid, "找空白", entry="gap")
    rid_legacy = await ctrl.create(pid, "随便")  # 不传 entry
    rid_bogus = await ctrl.create(pid, "未知", entry="bogus")

    async with session_factory() as s:
        assert (await repo.get_run(s, rid_search)).entry == "search"
        assert (await repo.get_run(s, rid_review)).entry == "review"
        assert (await repo.get_run(s, rid_gap)).entry == "gap"
        # legacy / 未知 → NULL（向后兼容 + 灰度）
        assert (await repo.get_run(s, rid_legacy)).entry is None
        assert (await repo.get_run(s, rid_bogus)).entry is None


# ======================================================================
# 3. 对话历史按 entry 隔离（codex P0-1：不跨入口串）
# ======================================================================

@pytest.mark.asyncio
async def test_history_isolated_by_entry(session_factory):
    pid = await _new_project(session_factory, "entry-history")
    ctrl = _make_controller(session_factory)

    # 三个入口各跑完一轮
    r_s = await ctrl.create(pid, "搜索指令A", entry="search")
    await _mark_done(session_factory, r_s, "搜索回复A")
    r_r = await ctrl.create(pid, "综述指令B", entry="review")
    await _mark_done(session_factory, r_r, "综述回复B")
    r_l = await ctrl.create(pid, "legacy指令C")  # 无 entry
    await _mark_done(session_factory, r_l, "legacy回复C")

    async with session_factory() as s:
        search_hist = await repo.list_recent_dialog(s, pid, entry="search")
        review_hist = await repo.list_recent_dialog(s, pid, entry="review")
        legacy_hist = await repo.list_recent_dialog(s, pid, entry=None)

    # 搜索入口只见搜索历史，不漏综述/legacy
    assert search_hist == [("搜索指令A", "搜索回复A")]
    # 综述入口只见综述历史
    assert review_hist == [("综述指令B", "综述回复B")]
    # legacy 入口只见 legacy 历史（entry IS NULL）
    assert legacy_hist == [("legacy指令C", "legacy回复C")]


@pytest.mark.asyncio
async def test_new_run_injects_only_same_entry_history(session_factory):
    """create() 注入的初始 messages 只含同 entry 历史（端到端）。"""
    pid = await _new_project(session_factory, "entry-inject")
    ctrl = _make_controller(session_factory)

    r_s = await ctrl.create(pid, "搜索历史指令", entry="search")
    await _mark_done(session_factory, r_s, "搜索历史回复")

    # 新建一条 review run —— 不应注入 search 历史
    r_r2 = await ctrl.create(pid, "本轮综述", entry="review")
    async with session_factory() as s:
        state = await repo.get_state(s, r_r2)
    msgs = state.messages
    # messages[0]=system(REVIEW persona)，其后不应出现搜索历史
    joined = "".join(m.get("content", "") for m in msgs if isinstance(m.get("content"), str))
    assert "搜索历史指令" not in joined and "搜索历史回复" not in joined
    assert state.user_prompt == "本轮综述"
    # review persona 注入（非 legacy）
    assert msgs[0]["role"] == "system"
    assert "综述撰写助手" in msgs[0]["content"]


# ======================================================================
# 4. _build_run_ctx 据 entry 收窄 tool_ids + persona
# ======================================================================

@pytest.mark.asyncio
async def test_build_run_ctx_scopes_tool_ids_and_persona(session_factory):
    pid = await _new_project(session_factory, "entry-ctx")
    ctrl = _make_controller(session_factory)

    async def _ctx_for(entry: str | None):
        rid = await ctrl.create(pid, "x", entry=entry)
        async with session_factory() as s:
            run = await repo.get_run(s, rid)
        return await ctrl._build_run_ctx(run, emit=lambda ev: None)

    ctx_search = await _ctx_for("search")
    assert ctx_search.tool_ids == E.entry_tool_ids("search")
    assert ctx_search.system_prompt == SEARCH_SYSTEM
    # 执行级硬门依赖 tool_context.allowed_tool_ids == ctx.tool_ids
    assert ctx_search.tool_context["allowed_tool_ids"] == E.entry_tool_ids("search")

    ctx_review = await _ctx_for("review")
    assert ctx_review.tool_ids == {"review", "read_paper"}
    assert ctx_review.system_prompt == REVIEW_SYSTEM

    # legacy（无 entry）→ 全工具，无回归；allowed_tool_ids=None → 执行级不拦截
    ctx_legacy = await _ctx_for(None)
    assert ctx_legacy.tool_ids is None
    assert ctx_legacy.system_prompt == AGENT_SYSTEM
    assert ctx_legacy.tool_context["allowed_tool_ids"] is None


@pytest.mark.asyncio
async def test_execution_hard_block_rejects_out_of_entry_tool(session_factory):
    """执行级硬门（codex P1）：即便 LLM 幻觉/注入构造越权 function name，
    execute_tool_calls 也据 tool_context.allowed_tool_ids 拒绝，绝不 registry.execute。"""
    from app.agent.registry_factory import build_registry
    from app.harness.engine import execute_tool_calls

    reg = build_registry(session_factory, None)
    # review 入口只授权 {review, read_paper}；构造一个越权的 search__topic 调用
    context = {
        "allowed_tool_ids": E.entry_tool_ids("review"),
        "session_factory": session_factory,
    }
    forged = [{
        "id": "call-1",
        "type": "function",
        "function": {"name": "search__topic", "arguments": "{\"query\": \"x\"}"},
    }]
    msgs = await execute_tool_calls(reg, forged, context=context, concurrency=1)
    assert len(msgs) == 1
    tr = msgs[0]["_tool_result"]
    assert tr.success is False
    assert "未在当前入口授权" in (tr.error or "")
    assert tr.tool_id == "search"


@pytest.mark.asyncio
async def test_execution_no_block_when_allowed_or_legacy(session_factory):
    """授权工具放行；legacy（allowed_tool_ids=None）不拦截（无回归）。"""
    from app.agent.registry_factory import build_registry
    from app.harness.engine import execute_tool_calls

    reg = build_registry(session_factory, None)
    # read_paper 在 review 入口授权内 —— 不应被硬门拦（会真执行，失败与否取决于业务，
    # 但错误绝不是"未在当前入口授权"）。
    tc = [{
        "id": "c1", "type": "function",
        "function": {"name": "read_paper__list", "arguments": "{}"},
    }]
    msgs = await execute_tool_calls(
        reg, tc, context={"allowed_tool_ids": E.entry_tool_ids("review"),
                          "session_factory": session_factory}, concurrency=1)
    assert "未在当前入口授权" not in (msgs[0]["_tool_result"].error or "")

    # legacy：allowed_tool_ids=None → 任何工具都不被授权门拦截
    msgs2 = await execute_tool_calls(
        reg, tc, context={"allowed_tool_ids": None,
                          "session_factory": session_factory}, concurrency=1)
    assert "未在当前入口授权" not in (msgs2[0]["_tool_result"].error or "")


@pytest.mark.asyncio
async def test_confirm_path_rejects_unauthorized_write_before_confirmation(session_factory):
    """确认路径硬门（codex P1 二轮）：越权写工具（review 入口调 project__import）应在进入
    确认队列前被直接拒绝——不挂起 awaiting_confirmation、不弹 tool_confirm_required。"""
    from app.harness.config import get_config
    from app.harness.engine import LoopState, _resolve_round_with_confirm

    pid = await _new_project(session_factory, "entry-confirm")
    ctrl = _make_controller(session_factory)
    rid = await ctrl.create(pid, "x", entry="review")
    async with session_factory() as s:
        run = await repo.get_run(s, rid)
    ctx = await ctrl._build_run_ctx(run, emit=lambda ev: None)

    state = LoopState(messages=[])
    emitted: list[dict] = []

    async def _emit(ev):
        emitted.append(ev)

    forged_write = [{
        "id": "w1", "type": "function",
        "function": {"name": "project__import_search_results",
                     "arguments": "{\"candidate_ids\": []}"},
    }]
    suspended, completed = await _resolve_round_with_confirm(
        state, ctx, get_config(), {"role": "assistant", "content": ""}, forged_write,
        confirm_check=lambda _c: True,  # 若真入队会要求确认
        emit=_emit,
    )
    # 未挂起（提前拒绝），无 tool_confirm_required
    assert suspended is False
    assert not any(e.get("type") == "tool_confirm_required" for e in emitted)
    assert state.status != "awaiting_confirmation"
    # 得到一条越权拒绝结果
    assert len(completed) == 1
    tr = completed[0]["_tool_result"]
    assert tr.success is False and "未在当前入口授权" in (tr.error or "")


@pytest.mark.asyncio
async def test_scoped_registry_hides_out_of_domain_tools(session_factory):
    """收窄后 registry.get_function_definitions(tool_ids) 只暴露本域工具（LLM 拿不到越权工具）。"""
    from app.agent.registry_factory import build_registry

    reg = build_registry(session_factory, None)
    # review 入口暴露的函数定义只应来自 review/read_paper
    review_defs = reg.get_function_definitions(E.entry_tool_ids("review"))
    names = {d["function"]["name"] for d in review_defs}
    # 不应出现 search__*/library__*/project__* 等越权工具
    assert not any(n.startswith(("search__", "library__", "project__", "corpus__")) for n in names), names
    # 全工具（legacy）应显著多于 review 收窄集
    all_defs = reg.get_function_definitions(None)
    assert len(all_defs) > len(review_defs)
