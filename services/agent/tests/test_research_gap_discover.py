"""A5 · gap_discover 编排单测（纯离线，不用 client fixture，避免事件循环混用）。"""
from __future__ import annotations

import pytest

from app.agent.scratchpad import InMemoryScratchpadStore
from app.review import gap_discover as gd


@pytest.mark.asyncio
async def test_discover_gaps_collects_from_scratchpad(monkeypatch):
    """gap-finder subagent 经注入 scratchpad 落条目 → discover_gaps 以 store.list 为权威产物。"""
    store = InMemoryScratchpadStore()

    async def fake_dispatch(**kwargs):
        pad = kwargs["base_context"]["scratchpad"]
        await pad.add(
            theme="主题A", statement="X 与 Y 在 Z 情境未被研究", lens="concept",
            supporting_papers=[{"paper_id": 7, "anchor_id": "a7_1", "quote": "原文片段"}],
        )

        class R:
            outcome = gd.dispatch_to_skill.__globals__.get("OUTCOME_OK", "ok")
            data: list = []
            tool_failures = 0
        return R()

    monkeypatch.setattr(gd, "dispatch_to_skill", fake_dispatch)
    out = await gd.discover_gaps(
        topic="主题A", paper_summaries=[{"paper_id": 7, "title": "P", "research_question": "rq"}],
        registry=None, llm_router=None, base_context={}, run_id="run1", store=store, project_id=1,
    )
    assert out["run_id"] == "run1"
    assert len(out["gaps"]) == 1
    g = out["gaps"][0]
    assert g["lens"] == "concept"
    assert g["supporting_papers"][0]["anchor_id"] == "a7_1"
    assert g["status"] == "draft"          # 发现阶段不裁决


@pytest.mark.asyncio
async def test_discover_gaps_filters_error_placeholder_and_whitelists_ids(monkeypatch):
    """问题2/3 配套回归锁：
    - error 占位摘要不进 paper_id 白名单、不喂 gap-finder（避免诱导去 read 无内容论文）。
    - dispatch 的 tool_failure_reasons 透出，供调用方写 job.error（问题3 显式 failed）。
    """
    store = InMemoryScratchpadStore()
    captured: dict = {}

    async def fake_dispatch(**kwargs):
        captured["task"] = kwargs["task"]

        class R:
            outcome = "error"
            data: list = []
            tool_failures = 1
            tool_failure_reasons = ["read_paper: 无法加载 paper 999（不在本项目）"]
        return R()

    monkeypatch.setattr(gd, "dispatch_to_skill", fake_dispatch)
    out = await gd.discover_gaps(
        topic="T",
        paper_summaries=[
            {"paper_id": 7, "title": "P7", "research_question": "rq"},
            {"paper_id": 999, "title": "坏", "error": "单篇处理异常（已隔离）"},  # error 占位
        ],
        registry=None, llm_router=None, base_context={}, run_id="r1", store=store, project_id=1,
    )
    assert out["outcome"] == "error"
    assert out["tool_failure_reasons"] == ["read_paper: 无法加载 paper 999（不在本项目）"]
    # 白名单只含有效 id 7，不含 error 占位 id 999
    assert "7" in captured["task"]
    assert "999" not in captured["task"]


@pytest.mark.asyncio
async def test_discover_gaps_all_error_placeholder_fails_loud(monkeypatch):
    """codex review P1 回归锁：全部摘要是 error 占位 → 不派发 gap-finder，fail-loud outcome=error，
    绝不把"上游精读全失败"静默伪装成 done_empty（与问题3 同类静默吞错）。"""
    store = InMemoryScratchpadStore()
    dispatched = {"called": False}

    async def fake_dispatch(**kwargs):
        dispatched["called"] = True

        class R:
            outcome = "ok"
            data: list = []
            tool_failures = 0
        return R()

    monkeypatch.setattr(gd, "dispatch_to_skill", fake_dispatch)
    out = await gd.discover_gaps(
        topic="T",
        paper_summaries=[{"paper_id": 1, "error": "失败"}, {"paper_id": 2, "error": "失败"}],
        registry=None, llm_router=None, base_context={}, run_id="r2", store=store, project_id=1,
    )
    assert out["outcome"] == "error"
    assert out["gaps"] == []
    assert dispatched["called"] is False  # 无有效摘要时不浪费一次 dispatch
