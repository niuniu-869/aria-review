"""A3 · subagent dispatch 加固 + 2 skill 声明 单测。

覆盖（重点 codex 二审项：竞态/命名冲突/静默丢弃/fail-loud 是否真 fail）：
- spec：get_spec 未知 fail-loud；validate_specs 全注册通过 / 缺工具显式报错。
- dispatch fail-loud outcome：depth 超限→depth_rejected(非异常)；父无时限→skipped_deadline；
  子超时→timeout(非空成功)；子异常→error。
- 最小授权：子 loop 只暴露 spec.tool_ids（捕获 autonomous_loop 入参）。
- 结构化收集：只收 collect_tool_id 的 ToolResult.data，不解析 LLM 文本；happy 路径产出条目。
- 2 skill 可加载 + 在 SKILL_MANIFEST；submit_evidence_pack collect-only 不裁决。

离线：patch call_llm_with_fallback / autonomous_loop，绝不打真实 API。
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from app.agent import dispatch as dz
from app.agent.dispatch import (
    OUTCOME_DEPTH_REJECTED,
    OUTCOME_ERROR,
    OUTCOME_OK,
    OUTCOME_SKIPPED_DEADLINE,
    OUTCOME_TIMEOUT,
    collect_structured,
    dispatch_to_skill,
)
from app.agent.registry_factory import build_registry
from app.agent.scratchpad import InMemoryScratchpadStore, Scratchpad
from app.agent.subagent_specs import (
    SUBAGENT_SPECS,
    SubagentSpec,
    SubagentSpecError,
    get_spec,
    validate_specs,
)
from app.harness.config import EngineConfig, set_config
from app.harness.llm import LLMRouter
from app.harness.tools import ToolResult


@pytest.fixture(autouse=True)
def _patch_config():
    set_config(EngineConfig(
        context_limit=128_000, context_reserve=20_000, tool_concurrency=8,
        tool_timeout=30, tool_result_max_chars=4000,
        loop_base_timeout=120, loop_per_round_timeout=90, memo_interval=8,
    ))
    yield
    set_config(None)


def _router() -> LLMRouter:
    r = LLMRouter()
    r.add_provider(name="stub", api_key="k", base_url="http://stub/v1", models=["deepseek-chat"])
    return r


def _resp(message: dict) -> tuple[dict, str]:
    return ({"choices": [{"message": message, "finish_reason": "stop"}]}, "deepseek-chat")


def _tc(call_id: str, name: str, args: str) -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}


# ----------------------------------------------------------------- spec

def test_get_spec_unknown_fails_loud():
    with pytest.raises(SubagentSpecError):
        get_spec("nope")


def test_specs_carry_no_dispatch_tool():
    # worker 无派发权：tool_ids 绝不含 dispatch。
    for spec in SUBAGENT_SPECS.values():
        assert "dispatch" not in spec.tool_ids
        assert spec.max_depth == 1


def test_validate_specs_passes_with_full_registry():
    reg = build_registry(session_factory=None, r_client=None)
    validate_specs(reg)  # 不抛 = 所有 spec.tool_ids 均已注册


def test_validate_specs_fails_loud_when_tool_missing():
    class _Reg:
        def get(self, tid):
            return object() if tid in {"read_paper", "scratchpad", "search"} else None
    with pytest.raises(SubagentSpecError) as e:
        validate_specs(_Reg())  # submit_evidence_pack 缺失
    assert "submit_evidence_pack" in str(e.value)


# ----------------------------------------------------------------- collect_structured

def test_collect_structured_only_trusts_named_tool_data():
    results = [
        ToolResult(tool_id="scratchpad", action="add", success=True, data=[{"gap_id": "g1"}]),
        ToolResult(tool_id="read_paper", action="outline", success=True, data=[{"title": "x"}]),
        ToolResult(tool_id="scratchpad", action="add", success=False, data=[{"gap_id": "ignored"}],
                   error="no supporting_papers"),
    ]
    data, failures, reasons = collect_structured(results, "scratchpad")
    assert data == [{"gap_id": "g1"}]   # 别的工具不收
    assert failures == 1                # 失败的 add 显式记账，非静默丢弃
    assert reasons and "no supporting_papers" in reasons[0]


def test_collect_structured_excludes_list_snapshot_and_dedups():
    # add g1 → update g1 → list[g1,g2]：收集应只得本次新增/更新(去重取最新)，不收 list 快照。
    results = [
        ToolResult(tool_id="scratchpad", action="add", success=True,
                   data=[{"gap_id": "g1", "statement": "v1"}]),
        ToolResult(tool_id="scratchpad", action="update", success=True,
                   data=[{"gap_id": "g1", "statement": "v2"}]),
        ToolResult(tool_id="scratchpad", action="list", success=True,
                   data=[{"gap_id": "g1", "statement": "v2"}, {"gap_id": "g0", "statement": "old"}]),
    ]
    data, failures, _ = collect_structured(results, "scratchpad")
    assert failures == 0
    assert len(data) == 1                       # 不重复、不带 list 里的旧 g0
    assert data[0] == {"gap_id": "g1", "statement": "v2"}  # add→update 取最新


def test_collect_structured_keeps_all_evidence_packs_same_gap():
    # 证据类工具同一 gap_id 多份证据包(openalex + sciverse)必须全保留, 绝不去重(codex A3 二审 P2)。
    results = [
        ToolResult(tool_id="submit_evidence_pack", action="submit", success=True,
                   data=[{"gap_id": "g1", "reverse_search": {"provider": "openalex"}}]),
        ToolResult(tool_id="submit_evidence_pack", action="submit", success=True,
                   data=[{"gap_id": "g1", "reverse_search": {"provider": "sciverse"}}]),
    ]
    data, failures, _ = collect_structured(results, "submit_evidence_pack")
    assert failures == 0
    assert len(data) == 2                        # 两份证据都在，没被 gap_id 去重吞掉
    providers = {d["reverse_search"]["provider"] for d in data}
    assert providers == {"openalex", "sciverse"}


# ----------------------------------------------------------------- fail-loud outcomes

async def test_dispatch_depth_rejected_not_exception():
    # depth=1, spec.max_depth=1 → child_depth=2 > 1 → depth_rejected（返回 outcome，不抛）
    res = await dispatch_to_skill(
        skill_id="gap-finder", task="t", registry=build_registry(None, None),
        llm_router=_router(), base_context={}, depth=1,
    )
    assert res.outcome == OUTCOME_DEPTH_REJECTED
    assert res.data == []


async def test_dispatch_skipped_deadline():
    import time as _t
    res = await dispatch_to_skill(
        skill_id="gap-finder", task="t", registry=build_registry(None, None),
        llm_router=_router(), base_context={}, depth=0,
        deadline=_t.time() - 1.0,  # 截止时间已过 → 父无剩余时限
    )
    assert res.outcome == OUTCOME_SKIPPED_DEADLINE


async def test_dispatch_timeout_not_empty_success():
    import time as _t

    async def _slow_llm(*a, **k):
        await asyncio.sleep(0.4)
        return _resp({"role": "assistant", "content": "done"})

    with patch("app.harness.engine.call_llm_with_fallback", new=_slow_llm):
        res = await dispatch_to_skill(
            skill_id="gap-finder", task="t", registry=build_registry(None, None),
            llm_router=_router(), base_context={}, depth=0,
            deadline=_t.time() + 0.05,  # 父剩余 0.05s → 子超时极短
        )
    assert res.outcome == OUTCOME_TIMEOUT  # fail-loud：超时非「成功空结果」
    assert res.data == []


async def test_dispatch_error_on_llm_exception():
    async def _boom(*a, **k):
        raise RuntimeError("llm down")

    with patch("app.harness.engine.call_llm_with_fallback", new=_boom):
        res = await dispatch_to_skill(
            skill_id="gap-finder", task="t", registry=build_registry(None, None),
            llm_router=_router(), base_context={}, depth=0,
        )
    assert res.outcome == OUTCOME_ERROR


# ----------------------------------------------------------------- 最小授权

async def test_dispatch_passes_only_spec_tool_ids():
    captured: dict = {}

    async def _fake_loop(**kwargs):
        captured["tool_ids"] = kwargs.get("tool_ids")
        captured["model_names"] = kwargs.get("model_names")
        return ("done", "deepseek-chat", [], [])

    with patch("app.agent.dispatch.autonomous_loop", new=_fake_loop):
        await dispatch_to_skill(
            skill_id="value-evidence", task="t", registry=build_registry(None, None),
            llm_router=_router(), base_context={}, depth=0,
        )
    assert captured["tool_ids"] == {"read_paper", "search", "submit_evidence_pack"}
    assert captured["model_names"] == ["deepseek-chat"]


# ----------------------------------------------------------------- happy path（真 loop + scripted LLM）

async def test_dispatch_happy_collects_scratchpad_entry():
    pad = Scratchpad("run-d1", InMemoryScratchpadStore())
    base_context = {"scratchpad": pad, "run_id": ""}  # 无 session_factory → 纯 M1 直接执行写工具

    add_args = (
        '{"theme":"crashworthiness","statement":"X 与 Y 在 Z 情境未被研究",'
        '"lens":"concept","supporting_papers":[{"paper_id":1,"anchor_id":"a1_3_0","quote":"逐字片段"}]}'
    )
    calls = {"n": 0}

    async def _scripted(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return _resp({"role": "assistant", "content": "",
                          "tool_calls": [_tc("c1", "scratchpad__add", add_args)]})
        return _resp({"role": "assistant", "content": "已记录 1 条 GAP"})

    with patch("app.harness.engine.call_llm_with_fallback", new=_scripted):
        res = await dispatch_to_skill(
            skill_id="gap-finder", task="发现 GAP", registry=build_registry(None, None),
            llm_router=_router(), base_context=base_context, depth=0,
        )
    assert res.outcome == OUTCOME_OK
    assert len(res.data) == 1
    assert res.data[0]["statement"].startswith("X 与 Y")
    # 真落入 scratchpad
    assert len(await pad.list()) == 1


async def test_dispatch_malformed_tool_args_not_silent_ok():
    """子模型发出非法 JSON 工具参数 → 须计入失败、升级 error，非 ok+空（codex A3 二审 P2）。"""
    pad = Scratchpad("run-bad", InMemoryScratchpadStore())
    calls = {"n": 0}

    async def _scripted(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:  # 非法 JSON 参数
            return _resp({"role": "assistant", "content": "",
                          "tool_calls": [_tc("c1", "scratchpad__add", "not json {{{")]})
        return _resp({"role": "assistant", "content": "done"})

    with patch("app.harness.engine.call_llm_with_fallback", new=_scripted):
        res = await dispatch_to_skill(
            skill_id="gap-finder", task="t", registry=build_registry(None, None),
            llm_router=_router(), base_context={"scratchpad": pad, "run_id": ""}, depth=0,
        )
    assert res.outcome == OUTCOME_ERROR
    assert res.tool_failures >= 1
    assert await pad.list() == []


async def test_dispatch_collect_failure_not_silent_empty():
    """worker 调 scratchpad.add 但缺 supporting_papers 被拒 → dispatch 不可返回 ok+空，
    必须升级为 error（codex A3 P2 / fail-loud）。"""
    pad = Scratchpad("run-d2", InMemoryScratchpadStore())
    base_context = {"scratchpad": pad, "run_id": ""}
    bad_args = '{"theme":"t","statement":"s","lens":"concept","supporting_papers":[]}'  # 空证据→被拒
    calls = {"n": 0}

    async def _scripted(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return _resp({"role": "assistant", "content": "",
                          "tool_calls": [_tc("c1", "scratchpad__add", bad_args)]})
        return _resp({"role": "assistant", "content": "done"})

    with patch("app.harness.engine.call_llm_with_fallback", new=_scripted):
        res = await dispatch_to_skill(
            skill_id="gap-finder", task="t", registry=build_registry(None, None),
            llm_router=_router(), base_context=base_context, depth=0,
        )
    assert res.outcome == OUTCOME_ERROR     # 非 ok
    assert res.data == []
    assert res.tool_failures == 1
    assert await pad.list() == []           # 被拒条目确未落


# ----------------------------------------------------------------- skills + 工具

def test_two_worker_skills_loadable():
    from app.skills import load_skill
    from app.skills.loader import SKILL_MANIFEST

    assert "gap-finder" in SKILL_MANIFEST and "value-evidence" in SKILL_MANIFEST
    assert load_skill("gap-finder").content
    assert load_skill("value-evidence").content


async def test_submit_evidence_pack_collect_only():
    from app.tools.submit_evidence_pack import SubmitEvidencePackTool
    tool = SubmitEvidencePackTool()
    r = await tool.execute("submit", {"pack": {
        "gap_id": "g1",
        "reverse_search": {"query": "q", "provider": "openalex",
                           "hits": [{"title": "t", "year": 2021, "doi": None}]},
    }}, None)
    assert r.success
    pack = r.data[0]
    assert pack["gathered_by"] == "subagent"
    # collect-only：绝无 verdict / score 字段被工具加入
    assert "verdict" not in pack and "score" not in pack


async def test_submit_evidence_pack_strips_llm_verdict_fields():
    """LLM 若在 pack 里夹带 verdict/score，工具层硬剥离（裁决唯一权威是确定性 resolver）。"""
    from app.tools.submit_evidence_pack import SubmitEvidencePackTool
    tool = SubmitEvidencePackTool()
    r = await tool.execute("submit", {"pack": {
        "gap_id": "g1",
        "verdict": "valuable",      # LLM 夹带的裁决
        "score": 0.9,
        "reverse_search": {"query": "q", "provider": "openalex", "hits": []},
    }}, None)
    assert r.success
    pack = r.data[0]
    assert "verdict" not in pack and "score" not in pack
    assert set(pack.get("_stripped_verdict_fields", [])) >= {"verdict", "score"}


async def test_submit_evidence_pack_rejects_bad_pack():
    from app.tools.submit_evidence_pack import SubmitEvidencePackTool
    tool = SubmitEvidencePackTool()
    r = await tool.execute("submit", {"pack": "not-a-dict"}, None)
    assert r.success is False


async def test_submit_evidence_pack_requires_gap_id():
    """无 gap_id 的证据无法关联回 GAP → fail-loud 拒绝（codex A3 二审 P2）。"""
    from app.tools.submit_evidence_pack import SubmitEvidencePackTool
    tool = SubmitEvidencePackTool()
    r = await tool.execute("submit", {"pack": {"reverse_search": {"query": "q", "hits": []}}}, None)
    assert r.success is False and "gap_id" in (r.error or "")


async def test_submit_evidence_pack_rejects_empty_pack():
    """仅含 gap_id、无任何证据 → fail-loud 拒绝（codex A3 二审 P2）。"""
    from app.tools.submit_evidence_pack import SubmitEvidencePackTool
    tool = SubmitEvidencePackTool()
    r = await tool.execute("submit", {"pack": {"gap_id": "g1"}}, None)
    assert r.success is False and "空证据包" in (r.error or "")


async def test_submit_evidence_pack_zero_hits_is_valid_evidence():
    """反向检索 0 命中（query 已执行）是「真空白」证据，必须被接受。"""
    from app.tools.submit_evidence_pack import SubmitEvidencePackTool
    tool = SubmitEvidencePackTool()
    r = await tool.execute("submit", {"pack": {
        "gap_id": "g1", "reverse_search": {"query": "X and Y in Z", "provider": "openalex", "hits": []},
    }}, None)
    assert r.success  # 0 命中也是有效证据，不可当空包拒


async def test_dispatch_blocks_unauthorized_tool_at_execution_layer():
    """越权防线（codex A3 二审 P1）：worker 若调用 spec.tool_ids 之外但已在完整 registry
    注册的写工具，必须在执行层被硬拒，绝不触达其副作用。"""
    from app.harness.tools import BaseTool, ToolResult

    class _SpyForbiddenTool(BaseTool):
        tool_id = "forbidden_write"
        tool_name = "spy"
        description = "should never run for gap-finder"
        actions = ["act"]
        action_schemas = {"act": {"type": "object", "properties": {}, "required": []}}
        tags = ["write"]

        def __init__(self):
            self.called = 0

        async def _execute(self, action, params, context=None) -> ToolResult:
            self.called += 1
            return ToolResult(tool_id=self.tool_id, action=action, success=True, data=[{"ran": True}])

    spy = _SpyForbiddenTool()
    reg = build_registry(None, None)
    reg.register(spy)  # 注册进完整 registry，但 gap-finder 的 tool_ids 不含它
    pad = Scratchpad("run-sec", InMemoryScratchpadStore())
    calls = {"n": 0}

    async def _scripted(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:  # 越权调用未授权写工具
            return _resp({"role": "assistant", "content": "",
                          "tool_calls": [_tc("c1", "forbidden_write__act", "{}")]})
        return _resp({"role": "assistant", "content": "done"})

    with patch("app.harness.engine.call_llm_with_fallback", new=_scripted):
        res = await dispatch_to_skill(
            skill_id="gap-finder", task="t", registry=reg,
            llm_router=_router(), base_context={"scratchpad": pad, "run_id": ""}, depth=0,
        )
    assert spy.called == 0  # 未授权工具在执行层被硬拒，副作用从未发生
    # 且越权调用未被静默吞掉：无有效产出 + 有工具失败 → fail-loud 升级 error（codex A3 二审 P2）
    assert res.outcome == OUTCOME_ERROR
    assert res.tool_failures >= 1


async def test_submit_evidence_pack_coerces_string_notes():
    """SOP 下 worker 可能提交单条字符串 notes → 规整为 [str]，不应误拒（codex A3 二审 P2）。"""
    from app.tools.submit_evidence_pack import SubmitEvidencePackTool
    tool = SubmitEvidencePackTool()
    r = await tool.execute("submit", {"pack": {
        "gap_id": "g1", "notes": "检索了 X 与 Y 的近 5 年文献",
    }}, None)
    assert r.success
    assert r.data[0]["notes"] == ["检索了 X 与 Y 的近 5 年文献"]


async def test_submit_evidence_pack_requires_reverse_search_query():
    """reverse_search 缺非空 query → 拒绝（下游无法核验检索内容，codex A3 二审 P2）。"""
    from app.tools.submit_evidence_pack import SubmitEvidencePackTool
    tool = SubmitEvidencePackTool()
    r = await tool.execute("submit", {"pack": {"gap_id": "g1", "reverse_search": {"hits": []}}}, None)
    assert r.success is False and "query" in (r.error or "")


async def test_dispatch_missing_authorized_tool_fails_loud():
    """registry 缺授权工具 → dispatch fail-loud error，绝不让 worker 在残缺工具集跑空(codex A3 二审 P2)。"""
    from app.harness.tools import ToolRegistry
    res = await dispatch_to_skill(
        skill_id="gap-finder", task="t", registry=ToolRegistry(),  # 空 registry，缺 read_paper/scratchpad
        llm_router=_router(), base_context={}, depth=0,
    )
    assert res.outcome == OUTCOME_ERROR
    assert "缺授权工具" in res.content


async def test_dispatch_unknown_skill_returns_error_not_raises():
    """未知 skill_id → 结构化 error outcome，绝不外抛打断父 run（契约一致）。"""
    res = await dispatch_to_skill(
        skill_id="typo-skill", task="t", registry=build_registry(None, None),
        llm_router=_router(), base_context={}, depth=0,
    )
    assert res.outcome == OUTCOME_ERROR
    assert "typo-skill" in res.content
