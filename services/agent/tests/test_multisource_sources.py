"""M1 源适配层单测 —— 净室实现 + 设计文档 §7 M1 协议断言。

断言重点 (codex 二审吸收):
  - normalize_meta_result source 参数化，且 sciverse 行为兼容;
  - EuropePMC 请求含 resultType=core;
  - CORE journals=[] 不产出 '[]';
  - Unpaywall 只走 /v2/{doi} (不碰 /search);
  - 缺 key 显式"未配置"而非静默 return []。
"""
from __future__ import annotations

import httpx
import pytest

from app.config import settings
from app.sciverse import normalize_meta_result
from app.sources.base import reconstruct_abstract
from app.sources.core import CoreSource, _venue, map_work as core_map
from app.sources.crossref import CrossrefSource
from app.sources.europepmc import EuropePmcSource
from app.sources.openalex import OpenAlexSource, _bare_doi, map_work as oa_map
from app.sources.registry import available_sources, search_source
from app.sources.semantic_scholar import SemanticScholarSource
from app.sources.unpaywall import UnpaywallClient


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------
# normalize_meta_result source 参数化
# --------------------------------------------------------------------------

def test_normalize_source_default_sciverse_behavior_compatible():
    row = {
        "title": "T", "doi": "10.1/x", "doc_id": "d1", "unique_id": "u1",
        "author": ["Alice"], "publication_published_year": 2024,
        "publication_venue_name_unified": "Venue", "citation_count": 3,
    }
    cand = normalize_meta_result(row)  # 默认 source="sciverse"
    assert cand["source"] == "sciverse" and cand["provider"] == "sciverse"
    assert cand["sciverseDocId"] == "d1" and cand["sciverseUniqueId"] == "u1"
    assert "pdfUrl" not in cand  # sciverse 行无 pdf_url → 不新增字段，结构不变
    ids = {(i["provider"], i["id_type"]) for i in cand["externalIds"]}
    assert ("sciverse", "doc_id") in ids and ("doi", "doi") in ids


def test_normalize_source_parameterized_non_sciverse():
    row = {
        "title": "Multi-source paper", "doi": "10.2/y",
        "author": ["Bob"], "publication_published_year": 2023.0,  # float 年份复用鲁棒性
        "source_id": "W999", "source_id_type": "work_id",
        "pdf_url": "https://oa.example.org/p.pdf", "oa_status": "gold",
    }
    cand = normalize_meta_result(row, "openalex")
    assert cand["source"] == "openalex" and cand["provider"] == "openalex"
    assert cand["sciverseDocId"] is None and cand["sciverseUniqueId"] is None
    assert cand["year"] == 2023  # float→int 复用 parse_year
    assert cand["pdfUrl"] == "https://oa.example.org/p.pdf"
    assert cand["oaStatus"] == "gold"
    ids = {(i["provider"], i["id_type"], i["external_id"]) for i in cand["externalIds"]}
    assert ("openalex", "work_id", "W999") in ids
    assert ("doi", "doi", "10.2/y") in ids
    assert cand["candidate_id"].startswith("openalex:")


# --------------------------------------------------------------------------
# CORE journals=[] 不产出 '[]'
# --------------------------------------------------------------------------

def test_core_venue_empty_journals_falls_back_to_publisher():
    assert _venue({"journals": [], "publisher": "Elsevier"}) == "Elsevier"


def test_core_venue_empty_journals_and_no_publisher_is_none_not_bracket():
    v = _venue({"journals": [], "publisher": None})
    assert v is None
    assert v != "[]"


def test_core_venue_prefers_journal_title():
    assert _venue({"journals": [{"title": "J. Test"}], "publisher": "Elsevier"}) == "J. Test"


def test_core_map_work_empty_journals_never_stringifies_list():
    cand = normalize_meta_result(core_map({"title": "X", "journals": [], "publisher": "ACM"}), "core")
    assert cand["containerTitle"] == "ACM"


# --------------------------------------------------------------------------
# 缺 key 显式"未配置"而非静默 []
# --------------------------------------------------------------------------

async def test_core_unconfigured_when_no_key(monkeypatch):
    monkeypatch.setattr(settings, "core_api_key", "")
    ok, reason = CoreSource().configured()
    assert ok is False and reason and "CORE_API_KEY" in reason
    outcome = await CoreSource().search("bibliometrics", limit=5)
    assert outcome.available is False  # 非静默：available=False + reason
    assert outcome.unconfigured_reason and outcome.candidates == []


async def test_unpaywall_unconfigured_when_no_email(monkeypatch):
    monkeypatch.setattr(settings, "unpaywall_email", "")
    ok, reason = UnpaywallClient().configured()
    assert ok is False and "UNPAYWALL_EMAIL" in reason
    assert await UnpaywallClient().lookup("10.1/x") is None


def test_available_sources_marks_core_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "core_api_key", "")
    rows = {r["source"]: r for r in available_sources()}
    assert rows["core"]["configured"] is False
    assert rows["openalex"]["configured"] is True  # 免鉴权源恒可用
    assert "reason" in rows["core"]


# --------------------------------------------------------------------------
# EuropePMC resultType=core
# --------------------------------------------------------------------------

async def test_europepmc_request_includes_resultType_core():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"resultList": {"result": []}})

    src = EuropePmcSource(client=_mock_client(handler))
    await src.search("smart structures", limit=10, since="2020-01-01")
    assert captured["params"].get("resultType") == "core"
    assert "PUB_YEAR:[2020 TO 2100]" in captured["params"].get("query", "")


async def test_europepmc_maps_core_fields_and_pdf():
    sample = {
        "resultList": {"result": [{
            "id": "PMC1", "source": "MED", "pmid": "123", "doi": "10.9/z",
            "title": "EPMC paper", "abstractText": "abstract here",
            "authorList": {"author": [{"fullName": "Zoe Q"}]},
            "pubYear": "2021", "journalInfo": {"journal": {"title": "Nature"}},
            "citedByCount": 7,
            "fullTextUrlList": {"fullTextUrl": [
                {"documentStyle": "html", "url": "https://x/html"},
                {"documentStyle": "pdf", "url": "https://x/full.pdf"},
            ]},
        }]}
    }

    def handler(request):
        return httpx.Response(200, json=sample)

    src = EuropePmcSource(client=_mock_client(handler))
    outcome = await src.search("q", limit=5)
    assert outcome.available and outcome.count == 1
    cand = outcome.candidates[0]
    assert cand["source"] == "europepmc"
    assert cand["abstract"] == "abstract here"
    assert cand["containerTitle"] == "Nature"
    assert cand["pdfUrl"] == "https://x/full.pdf"


# --------------------------------------------------------------------------
# Unpaywall 只走 /v2/{doi}
# --------------------------------------------------------------------------

async def test_unpaywall_only_hits_v2_doi_endpoint(monkeypatch):
    monkeypatch.setattr(settings, "unpaywall_email", "me@example.org")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={
            "oa_status": "green",
            "best_oa_location": {"url_for_pdf": "https://oa/p.pdf", "url": "https://oa/land"},
        })

    hit = await UnpaywallClient(client=_mock_client(handler)).lookup("10.1/abc")
    assert captured["path"] == "/v2/10.1/abc"
    assert "search" not in captured["path"]
    assert captured["params"].get("email") == "me@example.org"
    assert hit and hit.pdf_url == "https://oa/p.pdf" and hit.oa_status == "green"


# --------------------------------------------------------------------------
# OpenAlex 归一 (倒排摘要 / DOI 剥壳 / OA pdf)
# --------------------------------------------------------------------------

def test_reconstruct_abstract_from_inverted_index():
    inv = {"Hello": [0], "world": [1], "again": [2]}
    assert reconstruct_abstract(inv) == "Hello world again"
    assert reconstruct_abstract(None) is None
    assert reconstruct_abstract({}) is None


def test_openalex_bare_doi_strips_url_prefix():
    assert _bare_doi("https://doi.org/10.1/x") == "10.1/x"
    assert _bare_doi("10.1/x") == "10.1/x"
    assert _bare_doi(None) is None


def test_openalex_map_work_reconstructs_abstract_and_oa_pdf():
    work = {
        "id": "https://openalex.org/W42", "doi": "https://doi.org/10.1/w",
        "title": "OA work", "publication_year": 2022, "publication_date": "2022-03-01",
        "abstract_inverted_index": {"deep": [0], "learning": [1]},
        "authorships": [{"author": {"display_name": "Ann"}}],
        "primary_location": {"source": {"display_name": "ICML"}, "landing_page_url": "https://land"},
        "best_oa_location": {"pdf_url": "https://oa/w.pdf"},
        "open_access": {"oa_status": "green"},
        "cited_by_count": 5,
    }
    cand = normalize_meta_result(oa_map(work), "openalex")
    assert cand["abstract"] == "deep learning"
    assert cand["doi"] == "10.1/w"
    assert cand["pdfUrl"] == "https://oa/w.pdf"
    assert cand["containerTitle"] == "ICML"
    assert cand["candidate_id"] == f"openalex:{__import__('hashlib').sha256(b'W42').hexdigest()[:16]}"


# --------------------------------------------------------------------------
# Crossref metadata-only (无 pdf) + JATS 摘要清洗
# --------------------------------------------------------------------------

async def test_crossref_metadata_only_no_pdf_and_cleans_jats():
    sample = {"message": {"total-results": 1, "items": [{
        "DOI": "10.5/c", "title": ["Crossref paper"],
        "abstract": "<jats:p>Clean me</jats:p>",
        "author": [{"given": "J", "family": "Doe"}],
        "published": {"date-parts": [[2019, 5, 1]]},
        "container-title": ["J. Cross"], "is-referenced-by-count": 2,
    }]}}

    def handler(request):
        return httpx.Response(200, json=sample)

    outcome = await CrossrefSource(client=_mock_client(handler)).search("q", limit=5)
    cand = outcome.candidates[0]
    assert cand["source"] == "crossref"
    assert cand["abstract"] == "Clean me"  # JATS 标签清除
    assert cand["year"] == 2019
    assert "pdfUrl" not in cand  # metadata-only：不产 PDF


# --------------------------------------------------------------------------
# 未知源
# --------------------------------------------------------------------------

async def test_search_unknown_source_is_unavailable():
    outcome = await search_source("bogus", "q", limit=5)
    assert outcome.available is False and outcome.candidates == []


# --------------------------------------------------------------------------
# codex P1/P2 复审吸收：sciverse url 严格兼容 / 429 重试 / Unpaywall 可见
# --------------------------------------------------------------------------

def test_normalize_sciverse_url_none_without_doi_behavior_compatible():
    # 无 DOI 的 sciverse row：url 必须仍是 None（不回落 row['url']），字节不动契约。
    cand = normalize_meta_result({"title": "No DOI", "url": "https://sciverse/landing"})
    assert cand["url"] is None
    assert "pdfUrl" not in cand and "oaStatus" not in cand


def test_normalize_non_sciverse_url_falls_back_to_row_url():
    cand = normalize_meta_result({"title": "OA", "url": "https://oa/landing", "source_id": "X"}, "core")
    assert cand["url"] == "https://oa/landing"


async def test_get_json_retries_on_429_then_succeeds(monkeypatch):
    import app.sources.base as base_mod

    async def _no_sleep(_):  # 打桩退避，保持测试即时
        return None

    monkeypatch.setattr(base_mod.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(settings, "multisource_max_retries", 3)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, json={"err": "rate"})
        return httpx.Response(200, json={"data": [{"title": "S2 paper", "paperId": "p1"}]})

    src = SemanticScholarSource(client=_mock_client(handler))
    outcome = await src.search("q", limit=5)
    assert calls["n"] == 3  # 前两次 429 重试，第三次成功
    assert outcome.available and outcome.count == 1


async def test_get_json_exhausts_retries_returns_last_429(monkeypatch):
    import app.sources.base as base_mod

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(base_mod.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(settings, "multisource_max_retries", 2)

    def handler(request):
        return httpx.Response(429, json={"err": "rate"})

    src = SemanticScholarSource(client=_mock_client(handler))
    outcome = await src.search("q", limit=5)
    assert outcome.available is True and outcome.count == 0
    assert outcome.error and "429" in outcome.error


def test_available_sources_exposes_unpaywall_enrichment(monkeypatch):
    monkeypatch.setattr(settings, "unpaywall_email", "")
    rows = {r["source"]: r for r in available_sources()}
    assert rows["unpaywall"]["role"] == "enrichment"
    assert rows["unpaywall"]["configured"] is False
    assert all(rows[s]["role"] == "search" for s in ("openalex", "core", "europepmc"))
