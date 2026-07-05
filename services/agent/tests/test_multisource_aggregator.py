"""M2 聚合层集测：跨源合并择优 / 确定性预过滤 / 配额 / 并发 fan-out。"""
from __future__ import annotations

import pytest

from app.config import settings
from app.sources import aggregator as agg
from app.sources.aggregator import (
    AggregateResult,
    merge_candidates,
    multi_source_search,
    prefilter,
    resolve_sources,
)
from app.sources.base import SourceOutcome


def _cand(**kw) -> dict:
    base = {
        "candidate_id": kw.get("candidate_id", "c"),
        "title": "T", "doi": None, "authors": [], "year": None,
        "abstract": None, "keywords": None, "containerTitle": None,
        "url": None, "citedByCount": None, "source": "openalex",
        "externalIds": [],
    }
    base.update(kw)
    return base


# --------------------------------------------------------------------------
# 跨源合并择优 (§4.3)
# --------------------------------------------------------------------------

def test_merge_by_doi_prefers_longer_abstract_and_unions_ids():
    a = _cand(candidate_id="a", doi="10.1/x", source="crossref", abstract="short",
              externalIds=[{"provider": "doi", "id_type": "doi", "external_id": "10.1/x"}])
    b = _cand(candidate_id="b", doi="10.1/X", source="openalex",  # 大小写归一后同 DOI
              abstract="a much longer abstract with detail", pdfUrl="https://oa/x.pdf",
              externalIds=[{"provider": "openalex", "id_type": "work_id", "external_id": "W1"}])
    merged = merge_candidates([a, b])
    assert len(merged) == 1
    m = merged[0]
    assert m["candidate_id"] == "a"  # base 稳定
    assert m["abstract"] == "a much longer abstract with detail"  # 更长择优
    assert m["pdfUrl"] == "https://oa/x.pdf"  # 补 OA 直链
    assert set(m["mergedSources"]) == {"crossref", "openalex"}  # 溯源双源
    ids = {(i["provider"], i["external_id"]) for i in m["externalIds"]}
    assert ("doi", "10.1/x") in ids and ("openalex", "W1") in ids  # 外部 id 并集


def test_merge_by_title_year_when_no_doi():
    a = _cand(candidate_id="a", title="Deep Learning Survey", year=2021, source="core")
    b = _cand(candidate_id="b", title="deep  learning survey", year=2021, source="hal")
    merged = merge_candidates([a, b])
    assert len(merged) == 1  # 标题归一+同年 → 合并


def test_merge_distinct_titles_not_merged():
    a = _cand(candidate_id="a", title="Paper A", year=2021)
    b = _cand(candidate_id="b", title="Paper B", year=2021)
    assert len(merge_candidates([a, b])) == 2


def test_merge_same_title_different_year_not_merged():
    a = _cand(candidate_id="a", title="Same", year=2020)
    b = _cand(candidate_id="b", title="Same", year=2021)
    assert len(merge_candidates([a, b])) == 2


# --------------------------------------------------------------------------
# 确定性预过滤 + 稳定排序 (§4.4)
# --------------------------------------------------------------------------

def test_prefilter_drops_below_year_floor_keeps_none_year():
    cands = [
        _cand(candidate_id="old", title="Old", year=2010),
        _cand(candidate_id="new", title="New", year=2022),
        _cand(candidate_id="noyear", title="NoYear", year=None),
    ]
    kept, truncated = prefilter(cands, since="2016-01-01", total_cap=100)
    ids = {c["candidate_id"] for c in kept}
    assert "old" not in ids  # 明确低于下限 → 丢
    assert "new" in ids and "noyear" in ids  # 缺年份保召回
    assert truncated == 0


def test_prefilter_stable_sort_year_desc_then_pdf_first():
    cands = [
        _cand(candidate_id="y2019", title="A", year=2019),
        _cand(candidate_id="y2023", title="B", year=2023),
        _cand(candidate_id="y2023pdf", title="C", year=2023, pdfUrl="https://p.pdf"),
    ]
    kept, _ = prefilter(cands, since=None, total_cap=100)
    order = [c["candidate_id"] for c in kept]
    assert order[0] == "y2023pdf"  # 同年有 PDF 优先
    assert order.index("y2023") < order.index("y2019")  # 年份倒序


def test_prefilter_truncates_to_total_cap():
    cands = [_cand(candidate_id=f"c{i}", title=f"T{i}", year=2020 + (i % 5)) for i in range(50)]
    kept, truncated = prefilter(cands, since=None, total_cap=10)
    assert len(kept) == 10 and truncated == 40


def test_prefilter_drops_empty_title():
    cands = [_cand(candidate_id="empty", title="  "), _cand(candidate_id="ok", title="Real")]
    kept, _ = prefilter(cands, since=None, total_cap=100)
    assert [c["candidate_id"] for c in kept] == ["ok"]


# --------------------------------------------------------------------------
# 源清单解析
# --------------------------------------------------------------------------

def test_resolve_sources_auto_skips_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "core_api_key", "")  # CORE 未配置
    selected, skipped = resolve_sources("auto")
    assert "core" not in selected
    assert "openalex" in selected  # 免鉴权源入选
    assert any(s["source"] == "core" and s["available"] is False for s in skipped)


def test_resolve_sources_explicit_flags_unknown():
    selected, skipped = resolve_sources(["openalex", "bogus"])
    assert selected == ["openalex"]
    assert any(s["source"] == "bogus" for s in skipped)


# --------------------------------------------------------------------------
# 并发 fan-out (打桩各源，验证聚合链路)
# --------------------------------------------------------------------------

async def test_multi_source_search_fans_out_merges_and_reports(monkeypatch):
    async def fake_search(name, query, *, limit, since=None):
        if name == "openalex":
            return SourceOutcome("openalex", True, [
                _cand(candidate_id="oa1", title="Shared", doi="10.9/s", year=2022,
                      abstract="oa abstract", source="openalex", pdfUrl="https://oa/s.pdf"),
                _cand(candidate_id="oa2", title="Only OA", year=2021, source="openalex"),
            ], total=2)
        if name == "core":
            return SourceOutcome("core", True, [
                _cand(candidate_id="co1", title="Shared", doi="10.9/S", year=2022,
                      abstract="core has a longer abstract here", source="core"),
            ], total=1)
        return SourceOutcome(name, False, [], unconfigured_reason="未配置")

    monkeypatch.setattr(agg, "search_source", fake_search)
    monkeypatch.setattr(agg, "resolve_sources", lambda s: (["openalex", "core", "europepmc"],
                                                           [{"source": "hal", "available": False,
                                                             "count": 0, "error": None, "reason": "未知"}]))
    result = await multi_source_search("auto", "shared", since="2016-01-01")
    assert isinstance(result, AggregateResult)
    assert result.total_before_merge == 3
    assert result.total_after_merge == 2  # Shared 跨源合并
    shared = next(c for c in result.candidates if c["title"] == "Shared")
    assert shared["abstract"] == "core has a longer abstract here"  # 择优更长
    assert shared["pdfUrl"] == "https://oa/s.pdf"  # 保 OA
    per = {p["source"]: p for p in result.per_source}
    assert per["europepmc"]["available"] is False  # 空源如实报告
    assert per["hal"]["available"] is False  # resolve 阶段 skip 的源也在册


async def test_multi_source_search_empty_query():
    result = await multi_source_search("auto", "   ")
    assert result.candidates == [] and result.query == ""


# --------------------------------------------------------------------------
# codex P2 复审吸收：gather 异常隔离 + no-year 保守不合
# --------------------------------------------------------------------------

def test_merge_same_title_both_no_year_not_merged():
    # 同标题、双方都缺 year → 保守不合 (可能是不同论文)。
    a = _cand(candidate_id="a", title="Same Title", year=None)
    b = _cand(candidate_id="b", title="Same Title", year=None)
    assert len(merge_candidates([a, b])) == 2


def test_merge_title_year_merges_only_when_both_have_year():
    a = _cand(candidate_id="a", title="X", year=2020)
    b = _cand(candidate_id="b", title="X", year=None)  # 一方缺 year → 不合
    assert len(merge_candidates([a, b])) == 2


async def test_multi_source_search_isolates_source_exception(monkeypatch):
    async def fake_search(name, query, *, limit, since=None):
        if name == "openalex":
            return SourceOutcome("openalex", True, [_cand(candidate_id="oa", title="OK", year=2022)])
        raise RuntimeError("boom from core")  # core 意外抛异常

    monkeypatch.setattr(agg, "search_source", fake_search)
    monkeypatch.setattr(agg, "resolve_sources", lambda s: (["openalex", "core"], []))
    result = await multi_source_search("auto", "q")
    # openalex 候选保住，core 异常转成 available=False + error，不拖垮整批。
    assert result.count == 1
    per = {p["source"]: p for p in result.per_source}
    assert per["core"]["available"] is False and "boom" in per["core"]["error"]
    assert per["openalex"]["available"] is True
