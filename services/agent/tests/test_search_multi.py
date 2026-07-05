"""M3 Agent 工具化测试：search__multi / search__sources 复用候选/event/导入路径。"""
from __future__ import annotations

import pytest

from app.tools import search as search_mod
from app.tools.search import SearchTool
from app.sources.aggregator import AggregateResult


def _cand(cid, title, **kw):
    d = {"candidate_id": cid, "title": title, "doi": kw.get("doi"), "year": kw.get("year"),
         "authors": [], "source": kw.get("source", "openalex"), "externalIds": []}
    d.update(kw)
    return d


async def test_multi_action_caches_candidates_and_emits(monkeypatch):
    events = []

    async def fake_emit(ev):
        events.append(ev)

    async def fake_multi(sources, query, *, limit, since):
        return AggregateResult(
            candidates=[_cand("openalex:1", "A paper", year=2022, pdfUrl="https://p.pdf"),
                        _cand("core:2", "B paper", year=2021)],
            query=query,
            per_source=[{"source": "openalex", "available": True, "count": 2, "error": None, "reason": None},
                        {"source": "core", "available": False, "count": 0, "error": None, "reason": "未配置 CORE_API_KEY"}],
            total_before_merge=3, total_after_merge=2, truncated=0,
        )

    monkeypatch.setattr(search_mod, "multi_source_search", fake_multi)
    tool = SearchTool(None)
    ctx = {"emit": fake_emit}
    res = await tool.execute("multi", {"query": "graph neural network"}, ctx)

    assert res.success
    # 候选进缓存，供 project__import_search_results 导入
    assert len(ctx["search_candidates"]) == 2
    # emit search_results（含 perSource）
    assert events and events[0]["type"] == "search_results"
    assert events[0]["provider"] == "multi" and len(events[0]["candidates"]) == 2
    # 摘要含合并统计 + 源状态（未配置源如实标注，非静默）
    assert "合并前 3" in res.summary and "去重合并后 2" in res.summary
    assert "core 未用" in res.summary and "未配置 CORE_API_KEY" in res.summary
    # 候选卡逐条带 candidate_id
    assert "[openalex:1]" in res.summary and "[core:2]" in res.summary


async def test_multi_action_empty_reports_sources(monkeypatch):
    async def fake_multi(sources, query, *, limit, since):
        return AggregateResult(
            candidates=[], query=query,
            per_source=[{"source": "core", "available": False, "count": 0, "error": None, "reason": "未配置 CORE_API_KEY"}],
        )

    monkeypatch.setattr(search_mod, "multi_source_search", fake_multi)
    res = await SearchTool(None).execute("multi", {"query": "x"}, {})
    assert res.success and res.data == []
    assert "未配置 CORE_API_KEY" in res.summary


async def test_multi_action_requires_query():
    res = await SearchTool(None).execute("multi", {"query": "  "}, {})
    assert not res.success and "query 是必填" in res.error


async def test_sources_action_lists_availability(monkeypatch):
    monkeypatch.setattr(search_mod, "available_sources", lambda: [
        {"source": "openalex", "role": "search", "configured": True, "reason": None, "tier": "ready"},
        {"source": "core", "role": "search", "configured": False, "reason": "未配置 CORE_API_KEY", "tier": "ready"},
        {"source": "unpaywall", "role": "enrichment", "configured": False, "reason": "未配置 UNPAYWALL_EMAIL", "tier": "ready"},
    ])
    res = await SearchTool(None).execute("sources", {}, {})
    assert res.success
    assert "openalex" in res.summary and "可用" in res.summary
    assert "未配置 CORE_API_KEY" in res.summary
    assert "补链" in res.summary  # unpaywall 标为补链
    assert res.data[0]["sources"][0]["source"] == "openalex"


async def test_topic_action_still_routes(monkeypatch):
    # 行为兼容：既有 topic 路径不受 multi/sources 新增影响。
    async def fake_search_openalex(query, limit, since):
        return 200, {"results": [{"title": "T", "openalexId": "W1"}]}

    class FakeR:
        search_openalex = staticmethod(fake_search_openalex)

    res = await SearchTool(FakeR()).execute("topic", {"query": "x", "provider": "openalex"}, {})
    assert res.success and res.data[0]["candidates"][0]["title"] == "T"


async def test_unsupported_action_fails():
    res = await SearchTool(None).execute("bogus", {"query": "x"}, {})
    assert not res.success
