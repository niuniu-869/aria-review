"""A1 scratchpad（GAP 底座，类 harness 工作记忆）单测。

覆盖：
- GapCandidate dataclass 契约形状 round-trip（§2.2 字段级）。
- Scratchpad add→update→list（InMemory store）。
- fail-loud：空 supporting_papers / 缺 anchor_id / 非法 lens 被拒（ScratchpadError + 工具 _fail）。
- 并发写不丢条目（asyncio.gather 多并发 add）。
- ScratchpadTool（BaseTool）add/update/list 经注入 context。
- DbScratchpadStore 落库 round-trip + 并发不丢（session_factory，真 schema）。

离线：默认 FakeLLM（不加 allow_real_llm_router）；本模块纯结构操作零 LLM。
"""
from __future__ import annotations

import asyncio

import pytest

from app.agent.scratchpad import (
    GapCandidate,
    InMemoryScratchpadStore,
    Scratchpad,
    ScratchpadError,
)
from app.tools.scratchpad import ScratchpadTool


# ---------------------------------------------------------------- 数据结构

def _sp(paper_id=1, anchor_id="a1_3_0", quote="逐字原文片段"):
    return {"paper_id": paper_id, "anchor_id": anchor_id, "quote": quote}


def test_gap_candidate_contract_roundtrip():
    gc = GapCandidate(
        gap_id="gap_x",
        theme="主题簇 A",
        statement="X 与 Y 的关系在 Z 情境下未被研究",
        lens="concept",
        supporting_papers=[_sp()],
        counter_evidence=[{"paper_id": 2, "anchor_id": "a2_1_0", "note": "反例"}],
        confidence=0.7,
    )
    d = gc.to_dict()
    # 契约 §2.2 字段级齐备
    assert set(d) == {
        "gap_id", "theme", "statement", "lens", "supporting_papers",
        "counter_evidence", "confidence", "status", "value_verdict",
    }
    assert d["status"] == "draft"
    assert d["value_verdict"] is None
    # round-trip 保真
    assert GapCandidate.from_dict(d).to_dict() == d


# ---------------------------------------------------------------- Scratchpad 核心

async def test_add_then_list_roundtrip():
    pad = Scratchpad("run-1", InMemoryScratchpadStore())
    e = await pad.add(theme="t", statement="s", lens="method", supporting_papers=[_sp()])
    assert e.gap_id and e.status == "draft"
    entries = await pad.list()
    assert [x.gap_id for x in entries] == [e.gap_id]


async def test_update_changes_status_and_statement():
    pad = Scratchpad("run-1", InMemoryScratchpadStore())
    e = await pad.add(theme="t", statement="旧论断", lens="theory", supporting_papers=[_sp()])
    upd = await pad.update(e.gap_id, statement="新论断", status="accepted")
    assert upd.statement == "新论断" and upd.status == "accepted"
    # list 反映更新且未新增条目
    entries = await pad.list()
    assert len(entries) == 1 and entries[0].statement == "新论断"


async def test_add_rejects_empty_supporting_papers():
    pad = Scratchpad("run-1", InMemoryScratchpadStore())
    with pytest.raises(ScratchpadError):
        await pad.add(theme="t", statement="s", lens="concept", supporting_papers=[])
    # fail-loud：被拒条目不落
    assert await pad.list() == []


async def test_add_rejects_supporting_paper_without_anchor():
    pad = Scratchpad("run-1", InMemoryScratchpadStore())
    with pytest.raises(ScratchpadError):
        await pad.add(theme="t", statement="s", lens="concept",
                      supporting_papers=[{"paper_id": 1, "quote": "无 anchor"}])


async def test_add_rejects_invalid_lens():
    pad = Scratchpad("run-1", InMemoryScratchpadStore())
    with pytest.raises(ScratchpadError):
        await pad.add(theme="t", statement="s", lens="未知", supporting_papers=[_sp()])


async def test_update_unknown_gap_id_fails_loud():
    pad = Scratchpad("run-1", InMemoryScratchpadStore())
    with pytest.raises(ScratchpadError):
        await pad.update("不存在", status="accepted")


async def test_update_rejects_invalid_status():
    pad = Scratchpad("run-1", InMemoryScratchpadStore())
    e = await pad.add(theme="t", statement="s", lens="concept", supporting_papers=[_sp()])
    with pytest.raises(ScratchpadError):
        await pad.update(e.gap_id, status="bogus")


async def test_concurrent_adds_lose_no_entries():
    pad = Scratchpad("run-1", InMemoryScratchpadStore())
    N = 50
    await asyncio.gather(*[
        pad.add(theme="t", statement=f"s{i}", lens="concept", supporting_papers=[_sp(paper_id=i)])
        for i in range(N)
    ])
    entries = await pad.list()
    assert len(entries) == N
    # gap_id 全唯一（并发不撞键）
    assert len({x.gap_id for x in entries}) == N


# ---------------------------------------------------------------- ScratchpadTool

def _ctx(pad):
    return {"run_id": pad.run_id, "scratchpad": pad}


async def test_tool_add_update_list():
    pad = Scratchpad("run-1", InMemoryScratchpadStore())
    tool = ScratchpadTool()
    r_add = await tool.execute("add", {
        "theme": "t", "statement": "s", "lens": "concept",
        "supporting_papers": [_sp()],
    }, _ctx(pad))
    assert r_add.success
    gid = r_add.data[0]["gap_id"]

    r_upd = await tool.execute("update", {"gap_id": gid, "status": "verified"}, _ctx(pad))
    assert r_upd.success and r_upd.data[0]["status"] == "verified"

    r_list = await tool.execute("list", {}, _ctx(pad))
    assert r_list.success and len(r_list.data) == 1


async def test_tool_add_fail_loud_no_supporting_papers():
    pad = Scratchpad("run-1", InMemoryScratchpadStore())
    tool = ScratchpadTool()
    r = await tool.execute("add", {
        "theme": "t", "statement": "s", "lens": "concept", "supporting_papers": [],
    }, _ctx(pad))
    assert r.success is False and r.error  # fail-loud：success=False，非静默空


async def test_tool_missing_scratchpad_context_fails():
    tool = ScratchpadTool()
    r = await tool.execute("list", {}, {"run_id": "x"})  # 无 scratchpad
    assert r.success is False


# ---------------------------------------------------------------- DbScratchpadStore

async def test_db_store_roundtrip(session_factory):
    from app.agent.scratchpad import DbScratchpadStore
    store = DbScratchpadStore(session_factory)
    pad = Scratchpad("run-db-1", store)
    e = await pad.add(theme="t", statement="db 持久化", lens="concept",
                      supporting_papers=[_sp()], confidence=0.5)
    # 新建一个 Scratchpad 实例（模拟另一次请求）从 DB 重新拉取
    pad2 = Scratchpad("run-db-1", DbScratchpadStore(session_factory))
    entries = await pad2.list()
    assert len(entries) == 1
    assert entries[0].gap_id == e.gap_id
    assert entries[0].statement == "db 持久化"
    assert entries[0].supporting_papers[0]["anchor_id"] == "a1_3_0"


async def test_db_store_concurrent_writes_lose_none(session_factory):
    from app.agent.scratchpad import DbScratchpadStore
    pad = Scratchpad("run-db-2", DbScratchpadStore(session_factory))
    N = 30
    await asyncio.gather(*[
        pad.add(theme="t", statement=f"s{i}", lens="method", supporting_papers=[_sp(paper_id=i)])
        for i in range(N)
    ])
    entries = await Scratchpad("run-db-2", DbScratchpadStore(session_factory)).list()
    assert len(entries) == N
    assert len({x.gap_id for x in entries}) == N


async def test_db_store_run_isolation(session_factory):
    from app.agent.scratchpad import DbScratchpadStore
    store = DbScratchpadStore(session_factory)
    await Scratchpad("run-A", store).add(theme="t", statement="A", lens="concept", supporting_papers=[_sp()])
    await Scratchpad("run-B", store).add(theme="t", statement="B", lens="concept", supporting_papers=[_sp()])
    a = await Scratchpad("run-A", store).list()
    b = await Scratchpad("run-B", store).list()
    assert len(a) == 1 and len(b) == 1
    assert a[0].statement == "A" and b[0].statement == "B"
