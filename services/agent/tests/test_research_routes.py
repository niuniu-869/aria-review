"""A5 · 研究副驾路由集成测试 — 契约形状（B fixture 真相源）+ HITL + 状态映射。

DB 路径用 httpx.AsyncClient + ASGITransport（同 loop，避免 sync TestClient 跨 loop teardown）。
成功 discover/verify 走真库背景任务（global SessionLocal）→ 此处只测同步读写端点 + 404/422；
gap 经 session_factory 直接 seed，覆盖 scratchpad / verdict / PATCH 的契约形状。
"""
from __future__ import annotations

import httpx
import pytest
import pytest_asyncio

from app.db import get_session
from app.errors import ApiError
from app.main import app, get_r_client
from app.models import GapCandidateRecord
from app.routes_research import (
    GapPatchRequest,
    _discover_job_update,
    _gap_dict,
    _run_status,
    _validate_patch_oneof,
)


# ===================================================================== 纯单元（无 DB）

class _Job:
    def __init__(self, status):
        self.status = status


def test_run_status_mapping():
    assert _run_status(_Job("done")) == "completed"
    assert _run_status(_Job("failed")) == "failed"
    assert _run_status(_Job("error")) == "failed"
    assert _run_status(_Job("running")) == "running"
    assert _run_status(_Job("queued")) == "running"
    assert _run_status(None) == "running"


def test_discover_job_update_ok_with_gaps():
    upd = _discover_job_update({"outcome": "ok", "gaps": [{"gap_id": "g1"}, {"gap_id": "g2"}],
                                "tool_failures": 1})
    assert upd["status"] == "done"
    assert upd["error"] is None
    assert upd["summary_json"]["empty"] is False
    assert upd["summary_json"]["tool_failures"] == 1   # codex review P2：done 也保留失败计数
    assert upd["event"]["type"] == "done"


def test_discover_job_update_ok_empty_is_done_empty():
    """completed-empty（codex 二审）：outcome=ok 但 0 条 = 正常跑完未发现，仍 done 但标 empty。"""
    upd = _discover_job_update({"outcome": "ok", "gaps": [], "tool_failures": 0})
    assert upd["status"] == "done"
    assert upd["summary_json"]["empty"] is True
    assert upd["event"]["type"] == "done_empty"


def test_discover_job_update_error_is_failed():
    """问题3 回归锁：gap-finder outcome=error 必须置 failed，绝不静默 done（吞错成 completed）。"""
    upd = _discover_job_update({"outcome": "error", "gaps": [], "tool_failures": 1,
                                "tool_failure_reasons": ["read_paper: 无法加载 paper 571（不在本项目）"]})
    assert upd["status"] == "failed"
    assert "571" in upd["error"]
    assert upd["event"]["type"] == "error"


def test_discover_job_update_nonok_outcome_failed():
    """任意非 ok outcome（timeout/越权等）一律 failed，不放过。"""
    upd = _discover_job_update({"outcome": "timeout", "gaps": [], "tool_failures": 0})
    assert upd["status"] == "failed"


def test_gap_dict_from_orm_like():
    class Row:
        gap_id = "gap_1"; theme = "T"; statement = "S"; lens = "concept"
        supporting_papers = [{"paper_id": 1, "anchor_id": "a1", "quote": "q"}]
        counter_evidence = []; confidence = 0.5; status = "draft"; value_verdict = None
    d = _gap_dict(Row())
    assert set(d) == {"gap_id", "theme", "statement", "lens", "supporting_papers",
                      "counter_evidence", "confidence", "status", "value_verdict"}


def test_patch_oneof_revise_requires_statement():
    with pytest.raises(ApiError) as ei:
        _validate_patch_oneof(GapPatchRequest(human_decision="revise"))
    assert ei.value.status_code == 422


def test_patch_oneof_accept_rejects_statement():
    with pytest.raises(ApiError):
        _validate_patch_oneof(GapPatchRequest(human_decision="accept", statement="x"))


def test_patch_oneof_accept_ok():
    _validate_patch_oneof(GapPatchRequest(human_decision="accept"))
    _validate_patch_oneof(GapPatchRequest(human_decision="revise", statement="新论断"))


# ===================================================================== 集成（async client）

@pytest_asyncio.fixture
async def aclient(session_factory, fake_r):
    async def _test_session():
        async with session_factory() as s:
            yield s
    app.dependency_overrides[get_r_client] = lambda: fake_r
    app.dependency_overrides[get_session] = _test_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, session_factory
    app.dependency_overrides.clear()


async def _new_project(c) -> int:
    r = await c.post("/projects", json={"name": "A5"})
    assert r.status_code == 201
    return r.json()["id"]


async def _seed_gap(sf, *, pid, run_id, gap_id, value_verdict=None, evidence_pack=None,
                    status="draft", statement="X 与 Y 在 Z 未被研究"):
    async with sf() as s:
        s.add(GapCandidateRecord(
            gap_id=gap_id, run_id=run_id, project_id=pid, theme="主题A",
            statement=statement, lens="concept",
            supporting_papers=[{"paper_id": 7, "anchor_id": "a7_1", "quote": "片段"}],
            counter_evidence=[], confidence=0.6, status=status,
            value_verdict=value_verdict, evidence_pack=evidence_pack,
        ))
        await s.commit()


@pytest.mark.asyncio
async def test_discover_missing_project_404(aclient):
    c, _ = aclient
    r = await c.post("/projects/999999/corpus/c1/gaps:discover", json={"topic": "t"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_discover_no_corpus_400(aclient):
    """问题1 回归锁：项目无可读全文语料 → discover 端点立即 400 NO_CORPUS。

    此前无全文项目（如 OpenAlex 元数据建库）会静默建 job + 202，异步跑到 load 空才
    failed，用户等半天看模糊失败。预检后即时快速失败。空项目无 included 全文 → 命中。
    """
    c, _ = aclient
    pid = await _new_project(c)  # 空项目：无 included 全文论文
    r = await c.post(f"/projects/{pid}/corpus/c1/gaps:discover", json={})
    assert r.status_code == 400
    assert r.json()["code"] == "NO_CORPUS"


@pytest.mark.asyncio
async def test_discover_no_body_not_422(aclient):
    """契约 §2.1 :discover 无请求体（openapi requestBody?: never）；前端不发 body。

    回归锁: 后端曾把 topic 设为必填 → 前端无 body 请求 422（dogfood 实测 bad-gateway/422）。
    修复后 body 可选、topic 从项目 research_question/name 派生。打到不存在项目 → 走到
    项目存在性检查返回 404（而非请求体校验 422），证明 body 已可选。
    """
    c, _ = aclient
    r = await c.post("/projects/999999/corpus/c1/gaps:discover")  # 不带任何 body
    assert r.status_code == 404  # 不是 422 — body 可选、topic 派生


@pytest.mark.asyncio
async def test_scratchpad_shape_with_seeded_gap(aclient):
    c, sf = aclient
    pid = await _new_project(c)
    await _seed_gap(sf, pid=pid, run_id="777", gap_id="gap_a")
    r = await c.get(f"/projects/{pid}/agent/runs/777/scratchpad")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"run_id", "entries", "run_status"}
    assert body["run_id"] == "777"
    assert len(body["entries"]) == 1
    g = body["entries"][0]
    assert set(g) >= {"gap_id", "theme", "statement", "lens", "supporting_papers",
                      "counter_evidence", "confidence", "status", "value_verdict"}
    assert g["supporting_papers"][0]["anchor_id"] == "a7_1"


@pytest.mark.asyncio
async def test_verdict_409_then_200_shape(aclient):
    c, sf = aclient
    pid = await _new_project(c)
    await _seed_gap(sf, pid=pid, run_id="778", gap_id="gap_v")
    # 未核验 → 409
    r0 = await c.get(f"/projects/{pid}/gaps/gap_v/verdict")
    assert r0.status_code == 409
    # 注入裁决 + 证据包 → 200 GapVerdictResult 复合体（§2.4-1）
    verdict = {"gap_id": "gap_v", "verdict": "valuable", "score": 0.8,
               "thresholds": {"reverse_hit_high": 25, "reverse_hit_low": 3},
               "rationale": "rare+hole", "decided_by": "deterministic"}
    evidence = {"gap_id": "gap_v", "reverse_search": {"query": "q", "provider": "openalex",
                "hit_count": 2, "top_hits": []},
                "biblio_structure": {"metric": "cooccurrence_gap", "value": 0.0,
                "interpretation": "断层", "source_view": "conceptual"},
                "gathered_by": "subagent", "skipped": []}
    from sqlalchemy import select
    async with sf() as s:
        rec = (await s.execute(
            select(GapCandidateRecord).where(GapCandidateRecord.gap_id == "gap_v")
        )).scalar_one()
        rec.value_verdict = verdict
        rec.evidence_pack = evidence
        await s.commit()
    r = await c.get(f"/projects/{pid}/gaps/gap_v/verdict")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"gap_id", "verdict", "evidence"}
    assert body["verdict"]["decided_by"] == "deterministic"
    assert body["evidence"]["gathered_by"] == "subagent"


@pytest.mark.asyncio
async def test_patch_hitl_accept_and_revise(aclient):
    c, sf = aclient
    pid = await _new_project(c)
    await _seed_gap(sf, pid=pid, run_id="779", gap_id="gap_h")
    # accept → status accepted
    r1 = await c.patch(f"/projects/{pid}/gaps/gap_h", json={"human_decision": "accept"})
    assert r1.status_code == 200
    assert r1.json()["status"] == "accepted"
    # revise → 改写 statement
    r2 = await c.patch(f"/projects/{pid}/gaps/gap_h",
                       json={"human_decision": "revise", "statement": "修订后的论断"})
    assert r2.status_code == 200
    assert r2.json()["statement"] == "修订后的论断"


@pytest.mark.asyncio
async def test_patch_revise_without_statement_422_before_lookup(aclient):
    c, _ = aclient
    pid = await _new_project(c)
    r = await c.patch(f"/projects/{pid}/gaps/nope", json={"human_decision": "revise"})
    assert r.status_code == 422   # 前置 oneOf 校验先于 gap 404


@pytest.mark.asyncio
async def test_patch_missing_gap_404(aclient):
    c, _ = aclient
    pid = await _new_project(c)
    r = await c.patch(f"/projects/{pid}/gaps/nope", json={"human_decision": "accept"})
    assert r.status_code == 404
