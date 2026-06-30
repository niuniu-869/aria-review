"""SearchTool 单元测试（TDD — 先写测试再实现）。

覆盖:
1. execute topic 成功 → candidates 返回、candidate_id 来自 openalexId、emit 收到 search_results
2. candidate_id 回退：无 openalexId → doi: + hash[:16]
3. candidate_id 回退：无 openalexId 无 doi → title hash[:16]
4. emit=None 时不报错（纯 ToolResult 成功）
5. query 为空 → success=False
6. R 服务失败 (4xx/5xx) → success=False
7. R 返回空 results → 友好空结果 success=True
8. SearchTool 不在 write_tools（只读工具）
"""
from __future__ import annotations

import hashlib
import pytest

from app.tools.search import SearchTool
from app.harness.tools import ToolRegistry


# ======================================================================
# Fake r_client（仅实现 search_openalex）
# ======================================================================

class FakeRSearch:
    """只实现 search_openalex，供 SearchTool 测试用。"""

    def __init__(self, status: int = 200, body: dict | None = None):
        self._status = status
        self._body = body

    async def search_openalex(self, query: str, n: int, since: str):
        return self._status, self._body


# ======================================================================
# 辅助
# ======================================================================

def _make_candidate(
    openalex_id: str | None = "W1234567",
    doi: str | None = "10.1234/test",
    title: str = "Test Paper Title",
):
    return {
        "openalexId": openalex_id,
        "title": title,
        "authors": ["Alice Smith", "Bob Jones"],
        "year": 2022,
        "doi": doi,
        "containerTitle": "Journal of Testing",
        "url": "https://openalex.org/W1234567",
        "publicationDate": "2022-03-01",
        "abstract": "This is a test abstract.",
        "citedByCount": 42,
        "source": "openalex",
    }


def _sha256_hex16(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


# ======================================================================
# Test 1: 成功路径，candidate_id 来自 openalexId
# ======================================================================

@pytest.mark.asyncio
async def test_topic_success_candidate_id_from_openalex_id():
    """R 返回正常候选 → execute 成功；candidate_id == openalexId。"""
    fake_body = {
        "results": [
            _make_candidate(openalex_id="W9876543", doi="10.1/x"),
            _make_candidate(openalex_id="W1111111", doi="10.2/y", title="Second Paper"),
        ]
    }
    r = FakeRSearch(200, fake_body)
    tool = SearchTool(r)

    emitted = []

    async def emit(event):
        emitted.append(event)

    ctx = {"emit": emit}
    result = await tool.execute("topic", {"query": "analyst forecast", "limit": 5}, ctx)

    assert result.success, f"Expected success, got error: {result.error}"
    # candidate_id 来自 openalexId
    assert result.data, "data should not be empty"
    cands = result.data[0].get("candidates", [])
    assert len(cands) == 2
    assert cands[0]["candidate_id"] == "W9876543"
    assert cands[1]["candidate_id"] == "W1111111"

    # emit 应收到一个 search_results 事件
    assert len(emitted) == 1
    evt = emitted[0]
    assert evt["type"] == "search_results"
    assert evt["query"] == "analyst forecast"
    assert len(evt["candidates"]) == 2
    # summary 含计数
    assert "2" in result.summary
    # summary 含候选标题（至少其中一篇）—— 验证 LLM grounding 改进
    assert "Test Paper Title" in result.summary or "Second Paper" in result.summary
    # summary 逐条暴露 candidate_id —— LLM 才能按 ID 自筛导入
    assert "W9876543" in result.summary and "W1111111" in result.summary


# ======================================================================
# Test 2: candidate_id 回退到 doi hash
# ======================================================================

@pytest.mark.asyncio
async def test_candidate_id_fallback_to_doi_hash():
    """openalexId 为 None → candidate_id = 'doi:' + sha256(doi)[:16]。"""
    c = _make_candidate(openalex_id=None, doi="10.9999/fallback")
    r = FakeRSearch(200, {"results": [c]})
    tool = SearchTool(r)
    result = await tool.execute("topic", {"query": "test", "limit": 5}, {})
    assert result.success
    cands = result.data[0]["candidates"]
    expected = "doi:" + _sha256_hex16("10.9999/fallback")
    assert cands[0]["candidate_id"] == expected


# ======================================================================
# Test 3: candidate_id 回退到 title hash
# ======================================================================

@pytest.mark.asyncio
async def test_candidate_id_fallback_to_title_hash():
    """openalexId 和 doi 均为 None → candidate_id = sha256(title)[:16]。"""
    c = _make_candidate(openalex_id=None, doi=None, title="Unique Title No DOI")
    r = FakeRSearch(200, {"results": [c]})
    tool = SearchTool(r)
    result = await tool.execute("topic", {"query": "test", "limit": 5}, {})
    assert result.success
    cands = result.data[0]["candidates"]
    expected = _sha256_hex16("Unique Title No DOI")
    assert cands[0]["candidate_id"] == expected


# ======================================================================
# Test 4: emit=None 不报错
# ======================================================================

@pytest.mark.asyncio
async def test_emit_none_does_not_raise():
    """ctx 不含 emit（或 emit=None）→ 工具正常返回 success=True。"""
    r = FakeRSearch(200, {"results": [_make_candidate()]})
    tool = SearchTool(r)
    result = await tool.execute("topic", {"query": "test"}, {})
    assert result.success


# ======================================================================
# Test 5: query 为空 → success=False
# ======================================================================

@pytest.mark.asyncio
async def test_empty_query_returns_failure():
    """query 为空字符串 → execute 返回 success=False。"""
    r = FakeRSearch(200, {"results": []})
    tool = SearchTool(r)
    result = await tool.execute("topic", {"query": ""}, {})
    assert not result.success
    assert result.error is not None


# ======================================================================
# Test 6: R 服务失败
# ======================================================================

@pytest.mark.asyncio
async def test_r_failure_returns_failure():
    """R 服务返回 4xx/5xx → execute 返回 success=False（友好错误消息）。"""
    r = FakeRSearch(500, {"code": "R_SERVICE_ERROR", "message": "R 挂了"})
    tool = SearchTool(r)
    result = await tool.execute("topic", {"query": "analyst forecast"}, {})
    assert not result.success
    assert result.error is not None


# ======================================================================
# Test 7: R 返回空 results → 友好空结果 success=True
# ======================================================================

@pytest.mark.asyncio
async def test_empty_results_returns_empty_success():
    """R 返回空 results 列表 → success=True，data 为空/提示无结果。"""
    r = FakeRSearch(200, {"results": []})
    tool = SearchTool(r)
    result = await tool.execute("topic", {"query": "veryrarequery12345"}, {})
    assert result.success
    assert "0" in result.summary or "没有" in result.summary or "未找到" in result.summary


# ======================================================================
# Test 8: SearchTool 是只读工具，不在 write_tools
# ======================================================================

def test_search_tool_is_read_only():
    """SearchTool 注册进 ToolRegistry 后不应被标记为写工具。"""
    r = FakeRSearch()
    tool = SearchTool(r)
    reg = ToolRegistry()
    reg.register(tool)
    # 不调用 mark_write_tools("search")
    assert not reg.is_write_tool("search"), "SearchTool should NOT be a write tool"


# ======================================================================
# Test 9: limit / since 有默认值
# ======================================================================

@pytest.mark.asyncio
async def test_default_limit_and_since():
    """不传 limit/since 时，应使用默认值（limit=50，since 有合理默认），不报错。"""
    calls = []

    class RecordingR:
        async def search_openalex(self, query, n, since):
            calls.append({"query": query, "n": n, "since": since})
            return 200, {"results": []}

    tool = SearchTool(RecordingR())
    result = await tool.execute("topic", {"query": "test"}, {})
    assert result.success
    assert len(calls) == 1
    assert calls[0]["n"] == 50  # 默认 limit
    assert calls[0]["since"] is not None  # 有默认 since


@pytest.mark.asyncio
async def test_search_candidates_cached_in_context():
    """检索结果应缓存到 tool_context，供 project__import_search_results 批量入库。"""
    c1 = _make_candidate(openalex_id="W1", doi="10.1/a", title="Paper A")
    c2 = _make_candidate(openalex_id="W1", doi="10.1/a", title="Paper A")
    r = FakeRSearch(200, {"results": [c1, c2]})
    tool = SearchTool(r)
    ctx = {}

    result = await tool.execute("topic", {"query": "civil engineering"}, ctx)

    assert result.success
    assert len(ctx["search_candidates"]) == 1
    assert ctx["search_candidates"][0]["candidate_id"] == "W1"


# ======================================================================
# Test 10: function_definitions 合法
# ======================================================================

def test_search_tool_function_definitions():
    """SearchTool 注册后 to_function_definitions 包含 search__topic。"""
    tool = SearchTool(FakeRSearch())
    reg = ToolRegistry()
    reg.register(tool)
    defs = reg.get_function_definitions()
    names = {d["function"]["name"] for d in defs}
    assert "search__topic" in names
    for fd in defs:
        if fd["function"]["name"] == "search__topic":
            params = fd["function"]["parameters"]
            assert params["type"] == "object"
            assert "query" in params["properties"]
            assert "query" in params.get("required", [])


# ======================================================================
# Test 11: summary 含候选标题列表（LLM grounding）
# ======================================================================

@pytest.mark.asyncio
async def test_summary_enumerates_all_candidates_with_ids():
    """成功检索时，summary 应逐条枚举【全部】候选的标题 + candidate_id，
    供 LLM 逐条判相关性并按 ID 自筛导入（修复：旧版只预览前 5 条且无 ID，
    导致 prompts.py 要求的『只传相关 candidate_ids』无法落地）。"""
    titles = [f"Paper Title {i}" for i in range(1, 8)]
    results = [
        _make_candidate(openalex_id=f"W{i}", doi=f"10.1/{i}", title=t)
        for i, t in enumerate(titles, 1)
    ]
    r = FakeRSearch(200, {"results": results})
    tool = SearchTool(r)
    result = await tool.execute("topic", {"query": "machine learning"}, {})

    assert result.success
    # 全部 7 篇标题都应出现（不再只给前 5）
    for title in titles:
        assert title in result.summary, f"标题 '{title}' 应出现在 summary 中"
    # 每条候选的 candidate_id 都应出现，LLM 才能按 ID 自筛导入
    for i in range(1, 8):
        assert f"W{i}" in result.summary, f"candidate_id 'W{i}' 应出现在 summary 中"
    # summary 应引导 LLM 走 candidate_ids 自筛导入
    assert "import_search_results" in result.summary


# ======================================================================
# Test 12: candidate_id 防御 URL 形式 openalexId
# ======================================================================

@pytest.mark.asyncio
async def test_candidate_id_strips_url_prefix():
    """openalexId 为 URL 形式（https://openalex.org/W123）→ candidate_id 只取末段 W123。"""
    c = _make_candidate(openalex_id="https://openalex.org/W9988776", doi="10.1/x")
    r = FakeRSearch(200, {"results": [c]})
    tool = SearchTool(r)
    result = await tool.execute("topic", {"query": "test"}, {})

    assert result.success
    cands = result.data[0]["candidates"]
    assert cands[0]["candidate_id"] == "W9988776", (
        f"应剥去 URL 前缀，得到 'W9988776'，实际为 '{cands[0]['candidate_id']}'"
    )


# ======================================================================
# Test 13: search_openalex 抛出连接异常 → success=False
# ======================================================================

@pytest.mark.asyncio
async def test_connection_error_returns_failure():
    """r_client.search_openalex 抛出 Exception（连接失败）→ execute 返回 success=False。"""

    class BrokenRClient:
        async def search_openalex(self, query: str, n: int, since: str):
            raise Exception("Connection refused")

    tool = SearchTool(BrokenRClient())
    result = await tool.execute("topic", {"query": "resilience test"}, {})

    assert not result.success, "连接异常应返回 success=False"
    assert result.error is not None
    assert "不可达" in result.error or "Connection" in result.error


# ======================================================================
# Test 14: E — R 返回 {error, detail} 风格失败体，error 含 detail 文案
# ======================================================================

@pytest.mark.asyncio
async def test_r_error_detail_field_included_in_error_message():
    """R 失败体为 {error, detail}（R 服务风格）→ result.error 包含 detail 内容。"""
    r = FakeRSearch(500, {
        "error": "SEARCH_ERROR",
        "detail": "OpenAlex rate limit exceeded on /works endpoint",
    })
    tool = SearchTool(r)
    result = await tool.execute("topic", {"query": "analyst forecast"}, {})

    assert not result.success, "R 返回 5xx 应返回 success=False"
    assert result.error is not None
    # detail 文案应出现在 error 消息中（供 LLM 诊断）
    assert "OpenAlex rate limit" in result.error, (
        f"detail 应包含在 error 中，实际 error: {result.error!r}"
    )


@pytest.mark.asyncio
async def test_r_error_detail_truncated_to_200_chars():
    """R 失败体 detail 超长时截断至 200 字符，不泄露完整堆栈。"""
    long_detail = "x" * 500  # 超过 200 字符
    r = FakeRSearch(500, {"error": "ERR", "detail": long_detail})
    tool = SearchTool(r)
    result = await tool.execute("topic", {"query": "test"}, {})

    assert not result.success
    assert result.error is not None
    # error 不应超过合理长度（200 字符 detail + 周边文案）
    # 核心约束：detail 部分不超 200 字符（截断）
    assert len(result.error) < 700, (
        f"error 应被截断，实际长度 {len(result.error)}"
    )


# ======================================================================
# Test 15 (P1-2): limit=200 → 实际透传给 R 的 n ≤ 100
# ======================================================================

@pytest.mark.asyncio
async def test_limit_200_is_preserved_for_large_candidate_recall():
    """limit=200 时，SearchTool 应保留较大召回量，供用户筛选和后续分析。"""
    calls = []

    class RecordingR:
        async def search_openalex(self, query: str, n: int, since: str):
            calls.append(n)
            return 200, {"results": []}

    tool = SearchTool(RecordingR())
    result = await tool.execute("topic", {"query": "test", "limit": 200}, {})

    assert result.success, f"预期成功，实际: {result.error}"
    assert len(calls) == 1, "应调用一次 search_openalex"
    assert calls[0] == 200


# ======================================================================
# Test 16 (P1-1): R 返回 502 OPENALEX_UNAVAILABLE → 与"空结果"区分
# ======================================================================

@pytest.mark.asyncio
async def test_r_502_openalex_unavailable_distinct_from_empty():
    """R 返回 502（OpenAlex 不可达）→ success=False；与真空结果（200+空数组）完全不同。"""
    # 场景 A: OpenAlex 不可达（502）
    r_error = FakeRSearch(502, {
        "error": "OPENALEX_UNAVAILABLE",
        "message": "OpenAlex 返回 503: service unavailable",
        "status": 503,
    })
    tool_error = SearchTool(r_error)
    result_error = await tool_error.execute("topic", {"query": "test"}, {})

    assert not result_error.success, "502 应返回 success=False"
    assert result_error.error is not None
    # error 文案应明确表示服务故障，不应是"未找到"
    assert "未找到" not in (result_error.error or ""), (
        "502 的 error 不应与'未找到匹配文献'混淆"
    )

    # 场景 B: 真空（200 + 空数组）
    r_empty = FakeRSearch(200, {"results": []})
    tool_empty = SearchTool(r_empty)
    result_empty = await tool_empty.execute("topic", {"query": "veryrarequery999"}, {})

    assert result_empty.success, "真空结果（200 空数组）应返回 success=True"
    assert "未找到" in (result_empty.summary or "") or "0" in (result_empty.summary or ""), (
        "真空结果的 summary 应提示 0 结果"
    )


# ======================================================================
# Provider 路由测试（benchmark 优化：中文→sciverse / 纯英文→openalex / 配置感知 fallback）
# ======================================================================
from app.tools import search as _search_mod


def test_auto_provider_english_to_openalex():
    assert _search_mod._auto_provider("machine learning bearing fault diagnosis") == "openalex"
    assert _search_mod._auto_provider("GAN") == "openalex"


def test_auto_provider_chinese_to_sciverse_when_configured(monkeypatch):
    monkeypatch.setattr(_search_mod, "_sciverse_configured", lambda: True)
    assert _search_mod._auto_provider("地下结构抗连续倒塌") == "sciverse"
    assert _search_mod._auto_provider("transformer 模型 自然语言处理") == "sciverse"  # 混合含中文


def test_auto_provider_chinese_falls_back_when_sciverse_unconfigured(monkeypatch):
    # codex P1: Sciverse 未配置时, 中文也回退 openalex, 不因缺 token 直接失败
    monkeypatch.setattr(_search_mod, "_sciverse_configured", lambda: False)
    assert _search_mod._auto_provider("地下结构抗连续倒塌") == "openalex"


@pytest.mark.asyncio
async def test_omitted_provider_english_routes_to_openalex():
    """省略 provider + 英文 query → 走 openalex(FakeR) 成功。"""
    r = FakeRSearch(200, {"results": [_make_candidate()]})
    result = await SearchTool(r).execute("topic", {"query": "supply chain finance"}, {})
    assert result.success


@pytest.mark.asyncio
async def test_omitted_provider_chinese_routes_to_sciverse(monkeypatch):
    """省略 provider + 中文 query + sciverse 已配置 → 路由到 sciverse 分支, 不调用 openalex。"""
    monkeypatch.setattr(_search_mod, "_sciverse_configured", lambda: True)
    called = {"oa": False}

    class TrackR:
        async def search_openalex(self, *a, **k):
            called["oa"] = True
            return 200, {"results": []}

    tool = SearchTool(TrackR())

    async def fake_sv(self, action, query, limit, emit, override=None, ctx=None):
        return self._ok(action, data=[{"candidates": [], "total": 0, "query": query, "provider": "sciverse"}],
                        source="api", summary="sciverse ok")

    monkeypatch.setattr(SearchTool, "_execute_sciverse", fake_sv)
    result = await tool.execute("topic", {"query": "盈余管理与公司治理"}, {})
    assert result.success
    assert called["oa"] is False, "中文 query 应路由到 sciverse, 不应调用 openalex"
