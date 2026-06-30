import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.main import app, get_r_client
from app.config import settings
from app.db import Base, get_session
from app import models  # noqa: F401 — 注册 ORM 映射 (勿用 import app.models, 会重绑 app 遮蔽 FastAPI 实例)

_VALID_CID = "11111111-1111-4111-8111-111111111111"


class FakeR:
    """内存版 r-analysis, 模拟契约行为, 不需要真实 R 服务。"""

    def __init__(self):
        self.up = True
        self.corpora: dict[str, dict] = {}

    async def health(self):
        return self.up

    async def parse(self, content: bytes, filename: str, dbsource: str):
        if b"BAD" in content:
            return 422, {"corpusId": "22222222-2222-4222-8222-222222222222",
                         "status": "failed", "error": "解析失败",
                         "schemaVersion": 1, "dbsource": dbsource}
        self.corpora[_VALID_CID] = {"status": "ready", "documentCount": 3, "dbsource": dbsource}
        return 200, {"corpusId": _VALID_CID, "status": "ready", "documentCount": 3,
                     "schemaVersion": 1, "dbsource": dbsource,
                     "createdAt": "2026-05-21T00:00:00Z"}

    async def get_corpus(self, corpus_id: str):
        if corpus_id not in self.corpora:
            return 404, {"code": "CORPUS_NOT_FOUND", "message": "语料不存在"}
        c = self.corpora[corpus_id]
        return 200, {"corpusId": corpus_id, "status": c["status"], "schemaVersion": 1,
                     "documentCount": c["documentCount"], "dbsource": c["dbsource"],
                     "createdAt": "2026-05-21T00:00:00Z"}

    async def get_overview(self, corpus_id: str):
        if corpus_id not in self.corpora:
            return 404, {"code": "CORPUS_NOT_FOUND", "message": "语料不存在"}
        return 200, {"schemaVersion": 1, "corpusId": corpus_id,
                     "stats": {"documents": 3, "sources": 2, "authors": 3,
                               "avgCitationsPerDoc": 4.0, "timespanFrom": 2019,
                               "timespanTo": 2022},
                     "annualProduction": [{"year": 2019, "articles": 1},
                                          {"year": 2022, "articles": 2}]}

    async def get_records(self, corpus_id: str, limit: int = 40):
        if corpus_id not in self.corpora:
            return 404, {"code": "CORPUS_NOT_FOUND", "message": "语料不存在"}
        return 200, {"corpusId": corpus_id, "records": [
            {"idx": 1, "title": "Bibliometric study", "authors": "ARIA M;CUCCURULLO C",
             "year": 2017, "doi": "10.1016/j.joi.2017.08.007"},
            {"idx": 2, "title": "Science mapping", "authors": "SMITH J", "year": 2020},
        ]}

    async def get_sources(self, corpus_id: str):
        if corpus_id not in self.corpora:
            return 404, {"code": "CORPUS_NOT_FOUND", "message": "语料不存在"}
        return 200, {"schemaVersion": 1, "corpusId": corpus_id,
                     "topSources": [{"source": "J Informetrics", "articles": 5}],
                     "hIndex": [{"source": "J Informetrics", "h": 3}],
                     "bradford": [{"source": "J Informetrics", "zone": "Zone 1", "freq": 5}]}

    async def get_authors(self, corpus_id: str):
        if corpus_id not in self.corpora:
            return 404, {"code": "CORPUS_NOT_FOUND", "message": "语料不存在"}
        return 200, {"schemaVersion": 1, "corpusId": corpus_id,
                     "topAuthors": [{"author": "ARIA M", "articles": 4}],
                     "hIndex": [{"author": "ARIA M", "h": 3}],
                     "lotka": {"beta": 2.1, "distribution": [{"articles": 1, "authors": 100}]}}

    async def get_documents(self, corpus_id: str):
        if corpus_id not in self.corpora:
            return 404, {"code": "CORPUS_NOT_FOUND", "message": "语料不存在"}
        return 200, {"schemaVersion": 1, "corpusId": corpus_id,
                     "topCited": [{"title": "T", "author": "A", "year": 2017, "cited": 99}],
                     "keywords": [{"term": "bibliometrics", "freq": 10}]}

    # --- A4 高级图信封 (默认 available:true; 用 corpus 标志位模拟降级) ---
    async def get_author_production(self, corpus_id: str):
        if corpus_id not in self.corpora:
            return 404, {"code": "CORPUS_NOT_FOUND", "message": "语料不存在"}
        return 200, {"available": True, "schemaVersion": 1, "corpusId": corpus_id,
                     "data": {"authors": ["ARIA M"], "years": [2019, 2020],
                              "cells": [{"author": "ARIA M", "year": 2019, "articles": 1},
                                        {"author": "ARIA M", "year": 2020, "articles": 2}]}}

    async def get_keyword_trend(self, corpus_id: str):
        if corpus_id not in self.corpora:
            return 404, {"code": "CORPUS_NOT_FOUND", "message": "语料不存在"}
        # 模拟 missing_field 降级 (仍 HTTP 200)
        return 200, {"available": False, "reason": "missing_field",
                     "missingField": "DE", "schemaVersion": 1, "corpusId": corpus_id,
                     "message": "当前语料缺少字段「DE」, 无法生成该图。",
                     "howto": "可从 OpenAlex/WoS 导入含关键词的题录。"}

    async def get_cited_refs(self, corpus_id: str):
        if corpus_id not in self.corpora:
            return 404, {"code": "CORPUS_NOT_FOUND", "message": "语料不存在"}
        return 200, {"available": True, "schemaVersion": 1, "corpusId": corpus_id,
                     "data": [{"ref": "LOUGHRAN T, 2011, J FINANC", "count": 34}]}

    # --- A5 高级图② 信封 (默认 available:true; evolution 模拟 not_enough_data 降级) ---
    async def get_thematic(self, corpus_id: str):
        if corpus_id not in self.corpora:
            return 404, {"code": "CORPUS_NOT_FOUND", "message": "语料不存在"}
        return 200, {"available": True, "schemaVersion": 1, "corpusId": corpus_id,
                     "data": {"clusters": [
                         {"label": "textual analysis", "centrality": 12.0,
                          "density": 11.0, "freq": 47}]}}

    async def get_evolution(self, corpus_id: str):
        if corpus_id not in self.corpora:
            return 404, {"code": "CORPUS_NOT_FOUND", "message": "语料不存在"}
        # 模拟 not_enough_data 降级 (年份跨度不足切不出周期; 仍 HTTP 200)
        return 200, {"available": False, "reason": "not_enough_data",
                     "schemaVersion": 1, "corpusId": corpus_id,
                     "message": "年份跨度不足, 无法切分出至少 2 个时间周期。"}

    async def get_histcite(self, corpus_id: str):
        if corpus_id not in self.corpora:
            return 404, {"code": "CORPUS_NOT_FOUND", "message": "语料不存在"}
        return 200, {"available": True, "schemaVersion": 1, "corpusId": corpus_id,
                     "data": {"nodes": [
                         {"id": "1", "year": 2010, "label": "HANLEY KW, 2010", "localCites": 29},
                         {"id": "2", "year": 2013, "label": "LOUGHRAN T, 2013", "localCites": 33}],
                         "edges": [{"from": "2", "to": "1"}]}}

    async def get_threefield(self, corpus_id: str):
        if corpus_id not in self.corpora:
            return 404, {"code": "CORPUS_NOT_FOUND", "message": "语料不存在"}
        return 200, {"available": True, "schemaVersion": 1, "corpusId": corpus_id,
                     "data": {"nodes": [
                         {"name": "A:ARIA M", "layer": 0},
                         {"name": "K:BIBLIOMETRICS", "layer": 1},
                         {"name": "S:J INFORMETRICS", "layer": 2}],
                         "links": [
                         {"source": "A:ARIA M", "target": "K:BIBLIOMETRICS", "value": 3},
                         {"source": "K:BIBLIOMETRICS", "target": "S:J INFORMETRICS", "value": 2}]}}

    _GRAPH = {"nodes": [{"id": "a", "label": "a", "value": 5.0},
                        {"id": "b", "label": "b", "value": 3.0}],
              "edges": [{"source": "a", "target": "b", "weight": 2.0}]}

    async def get_conceptual(self, corpus_id: str, limit: int = 100):
        if corpus_id not in self.corpora:
            return 404, {"code": "CORPUS_NOT_FOUND", "message": "语料不存在"}
        return 200, {"schemaVersion": 1, "corpusId": corpus_id,
                     "network": "co-occurrence-keywords", "graph": self._GRAPH}

    async def get_intellectual(self, corpus_id: str, limit: int = 100):
        if corpus_id not in self.corpora:
            return 404, {"code": "CORPUS_NOT_FOUND", "message": "语料不存在"}
        return 200, {"schemaVersion": 1, "corpusId": corpus_id,
                     "network": "co-citation-references", "graph": self._GRAPH}

    async def get_social(self, corpus_id: str, limit: int = 100):
        if corpus_id not in self.corpora:
            return 404, {"code": "CORPUS_NOT_FOUND", "message": "语料不存在"}
        return 200, {"schemaVersion": 1, "corpusId": corpus_id,
                     "authorCollab": self._GRAPH, "countryCollab": self._GRAPH}

    async def get_cite(self, corpus_id: str, style: str = "apa", limit: int = 200):
        if corpus_id not in self.corpora:
            return 404, {"code": "CORPUS_NOT_FOUND", "message": "语料不存在"}
        return 200, {"schemaVersion": 1, "corpusId": corpus_id, "style": style,
                     "citations": ["Aria, M. (2017). Bibliometrix. J Informetrics."]}

    async def from_topic(self, query: str, n: int, since: str, with_refs: bool):
        if not query:
            return 400, {"code": "VALIDATION_ERROR", "message": "缺少 query"}
        if query == "__noresults__":
            return 422, {"code": "NO_RESULTS", "message": "OpenAlex 未检索到结果"}
        cid = "33333333-3333-4333-8333-333333333333"
        self.corpora[cid] = {"status": "ready", "documentCount": n, "dbsource": "wos"}
        return 200, {"corpusId": cid, "status": "ready", "documentCount": n,
                     "schemaVersion": 1, "dbsource": "wos",
                     "createdAt": "2026-05-21T00:00:00Z"}

    async def from_refs(self, papers: list, with_refs: bool):
        cid = "44444444-4444-4444-8444-444444444444"
        self.corpora[cid] = {"status": "ready", "documentCount": len(papers), "dbsource": "wos"}
        return 200, {"corpusId": cid, "status": "ready", "documentCount": len(papers),
                     "schemaVersion": 1, "dbsource": "wos",
                     "createdAt": "2026-05-21T00:00:00Z",
                     "matched": len(papers), "unmatched": 0}

    async def parse_from_records(self, records: list):
        if not records:
            return 400, {"code": "VALIDATION_ERROR", "message": "缺少 records 字段或数组为空"}
        # 允许测试模拟失败：若第一条记录含 "__fail__" title
        if records and (records[0].get("title") or "") == "__fail__":
            cid = "ffffffff-ffff-4fff-8fff-ffffffffffff"
            return 422, {"corpusId": cid, "status": "failed",
                         "error": "records 建库失败: mock failure",
                         "schemaVersion": 1, "dbsource": "bibliocn"}
        cid = "55555555-5555-4555-8555-555555555555"
        doc_count = len(records)
        self.corpora[cid] = {"status": "ready", "documentCount": doc_count, "dbsource": "bibliocn"}
        return 200, {"corpusId": cid, "status": "ready", "documentCount": doc_count,
                     "schemaVersion": 1, "dbsource": "bibliocn",
                     "createdAt": "2026-05-21T00:00:00Z"}


async def _make_test_engine():
    """创建指向测试库的 async engine 并 create_all；返回 (engine, sessionmaker)。

    供 session / session_factory 两个 fixture 共用，避免三套建库逻辑。
    调用方负责: create_all 已由此函数完成；用毕需 drop_all + engine.dispose()。
    """
    engine = create_async_engine(settings.test_database_url, pool_pre_ping=True)
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, factory


@pytest_asyncio.fixture
async def session():
    """每测试建空库 → 提供 AsyncSession → 测试后 drop_all 清理。

    供所有 DB 仓储测试（test_repo_*.py、test_db_models.py）共用；
    每次测试独立 create_all/drop_all，保证完全隔离。
    """
    engine, factory = await _make_test_engine()
    async with factory() as s:
        yield s

    async with engine.begin() as c:
        await c.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory():
    """每测试建空库 → yield async_sessionmaker（工厂）→ drop_all 清理。

    供 controller / tools 层测试：调用方自行 `async with session_factory() as s:` 开会话。
    与 session fixture 共用同一建库逻辑（_make_test_engine），保证完全隔离。
    """
    engine, factory = await _make_test_engine()
    yield factory

    async with engine.begin() as c:
        await c.run_sync(Base.metadata.drop_all)
    await engine.dispose()


def pytest_configure(config):
    """注册自定义 marker, 避免未知 marker 警告。"""
    config.addinivalue_line(
        "markers",
        "allow_real_llm_router: 该测试需覆盖 LLMRouter.has_any_key()==True 分支, "
        "opt-out 全局离线强制 (见 _no_real_llm fixture)。",
    )


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch, request):
    """默认禁止测试打真实 LLM: 端点强制 FakeStreamClient (离线/确定/无成本)。

    .env 现含真实 DEEPSEEK_API_KEY, 否则 get_llm_client(None) 会打真实 API。
    需要真实 LLM 的测试 (harness 真实冒烟) 直接用 harness.llm, 不经 app.main.get_llm_client, 不受影响。

    另: 综述链 (app.review.read 的 map / synthesis 的 reduce) 经 LLMRouter.has_any_key()
    判定真假 LLM; .env 有 key 时 map 阶段会打真实 DeepSeek (烧 token、依赖网络、损害可复现)。
    这里把 has_any_key 强制为 False, 使 review/read/synthesis 全程走 FakeLLM, 测试离线确定。
    真实冒烟测试 (test_harness_llm) 自带「无 key 跳过」逻辑, 强制 False 后跳过, 不受影响。

    opt-out (codex P2): 需覆盖「有 key」分支的测试可标 @pytest.mark.allow_real_llm_router,
    届时不强制 has_any_key=False (该测试自行 patch 路由/key, 自负真实调用之责)。
    """
    from app.llm import FakeStreamClient
    monkeypatch.setattr("app.main.get_llm_client", lambda *a, **k: FakeStreamClient())
    if request.node.get_closest_marker("allow_real_llm_router") is None:
        monkeypatch.setattr("app.harness.llm.LLMRouter.has_any_key", lambda self: False)


@pytest.fixture
def fake_r():
    return FakeR()


@pytest_asyncio.fixture
async def client(fake_r):
    """HTTP 测试客户端；同时 override:
      - get_r_client → FakeR（离线 R 分析服务）
      - get_session  → 测试库 session（避免 REST 端点测试打开发库）

    改为 async fixture 以便在 setup/teardown 中驱动 _make_test_engine（async）。
    与 sync TestClient 兼容：TestClient 本身是同步的，但 fixture 可以是 async。
    """
    engine, factory = await _make_test_engine()

    async def _override_get_session():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_r_client] = lambda: fake_r
    app.dependency_overrides[get_session] = _override_get_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
