"""Task P2-4 — build_runlog 聚合可验证运行日志。

覆盖：
- 聚合 events / manifest 计数 / chain_head / content_sha256 稳定可重建
- fabricated_count 取自 validation_summary（非 evidence_refs）
- messages 取自 get_state(...).messages（非原始 snapshot dict）
- prompt / model_used 抽取
- tool_invocations 审计聚合
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.agent.runlog import RUNLOG_SCHEMA_VERSION, _content_sha256, build_runlog
from app.repositories.agent_run import (
    append_event_chained,
    create_run,
    save_state,
)
from app.repositories.project import create_project


@pytest.mark.asyncio
async def test_build_runlog_aggregates_all_sources(session):
    p = await create_project(session, {"name": "P"})
    r = await create_run(session, project_id=p.id)
    await append_event_chained(session, r.id, "run_start", {"a": 1})
    await append_event_chained(session, r.id, "run_complete", {"final": "x"})
    log = await build_runlog(session, r.id)
    assert log["schema_version"] == RUNLOG_SCHEMA_VERSION
    assert log["manifest"]["event_count"] == 2
    assert log["manifest"]["chain_head"] == log["events"][-1]["event_hash"]
    # content_sha256 在重建同一 run 时稳定
    log2 = await build_runlog(session, r.id)
    assert log["manifest"]["content_sha256"] == log2["manifest"]["content_sha256"]


@pytest.mark.asyncio
async def test_fabricated_count_from_validation_summary(session):
    from app.harness.engine import LoopState

    p = await create_project(session, {"name": "P2"})
    r = await create_run(session, project_id=p.id)
    state = LoopState(
        messages=[{"role": "system", "content": "sys"}],
        validation_summary={
            "fabricated_citations": 2,
            "fabricated_spans": ["X (2099)", "Y (1800)"],
        },
    )
    await save_state(session, r.id, state)
    log = await build_runlog(session, r.id)
    assert log["manifest"]["fabricated_count"] == 2
    assert log["fabricated_spans"] == ["X (2099)", "Y (1800)"]
    # 即使 evidence_refs 为空，fabricated_count 仍来自 validation_summary
    assert log["evidence_refs"] == []


@pytest.mark.asyncio
async def test_messages_from_state_not_raw_snapshot(session):
    from app.harness.engine import LoopState

    p = await create_project(session, {"name": "P3"})
    r = await create_run(session, project_id=p.id)
    state = LoopState(
        messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "请综述主题 X"},
            {"role": "assistant", "content": "ok"},
        ],
        rounds_log=[{"round": 1, "model": "deepseek-chat"}],
        final_output="最终综述",
    )
    await save_state(session, r.id, state)
    log = await build_runlog(session, r.id)
    # messages 是 state.messages 列表，不是完整快照 dict
    assert isinstance(log["messages"], list)
    assert log["messages"][1]["content"] == "请综述主题 X"
    assert log["run"]["prompt"] == "请综述主题 X"
    assert log["run"]["model_used"] == "deepseek-chat"
    assert log["run"]["final_output"] == "最终综述"


@pytest.mark.asyncio
async def test_tool_invocations_aggregated_in_order(session):
    from app.models import ToolInvocation

    p = await create_project(session, {"name": "P4"})
    r = await create_run(session, project_id=p.id)
    session.add_all([
        ToolInvocation(run_id=r.id, idempotency_key="k1", tool_id="review",
                       action="save", result={"ok": True}),
        ToolInvocation(run_id=r.id, idempotency_key="k2", tool_id="draft",
                       action="write", result={"ok": False}),
    ])
    await session.commit()
    log = await build_runlog(session, r.id)
    assert log["manifest"]["tool_invocation_count"] == 2
    keys = [t["idempotency_key"] for t in log["tool_invocations"]]
    assert keys == ["k1", "k2"]
    assert log["tool_invocations"][0]["tool_id"] == "review"


@pytest.mark.asyncio
async def test_no_state_empty_messages(session):
    p = await create_project(session, {"name": "P5"})
    r = await create_run(session, project_id=p.id)
    log = await build_runlog(session, r.id)
    # 新建 run save_state 前 messages_snapshot=None → get_state 返回空 messages
    assert log["messages"] == []
    assert log["run"]["prompt"] == ""
    assert log["run"]["model_used"] == ""
    assert log["manifest"]["fabricated_count"] == 0


def test_content_sha256_canonical_stable():
    a = {"b": 2, "a": 1}
    b = {"a": 1, "b": 2}
    assert _content_sha256(a) == _content_sha256(b)
