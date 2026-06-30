"""agent_event 哈希链测试（P2-1）。

覆盖：
1. append_event_chained 连续追加 3 条 → 每条 prev_hash == 上一条 event_hash，
   首条 prev_hash == ""。
2. 篡改 payload 后重算 _event_hash 不再匹配存储的 event_hash（链断可检测）。
"""
from __future__ import annotations

import pytest

from app.repositories.agent_run import (
    _event_hash,
    append_event_chained,
    create_run,
)
from app.repositories.project import create_project


@pytest.mark.asyncio
async def test_event_chain_links_and_tamper_detectable(session):
    p = await create_project(session, {"name": "P"})
    r = await create_run(session, project_id=p.id)

    e1 = await append_event_chained(session, r.id, "run_start", {"a": 1})
    e2 = await append_event_chained(session, r.id, "round_complete", {"b": 2})
    e3 = await append_event_chained(session, r.id, "run_complete", {"c": 3})

    # 首条 prev_hash 为空串
    assert e1.prev_hash == ""
    # 每条 prev_hash == 上一条 event_hash（链相接）
    assert e2.prev_hash == e1.event_hash
    assert e3.prev_hash == e2.event_hash
    # event_hash 均为 64 hex（sha256）
    for ev in (e1, e2, e3):
        assert isinstance(ev.event_hash, str) and len(ev.event_hash) == 64

    # seq 递增
    assert [e1.seq, e2.seq, e3.seq] == [1, 2, 3]

    # 完整性校验：用存储的字段（含 ts）重算应等于存储的 event_hash
    recomputed = _event_hash(
        e2.prev_hash, e2.run_id, e2.seq, e2.type, e2.payload, e2.ts.isoformat(),
    )
    assert recomputed == e2.event_hash

    # 篡改 payload → 重算不再匹配（链断可检测）
    tampered = _event_hash(
        e2.prev_hash, e2.run_id, e2.seq, e2.type, {"b": 999}, e2.ts.isoformat(),
    )
    assert tampered != e2.event_hash
