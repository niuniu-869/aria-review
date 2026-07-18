"""P2 · feasibility_check 状态机 resolver + 编排测试。

验收(v3 S4)：三态裁决；decided_by=deterministic；data unavailable/method blocked→blocked；
全 unknown→hard（不误 blocked）；**★novelty 解耦一票否决回归**（gap statement 0 hit + component
method family 命中 → verdict∈{buildable,hard} 绝不 blocked）；subagent 非 ok → FeasibilityCheckError。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.agent.dispatch import OUTCOME_ERROR, OUTCOME_OK, DispatchResult
from app.review import feasibility_check as fc
from app.review.feasibility_check import (
    FeasibilityCheckError,
    resolve_feasibility_verdict,
    verify_gap_feasibility,
)


# ---- 状态机 resolver（纯函数） ----

def test_decided_by_deterministic_and_no_float_score():
    v = resolve_feasibility_verdict({"gap_id": "g1"})
    assert v["decided_by"] == "deterministic"
    # 无浮点主判分字段（codex P1-1 去伪精确）
    assert "score" not in v


def test_blocked_on_data_unavailable():
    v = resolve_feasibility_verdict({
        "gap_id": "g1",
        "negative_evidence": [{"kind": "data_unavailable", "note": "proprietary"}],
    })
    assert v["verdict"] == "blocked" and v["data_status"] == "unavailable"


def test_blocked_on_method_blocked():
    v = resolve_feasibility_verdict({
        "gap_id": "g1",
        "negative_evidence": [{"kind": "no_measurement", "note": "变量不可观测"}],
    })
    assert v["verdict"] == "blocked" and v["method_status"] == "blocked"


def test_buildable_needs_available_supported_not_heavy():
    v = resolve_feasibility_verdict({
        "gap_id": "g1",
        "data_availability": {"datasets": [{"name": "D", "access": "open", "url": "http://d"}]},
        "method_base": {"query": "federated learning", "building_blocks": [{"name": "FedAvg"}, {"name": "SHAP"}]},
        "resource_scale": {"scale_flag": "modest"},
    })
    assert v["verdict"] == "buildable"
    assert v["data_status"] == "available" and v["method_status"] == "supported"


def test_heavy_resource_downgrades_buildable_to_hard():
    v = resolve_feasibility_verdict({
        "gap_id": "g1",
        "data_availability": {"datasets": [{"name": "D", "access": "open", "url": "http://d"}]},
        "method_base": {"building_blocks": [{"name": "a"}, {"name": "b"}]},
        "resource_scale": {"scale_flag": "heavy"},
    })
    assert v["verdict"] == "hard"


def test_all_unknown_is_hard_not_blocked():
    # 全 unknown（无 open 数据证据、方法组件不足、无负证据）→ hard，绝不误 blocked（codex P0-1）
    v = resolve_feasibility_verdict({"gap_id": "g1", "method_base": {"building_blocks": [{"name": "a"}]}})
    assert v["verdict"] == "hard"
    assert v["data_status"] == "unknown" and v["method_status"] == "unknown"


def test_data_unknown_when_hit_but_no_access_evidence():
    # 命中数据名但无可访问证据（无 url/source）→ unknown，绝不冒充 available（codex P0-1 诚实）
    v = resolve_feasibility_verdict({
        "gap_id": "g1",
        "data_availability": {"datasets": [{"name": "SomeData", "access": "open"}]},  # 无 url/source
        "method_base": {"building_blocks": [{"name": "a"}, {"name": "b"}]},
    })
    assert v["data_status"] == "unknown"
    assert v["verdict"] == "hard"


def test_suspected_method_query_not_supported():
    # method query 疑含完整 GAP 论断（留痕）→ 即便 ≥2 blocks 也不算 supported（解耦命门）
    q = "A 与 B 在 Z 情境下是否已被研究"
    v = resolve_feasibility_verdict({
        "gap_id": "g1",
        "method_base": {"query": q, "building_blocks": [{"name": "a"}, {"name": "b"}]},
        "_suspected_gap_statement_queries": [q],
    })
    assert v["method_status"] == "unknown"  # 被解耦守卫压回 unknown
    assert v["signals"]["method_query_suspected"] is True


def test_novelty_decoupling_regression_novel_gap_never_blocked():
    """★一票否决回归（codex 强化）：一条真·新颖 gap（gap statement 0 hit → novelty=valuable）
    只要 component method family 命中(≥2 building_blocks) → feasibility∈{buildable,hard}，
    **绝不因新颖被打 blocked**。novelty 与 feasibility 两裁决独立。"""
    # 模拟：novelty 侧 statement 0 hit（不进 feasibility pack）；feasibility 侧只有 component 证据。
    pack = {
        "gap_id": "novel-1",
        "method_base": {"query": "federated learning, SHAP", "building_blocks": [
            {"kind": "method", "name": "FedAvg"}, {"kind": "tool", "name": "SHAP"}]},
        # data 未拿到明确可访问证据 → unknown（不 available 也不 unavailable）
        "data_availability": {"datasets": [{"name": "chest-xray", "access": "unknown"}]},
        "resource_scale": {"scale_flag": "unknown"},
        # 关键：无任何 data_unavailable / no_measurement / unidentifiable 负证据
    }
    v = resolve_feasibility_verdict(pack)
    assert v["verdict"] in ("buildable", "hard")
    assert v["verdict"] != "blocked", "新颖 gap 不得因缺明确 blocker 被误判 blocked"
    # method 有组件基座 → supported；data unknown → 落 hard（不 blocked、不硬 buildable）
    assert v["method_status"] == "supported" and v["data_status"] == "unknown"
    assert v["verdict"] == "hard"


def test_resolve_missing_gap_id_fails_loud():
    with pytest.raises(FeasibilityCheckError):
        resolve_feasibility_verdict({})


def test_data_status_requires_accessible_url_not_free_text_source(monkeypatch):
    # access=open 但只有自由文本 source（非链接）→ unknown（codex P2-2 诚实，不冒充可得）
    v = resolve_feasibility_verdict({
        "gap_id": "g", "data_availability": {"datasets": [
            {"name": "D", "access": "open", "source": "某论文提到该数据集"}]},
    })
    assert v["data_status"] == "unknown"
    # source 含仓库链接 → available
    v2 = resolve_feasibility_verdict({
        "gap_id": "g", "data_availability": {"datasets": [
            {"name": "D", "access": "open", "source": "https://github.com/x/d"}]},
        "method_base": {"building_blocks": [{"name": "a"}, {"name": "b"}]},
        "resource_scale": {"scale_flag": "modest"},
    })
    assert v2["data_status"] == "available" and v2["verdict"] == "buildable"


# ---- _assemble_pack 合并（codex P1-1/P1-2/P2-1） ----

def test_assemble_drops_suspected_query_blocks():
    """疑似含完整 GAP 论断 query 的 pack，其 building_blocks 不并入 clean 集（P1-1 解耦命门）。"""
    q_bad = "A 与 B 在 Z 是否已被研究"
    pack = fc._assemble_pack("g", [
        {"gap_id": "g", "method_base": {"query": "fedavg", "building_blocks": [{"name": "FedAvg"}]}},
        {"gap_id": "g", "method_base": {"query": q_bad, "building_blocks": [{"name": "X"}, {"name": "Y"}]},
         "_suspected_gap_statement_queries": [q_bad]},
    ])
    names = {b["name"].lower() for b in pack["method_base"]["building_blocks"]}
    assert names == {"fedavg"}  # 疑似 pack 的 X/Y 被丢，只留 clean 的 FedAvg
    # 仅 1 个 clean block → 不达 supported 门槛
    assert resolve_feasibility_verdict(pack)["method_status"] == "unknown"


def test_assemble_resource_heavy_precedence():
    """资源合并 heavy 优先（P1-2）：一份 modest 一份 heavy → heavy，防误判 buildable。"""
    pack = fc._assemble_pack("g", [
        {"gap_id": "g", "resource_scale": {"scale_flag": "modest"}},
        {"gap_id": "g", "resource_scale": {"scale_flag": "heavy"}},
    ])
    assert pack["resource_scale"]["scale_flag"] == "heavy"


def test_assemble_field_merge_keeps_stronger_dataset_evidence():
    """同名 dataset 字段级合并（P2-1）：先来的缺 url、后来的带 url → 合并保留 url，不 first-wins 丢。"""
    pack = fc._assemble_pack("g", [
        {"gap_id": "g", "data_availability": {"datasets": [{"name": "D", "access": "open"}]}},
        {"gap_id": "g", "data_availability": {"datasets": [{"name": "D", "access": "open", "url": "http://d"}]}},
        {"gap_id": "g", "method_base": {"building_blocks": [{"name": "a"}, {"name": "b"}]}},
        {"gap_id": "g", "resource_scale": {"scale_flag": "modest"}},
    ])
    ds = pack["data_availability"]["datasets"]
    assert len(ds) == 1 and ds[0]["url"] == "http://d"  # 合并保留 url
    assert resolve_feasibility_verdict(pack)["data_status"] == "available"


# ---- 编排 verify_gap_feasibility（monkeypatch dispatch，注入 status 证据非 hit_count） ----

@pytest.mark.asyncio
async def test_verify_orchestration_ok(monkeypatch):
    async def _fake_dispatch(**kwargs):
        assert kwargs["skill_id"] == "feasibility-scout"
        return DispatchResult(
            data=[{
                "gap_id": "g1",
                "method_base": {"query": "fedavg", "building_blocks": [{"name": "FedAvg"}, {"name": "SHAP"}]},
                "data_availability": {"datasets": [{"name": "D", "access": "open", "url": "http://d"}]},
                "resource_scale": {"scale_flag": "modest"},
            }],
            outcome=OUTCOME_OK, skill_id="feasibility-scout",
        )

    monkeypatch.setattr(fc, "dispatch_to_skill", _fake_dispatch)
    out = await verify_gap_feasibility(
        {"gap_id": "g1", "statement": "s", "theme": "t"},
        registry=None, llm_router=None, base_context={"project_id": 1},
    )
    assert out["gap_id"] == "g1"
    assert out["verdict"]["verdict"] == "buildable"
    assert out["verdict"]["decided_by"] == "deterministic"
    assert out["pack"]["gap_id"] == "g1"


@pytest.mark.asyncio
async def test_verify_orchestration_subagent_not_ok_fails_loud(monkeypatch):
    async def _fake_dispatch(**kwargs):
        return DispatchResult(outcome=OUTCOME_ERROR, skill_id="feasibility-scout",
                              tool_failures=2, tool_failure_reasons=["read_paper: 越界"])

    monkeypatch.setattr(fc, "dispatch_to_skill", _fake_dispatch)
    with pytest.raises(FeasibilityCheckError):
        await verify_gap_feasibility(
            {"gap_id": "g1", "statement": "s", "theme": "t"},
            registry=None, llm_router=None, base_context={},
        )


# ---- S5a 端点契约（httpx ASGITransport；POST 用 monkeypatch worker 避 LLM 依赖） ----

async def _new_project(sf, name: str) -> int:
    from app.repositories.project import create_project
    async with sf() as s:
        proj = await create_project(s, {"name": name})
        return proj.id


async def _seed_gap(sf, pid: int, gap_id: str, *, value_verdict=None, feasibility_verdict=None,
                    feasibility_pack=None):
    from app.models import GapCandidateRecord
    async with sf() as s:
        s.add(GapCandidateRecord(
            gap_id=gap_id, run_id="run-1", project_id=pid, theme="T",
            statement="X 与 Y 在 Z 未被研究", lens="concept",
            supporting_papers=[{"paper_id": 7, "anchor_id": "a7", "quote": "q"}],
            counter_evidence=[], confidence=0.6, status="draft",
            value_verdict=value_verdict, feasibility_verdict=feasibility_verdict,
            feasibility_pack=feasibility_pack,
        ))
        await s.commit()


async def _aclient(session_factory, fake_r):
    import httpx
    from app.db import get_session
    from app.harness.events import SubscribableEventPublisher
    from app.main import app, get_r_client

    app.state.publisher = SubscribableEventPublisher()
    app.state.r_client = fake_r  # 端点把 app.state.r_client 传给 background task

    async def _test_session():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_r_client] = lambda: fake_r
    app.dependency_overrides[get_session] = _test_session
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test"), app


@pytest.mark.asyncio
async def test_feasibility_endpoint_404_unknown_gap(session_factory, fake_r):
    pid = await _new_project(session_factory, "feas-ep-404")
    client, app = await _aclient(session_factory, fake_r)
    try:
        async with client as c:
            r = await c.post(f"/projects/{pid}/gaps/nope:feasibility")
            assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_feasibility_verdict_409_before_check(session_factory, fake_r):
    pid = await _new_project(session_factory, "feas-ep-409")
    await _seed_gap(session_factory, pid, "g-409")
    client, app = await _aclient(session_factory, fake_r)
    try:
        async with client as c:
            r = await c.get(f"/projects/{pid}/gaps/g-409/feasibility-verdict")
            assert r.status_code == 409
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_feasibility_verdict_readable_and_independent_of_value(session_factory, fake_r):
    pid = await _new_project(session_factory, "feas-ep-ok")
    # gap 已有 value_verdict + feasibility_verdict（模拟两独立核验完成）
    await _seed_gap(
        session_factory, pid, "g-ok",
        value_verdict={"gap_id": "g-ok", "verdict": "valuable", "decided_by": "deterministic"},
        feasibility_verdict={"gap_id": "g-ok", "verdict": "hard", "data_status": "unknown",
                             "method_status": "supported", "resource_status": "unknown",
                             "decided_by": "deterministic"},
        feasibility_pack={"gap_id": "g-ok", "method_base": {"building_blocks": [{"name": "a"}]}},
    )
    client, app = await _aclient(session_factory, fake_r)
    try:
        async with client as c:
            # feasibility verdict 可读
            rf = await c.get(f"/projects/{pid}/gaps/g-ok/feasibility-verdict")
            assert rf.status_code == 200
            body = rf.json()
            assert body["verdict"]["verdict"] == "hard"
            assert body["verdict"]["decided_by"] == "deterministic"
            assert body["pack"]["gap_id"] == "g-ok"
            # value verdict 独立可读，未被 feasibility 影响
            rv = await c.get(f"/projects/{pid}/gaps/g-ok/verdict")
            assert rv.status_code == 200
            assert rv.json()["verdict"]["verdict"] == "valuable"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_feasibility_post_202_creates_job(session_factory, fake_r, monkeypatch):
    """POST :feasibility → 202 + feasibility_run_id；worker monkeypatch 为 no-op 避 LLM 依赖。"""
    from app import routes_research

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(routes_research, "_run_gap_feasibility", _noop)
    pid = await _new_project(session_factory, "feas-ep-202")
    await _seed_gap(session_factory, pid, "g-202")
    client, app = await _aclient(session_factory, fake_r)
    try:
        async with client as c:
            r = await c.post(f"/projects/{pid}/gaps/g-202:feasibility")
            assert r.status_code == 202
            job_id = r.json()["feasibility_run_id"]
            assert job_id.isdigit()
            # 0.6.2 生产 E2E 抓到的回归：AiJobKind Literal 缺 gap_feasibility，
            # 前端轮询 GET /ai/jobs/{id} 序列化 500。此断言锁死该 kind 可读取。
            job = await c.get(f"/projects/{pid}/ai/jobs/{job_id}")
            assert job.status_code == 200
            assert job.json()["kind"] == "gap_feasibility"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_feasibility_task_carries_read_paper_whitelist(session_factory, fake_r, monkeypatch):
    """生产 job 18 回归：task 须显式携带 read_paper 白名单与「检索结果不可 read_paper」禁令。"""
    from app.review import feasibility_check as fc

    captured = {}

    async def _spy_dispatch(**kwargs):
        captured["task"] = kwargs.get("task", "")
        class _R:
            outcome = "ok"
            data = [{"gap_id": "g-wl", "data_availability": {"query": "q", "provider": "openalex", "datasets": []},
                     "method_base": {"query": "q", "building_blocks": []}}]
            tool_failures = 0
            tool_failure_reasons = []
        return _R()

    monkeypatch.setattr(fc, "dispatch_to_skill", _spy_dispatch)
    gap = {"gap_id": "g-wl", "theme": "t", "statement": "s", "lens": "method",
           "supporting_papers": [{"paper_id": 128}, {"paper_id": 16}], "counter_evidence": []}
    await fc.verify_gap_feasibility(gap, registry=None, llm_router=None, base_context={})
    assert "128" in captured["task"] and "16" in captured["task"]
    assert "不可** read_paper" in captured["task"]
