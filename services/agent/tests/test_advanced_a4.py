"""A4 后端补算① — 三个信封端点透传 + 已有端点可选字段增量。

策略: 复用 conftest 的 FakeR(client fixture)。FakeR 默认:
  - author/production → available:true
  - documents/keyword-trend → available:false / missing_field (模拟 PDF 语料缺 DE)
  - documents/cited-refs → available:true
分别覆盖 envelope 三态 (true / false-降级 / 语料不存在 404)。
另对 FakeR 注入 g/m/tc + rank/cumPct + hIndex/annualGrowthRate, 断言已有端点透传增量字段。
"""
_MISSING = "99999999-9999-4999-8999-999999999999"


def _mk(client):
    return client.post("/projects/p/corpus",
                       files={"file": ("x.txt", b"c")},
                       data={"dbsource": "wos"}).json()["corpusId"]


# ---------------- 信封端点: available:true 透传 ----------------

def test_author_production_available_true(client):
    cid = _mk(client)
    r = client.get(f"/projects/p/corpus/{cid}/authors/production")
    assert r.status_code == 200
    b = r.json()
    assert b["available"] is True
    assert b["projectId"] == "p"
    assert b["corpusId"] == cid
    assert b["data"]["authors"] == ["ARIA M"]
    assert b["data"]["cells"][0]["articles"] == 1


def test_cited_refs_available_true(client):
    cid = _mk(client)
    r = client.get(f"/projects/p/corpus/{cid}/documents/cited-refs")
    assert r.status_code == 200
    b = r.json()
    assert b["available"] is True
    assert b["data"][0]["ref"].startswith("LOUGHRAN")
    assert b["data"][0]["count"] == 34


# ---------------- 信封端点: available:false 降级 (仍 HTTP 200) ----------------

def test_keyword_trend_missing_field_is_200(client):
    cid = _mk(client)
    r = client.get(f"/projects/p/corpus/{cid}/documents/keyword-trend")
    # 关键: available:false 也是 HTTP 200, 非 502
    assert r.status_code == 200
    b = r.json()
    assert b["available"] is False
    assert b["reason"] == "missing_field"
    assert b["missingField"] == "DE"
    assert "message" in b
    assert b["projectId"] == "p"


# ---------------- 信封端点: 语料不存在 → 404 (非信封) ----------------

def test_author_production_404(client):
    r = client.get(f"/projects/p/corpus/{_MISSING}/authors/production")
    assert r.status_code == 404
    assert r.json()["code"] == "CORPUS_NOT_FOUND"


def test_cited_refs_404(client):
    r = client.get(f"/projects/p/corpus/{_MISSING}/documents/cited-refs")
    assert r.status_code == 404


# ---------------- response_model 校验: available:false 时不应要求 data ----------------

def test_envelope_false_no_data_field(client):
    cid = _mk(client)
    r = client.get(f"/projects/p/corpus/{cid}/documents/keyword-trend")
    b = r.json()
    # 判别式联合: false 分支无 data, 不应被 response_model 拒绝
    assert "data" not in b or b.get("data") is None


# ---------------- 已有端点可选字段增量透传 ----------------

def test_sources_gmt_and_bradford_increment(client, fake_r):
    """sources hIndex 含 g/m/tc; bradford 含 rank/cumPct。"""
    async def _patched(corpus_id):
        if corpus_id not in fake_r.corpora:
            return 404, {"code": "CORPUS_NOT_FOUND", "message": "语料不存在"}
        return 200, {
            "schemaVersion": 1, "corpusId": corpus_id,
            "topSources": [{"source": "J Informetrics", "articles": 5}],
            "hIndex": [{"source": "J Informetrics", "h": 3, "g": 4, "m": 0.5, "tc": 40}],
            "bradford": [{"source": "J Informetrics", "zone": "Zone 1",
                          "freq": 5, "rank": 1, "cumPct": 12.5}],
        }
    fake_r.get_sources = _patched
    cid = _mk(client)
    r = client.get(f"/projects/p/corpus/{cid}/sources")
    assert r.status_code == 200
    b = r.json()
    h = b["hIndex"][0]
    assert h["g"] == 4 and h["m"] == 0.5 and h["tc"] == 40
    br = b["bradford"][0]
    assert br["rank"] == 1 and br["cumPct"] == 12.5


def test_sources_m_null_passthrough(client, fake_r):
    """m=null (缺基准年) 应透传为 None, 不报错。"""
    async def _patched(corpus_id):
        if corpus_id not in fake_r.corpora:
            return 404, {"code": "CORPUS_NOT_FOUND", "message": "语料不存在"}
        return 200, {
            "schemaVersion": 1, "corpusId": corpus_id,
            "topSources": [{"source": "X", "articles": 1}],
            "hIndex": [{"source": "X", "h": 1, "g": 1, "m": None, "tc": 2}],
            "bradford": [],
        }
    fake_r.get_sources = _patched
    cid = _mk(client)
    r = client.get(f"/projects/p/corpus/{cid}/sources")
    assert r.status_code == 200
    assert r.json()["hIndex"][0]["m"] is None


def test_authors_gmt_increment(client, fake_r):
    async def _patched(corpus_id):
        if corpus_id not in fake_r.corpora:
            return 404, {"code": "CORPUS_NOT_FOUND", "message": "语料不存在"}
        return 200, {
            "schemaVersion": 1, "corpusId": corpus_id,
            "topAuthors": [{"author": "ARIA M", "articles": 4}],
            "hIndex": [{"author": "ARIA M", "h": 3, "g": 5, "m": 0.6, "tc": 50}],
            "lotka": {"beta": 2.1, "distribution": [{"articles": 1, "authors": 100}]},
        }
    fake_r.get_authors = _patched
    cid = _mk(client)
    r = client.get(f"/projects/p/corpus/{cid}/authors")
    assert r.status_code == 200
    h = r.json()["hIndex"][0]
    assert h["g"] == 5 and h["m"] == 0.6 and h["tc"] == 50


def test_overview_hindex_growth_increment(client, fake_r):
    async def _patched(corpus_id):
        if corpus_id not in fake_r.corpora:
            return 404, {"code": "CORPUS_NOT_FOUND", "message": "语料不存在"}
        return 200, {
            "schemaVersion": 1, "corpusId": corpus_id,
            "stats": {"documents": 74, "sources": 30, "authors": 100,
                      "avgCitationsPerDoc": 5.0, "timespanFrom": 2016,
                      "timespanTo": 2026, "hIndex": 21, "annualGrowthRate": 12.9},
            "annualProduction": [{"year": 2016, "articles": 1}],
        }
    fake_r.get_overview = _patched
    cid = _mk(client)
    r = client.get(f"/projects/p/corpus/{cid}/overview")
    assert r.status_code == 200
    s = r.json()["stats"]
    assert s["hIndex"] == 21 and s["annualGrowthRate"] == 12.9


def test_overview_increment_optional_when_absent(client):
    """已有端点缺增量字段时仍正常 (向后兼容)。FakeR 默认 overview 无 hIndex。"""
    cid = _mk(client)
    r = client.get(f"/projects/p/corpus/{cid}/overview")
    assert r.status_code == 200
    s = r.json()["stats"]
    # 默认 FakeR overview 无 hIndex → None / 缺省
    assert s.get("hIndex") is None


def test_a4_available_payload_null_items_return_200(client, fake_r):
    """A4 图表信封中的 R 数据叶子为 null 时也不得触发 500。"""
    cid = _mk(client)

    async def author_production_with_null(_corpus_id):
        return 200, {
            "available": True,
            "schemaVersion": 1,
            "corpusId": cid,
            "data": {
                "authors": [None],
                "years": [None],
                "cells": [{"author": None, "year": None, "articles": None}],
            },
        }

    async def keyword_trend_with_null(_corpus_id):
        return 200, {
            "available": True,
            "schemaVersion": 1,
            "corpusId": cid,
            "data": {
                "years": [None],
                "terms": [None],
                "cells": [{"year": None, "term": None, "freq": None}],
            },
        }

    async def cited_refs_with_null(_corpus_id):
        return 200, {
            "available": True,
            "schemaVersion": 1,
            "corpusId": cid,
            "data": [{"ref": None, "count": None}],
        }

    fake_r.get_author_production = author_production_with_null
    fake_r.get_keyword_trend = keyword_trend_with_null
    fake_r.get_cited_refs = cited_refs_with_null

    production = client.get(f"/projects/p/corpus/{cid}/authors/production")
    trend = client.get(f"/projects/p/corpus/{cid}/documents/keyword-trend")
    refs = client.get(f"/projects/p/corpus/{cid}/documents/cited-refs")

    assert production.status_code == 200
    assert production.json()["data"]["cells"][0]["author"] is None
    assert trend.status_code == 200
    assert trend.json()["data"]["cells"][0]["term"] is None
    assert refs.status_code == 200
    assert refs.json()["data"][0]["ref"] is None
