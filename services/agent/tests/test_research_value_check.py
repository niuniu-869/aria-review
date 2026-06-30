"""A4 · value_check 单测 — 确定性价值 resolver + 计量结构佐证 + 编排 fail-loud。

重点（codex 二审项）：裁决纯函数零 LLM、阈值边界、反向检索去重防虚高、subagent fail-loud。
离线：编排测试 patch dispatch_to_skill，绝不打真实 API。
"""
from __future__ import annotations

import pytest

from app.review import value_check as vc
from app.review.value_check import (
    ValueCheckError,
    _build_reverse_search,
    resolve_value_verdict,
    structural_hole,
    verify_gap_value,
)


# ----------------------------------------------------------------- 结构佐证

_GRAPH = {
    "nodes": [
        {"id": "n1", "label": "MD&A 语调", "value": 10},
        {"id": "n2", "label": "崩盘风险", "value": 8},
        {"id": "n3", "label": "大语言模型", "value": 5},
    ],
    "edges": [
        {"source": "n1", "target": "n2", "weight": 4.0},  # 语调-崩盘 已共同研究
        # n3(大模型) 与 n1/n2 无连边 → 断层
    ],
}


def test_structural_hole_strong_edge_no_hole():
    bs, hole = structural_hole("MD&A 语调", "崩盘风险", _GRAPH)
    assert hole is False
    assert bs["metric"] == "low_coupling"
    assert bs["value"] == 4.0
    assert bs["source_view"] == "conceptual"


def test_structural_hole_no_edge_is_hole():
    bs, hole = structural_hole("大语言模型", "崩盘风险", _GRAPH)
    assert hole is True
    assert bs["metric"] == "cooccurrence_gap"
    assert bs["value"] == 0.0


def test_structural_hole_weak_edge_below_min_is_hole():
    g = {"nodes": _GRAPH["nodes"], "edges": [{"source": "n1", "target": "n3", "weight": 0.4}]}
    bs, hole = structural_hole("MD&A 语调", "大语言模型", g, min_weight=1.0)
    assert hole is True
    assert bs["value"] == 0.4


def test_structural_hole_concept_not_in_network_not_hole():
    bs, hole = structural_hole("量子纠缠", "崩盘风险", _GRAPH)
    assert hole is False  # 未定位≠有断层（不能把"没找到"当"有空白"）
    assert bs["source_view"] is None
    assert "未定位" in bs["interpretation"]


# ----------------------------------------------------------------- 确定性裁决

def _pack(hit_count, *, hole=True, gap_id="g1"):
    bs = (
        {"metric": "cooccurrence_gap", "value": 0.0, "interpretation": "存在共现断层", "source_view": "conceptual"}
        if hole else
        {"metric": "low_coupling", "value": 5.0, "interpretation": "已共同研究", "source_view": "conceptual"}
    )
    return {
        "gap_id": gap_id,
        "reverse_search": {"query": "q", "provider": "openalex", "hit_count": hit_count, "top_hits": []},
        "biblio_structure": bs,
        "gathered_by": "subagent",
        "skipped": [],
    }


def test_verdict_high_hits_likely_filled():
    v = resolve_value_verdict(_pack(40, hole=True))
    assert v["verdict"] == "likely_filled"
    assert v["decided_by"] == "deterministic"


def test_verdict_low_hits_and_hole_valuable():
    v = resolve_value_verdict(_pack(2, hole=True))
    assert v["verdict"] == "valuable"
    assert 0.0 <= v["score"] <= 1.0
    assert v["score"] > 0.5


def test_verdict_low_hits_no_hole_inconclusive():
    # 命中少但无结构佐证 → 缺一不可 → inconclusive（§2.3：valuable 需 AND）
    v = resolve_value_verdict(_pack(2, hole=False))
    assert v["verdict"] == "inconclusive"


def test_verdict_mid_hits_inconclusive():
    v = resolve_value_verdict(_pack(12, hole=True))
    assert v["verdict"] == "inconclusive"


def test_verdict_decided_by_always_deterministic():
    for h in (0, 3, 4, 24, 25, 100):
        assert resolve_value_verdict(_pack(h))["decided_by"] == "deterministic"


def test_verdict_thresholds_configurable_per_domain():
    # 工程领域文献稀疏 → 调低阈值；同样 hit=6 在不同阈值下判定不同
    p = _pack(6, hole=True)
    assert resolve_value_verdict(p, {"reverse_hit_high": 25, "reverse_hit_low": 3})["verdict"] == "inconclusive"
    assert resolve_value_verdict(p, {"reverse_hit_high": 10, "reverse_hit_low": 8})["verdict"] == "valuable"
    assert resolve_value_verdict(p, {"reverse_hit_high": 5, "reverse_hit_low": 2})["verdict"] == "likely_filled"


def test_verdict_illegal_thresholds_fail_loud():
    with pytest.raises(ValueCheckError):
        resolve_value_verdict(_pack(5), {"reverse_hit_high": 2, "reverse_hit_low": 9})


def test_verdict_missing_hit_count_fail_loud():
    bad = {"gap_id": "g", "reverse_search": {"query": "q", "provider": "openalex"}}
    with pytest.raises(ValueCheckError):
        resolve_value_verdict(bad)


def test_verdict_thresholds_echoed_transparent():
    v = resolve_value_verdict(_pack(2), {"reverse_hit_high": 30, "reverse_hit_low": 5})
    assert v["thresholds"] == {"reverse_hit_high": 30, "reverse_hit_low": 5}


# ----------------------------------------------------------------- 去重防虚高

def test_reverse_search_dedup_by_doi_and_title():
    packs = [{
        "reverse_search": {
            "query": "tone crash risk", "provider": "sciverse",
            "hits": [
                {"title": "A", "year": 2020, "doi": "10.1/x"},
                {"title": "A dup diff title", "year": 2020, "doi": "10.1/X"},   # 同 DOI 大小写 → 去重
                {"title": "B", "year": 2021, "doi": None},
                {"title": " b ", "year": 2021, "doi": None},                    # 同归一化 title → 去重
                {"title": "C", "year": 2022, "doi": "10.2/y"},
            ],
        }
    }]
    rs = _build_reverse_search(packs)
    assert rs["hit_count"] == 3          # A, B, C（防同一文献多源重复计数虚高）
    assert rs["provider"] == "sciverse"


# ----------------------------------------------------------------- 编排 fail-loud（离线）

class _FakeResult:
    def __init__(self, outcome, data=None, tool_failures=0):
        self.outcome = outcome
        self.data = data or []
        self.tool_failures = tool_failures
        self.tool_failure_reasons = []
        self.content = ""


@pytest.mark.asyncio
async def test_verify_gap_value_happy(monkeypatch):
    async def fake_dispatch(**kwargs):
        return _FakeResult(vc.OUTCOME_OK, data=[{
            "gap_id": "g1",
            "reverse_search": {"query": "q", "provider": "openalex",
                               "hits": [{"title": "T", "year": 2021, "doi": "10.9/z"}]},
            "biblio_structure": {"concept_a": "大语言模型", "concept_b": "崩盘风险"},
            "skipped": [],
        }])
    monkeypatch.setattr(vc, "dispatch_to_skill", fake_dispatch)

    gap = {"gap_id": "g1", "theme": "T", "statement": "大语言模型 崩盘风险 关系未被研究"}
    out = await verify_gap_value(gap, registry=None, llm_router=None, base_context={}, graph=_GRAPH)
    assert out["gap_id"] == "g1"
    assert out["evidence"]["gathered_by"] == "subagent"
    assert out["evidence"]["reverse_search"]["hit_count"] == 1
    # 大语言模型↔崩盘风险 无连边 → 断层；hit=1≤3 → valuable
    assert out["verdict"]["verdict"] == "valuable"
    assert out["verdict"]["decided_by"] == "deterministic"


@pytest.mark.asyncio
async def test_verify_gap_value_subagent_error_fail_loud(monkeypatch):
    async def fake_dispatch(**kwargs):
        return _FakeResult("error", tool_failures=2)   # subagent 非 ok
    monkeypatch.setattr(vc, "dispatch_to_skill", fake_dispatch)
    with pytest.raises(ValueCheckError):
        await verify_gap_value({"gap_id": "g"}, registry=None, llm_router=None, base_context={}, graph=_GRAPH)
