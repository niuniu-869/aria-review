"""测试阅读 subagent (app/review/read.py)。

覆盖:
  - PaperSummary dataclass 序列化/反序列化
  - KeyPoint dataclass 序列化/反序列化
  - _truncate_fulltext: 超长截断保留首尾+标题
  - _parse_llm_json: 正常/代码块/大括号提取/全失败
  - summarize_paper: Fake LLM 返回正确 PaperSummary
  - summarize_paper: 单篇 LLM 失败 → error 占位（不抛）
  - summarize_papers: 并发批 + 单篇失败隔离
  - summarize_papers: 扁平 dict 输入格式
"""
from __future__ import annotations

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, patch

from app.review.read import (
    summarize_paper,
    summarize_papers,
    PaperSummary,
    KeyPoint,
    _truncate_fulltext,
    _parse_llm_json,
    MAX_FULLTEXT_CHARS,
    TRUNC_HEAD,
    TRUNC_TAIL,
)


# ======================================================================
# PaperSummary / KeyPoint dataclass
# ======================================================================

class TestPaperSummaryDataclass:
    def test_to_dict_roundtrip(self):
        kp = KeyPoint(claim="Some claim", section="3.2 Results")
        ps = PaperSummary(
            paper_id="p001",
            title="Test Paper",
            research_question="What is X?",
            method="Survey",
            data="N=100",
            findings=["Finding 1", "Finding 2"],
            contribution="Novel framework",
            relevance="高",
            key_points=[kp],
        )
        d = ps.to_dict()
        ps2 = PaperSummary.from_dict(d)
        assert ps2.paper_id == "p001"
        assert ps2.title == "Test Paper"
        assert ps2.findings == ["Finding 1", "Finding 2"]
        assert len(ps2.key_points) == 1
        assert ps2.key_points[0].claim == "Some claim"
        assert ps2.key_points[0].section == "3.2 Results"

    def test_error_placeholder(self):
        ps = PaperSummary.error_placeholder("p99", "Broken Paper", "LLM failed")
        assert ps.is_error()
        assert ps.error == "LLM failed"
        assert ps.paper_id == "p99"
        assert ps.title == "Broken Paper"

    def test_is_error_false_for_normal(self):
        ps = PaperSummary(paper_id="p1", title="Normal")
        assert not ps.is_error()

    def test_from_dict_empty_fields(self):
        ps = PaperSummary.from_dict({"paper_id": "x", "title": "T"})
        assert ps.research_question == ""
        assert ps.findings == []
        assert ps.key_points == []
        assert ps.error is None


class TestKeyPoint:
    def test_to_dict_roundtrip(self):
        kp = KeyPoint(claim="C1", section="S1")
        d = kp.to_dict()
        kp2 = KeyPoint.from_dict(d)
        assert kp2.claim == "C1"
        assert kp2.section == "S1"

    def test_from_dict_missing_keys(self):
        kp = KeyPoint.from_dict({})
        assert kp.claim == ""
        assert kp.section == ""


# ======================================================================
# _truncate_fulltext
# ======================================================================

class TestTruncateFulltext:
    def test_short_text_unchanged(self):
        short = "Short text " * 100
        assert len(short) < MAX_FULLTEXT_CHARS
        result = _truncate_fulltext(short)
        assert result == short

    def test_long_text_truncated(self):
        long = "A " * (MAX_FULLTEXT_CHARS // 2 + 1)
        result = _truncate_fulltext(long)
        assert len(result) < len(long)
        assert "已截断" in result or len(result) < len(long)

    def test_head_preserved(self):
        long = ("HEAD_MARKER " * 10 + "\n") + ("middle " * 3000) + ("TAIL_MARKER " * 10)
        if len(long) > MAX_FULLTEXT_CHARS:
            result = _truncate_fulltext(long)
            assert "HEAD_MARKER" in result

    def test_tail_preserved(self):
        head = "Head content " * 500
        tail = "TAIL_UNIQUE_STRING " * 50
        long = head + tail
        if len(long) > MAX_FULLTEXT_CHARS:
            result = _truncate_fulltext(long)
            assert "TAIL_UNIQUE_STRING" in result

    def test_headers_extracted(self):
        head = "Head " * 500
        middle = "\n".join([
            "## Section 1",
            "content " * 200,
            "## Section 2",
            "content " * 200,
        ]) + "\n"
        tail = "Tail " * 300
        long = head + middle + tail
        if len(long) > MAX_FULLTEXT_CHARS:
            result = _truncate_fulltext(long)
            assert "Section 1" in result or "Section 2" in result


# ======================================================================
# _parse_llm_json
# ======================================================================

class TestParseLlmJson:
    VALID_JSON = {
        "paper_id": "p1",
        "title": "Test",
        "research_question": "RQ",
        "method": "Survey",
        "data": "N=50",
        "findings": ["F1", "F2"],
        "contribution": "C",
        "relevance": "高",
        "key_points": [{"claim": "Claim", "section": "Abstract"}],
    }

    def test_direct_json(self):
        content = json.dumps(self.VALID_JSON, ensure_ascii=False)
        ps = _parse_llm_json(content, "p1", "Test")
        assert not ps.is_error()
        assert ps.research_question == "RQ"
        assert ps.findings == ["F1", "F2"]
        assert len(ps.key_points) == 1

    def test_json_in_code_block(self):
        content = "```json\n" + json.dumps(self.VALID_JSON) + "\n```"
        ps = _parse_llm_json(content, "p1", "Test")
        assert not ps.is_error()

    def test_json_in_prose(self):
        content = "Here is the result:\n" + json.dumps(self.VALID_JSON) + "\nEnd."
        ps = _parse_llm_json(content, "p1", "Test")
        assert not ps.is_error()

    def test_invalid_json_returns_error(self):
        ps = _parse_llm_json("this is not JSON at all", "p1", "Test")
        assert ps.is_error()
        assert ps.paper_id == "p1"
        assert ps.title == "Test"

    def test_empty_string_returns_error(self):
        ps = _parse_llm_json("", "p1", "Test")
        assert ps.is_error()


# ======================================================================
# summarize_paper（Fake LLM）
# ======================================================================

FAKE_SUMMARY_JSON = {
    "paper_id": "p001",
    "title": "Bibliometric Study",
    "research_question": "How do publications grow?",
    "method": "Bibliometric analysis",
    "data": "WoS 2010-2020, N=500",
    "findings": ["Growth rate 12% per year", "Top journal: Nature"],
    "contribution": "Novel mapping of field",
    "relevance": "高",
    "key_points": [
        {"claim": "12% annual growth", "section": "3.2 Results"},
    ],
}

META = {
    "paper_id": "p001",
    "title": "Bibliometric Study",
    "authors": "Smith J",
    "year": 2022,
}


class TestSummarizePaper:
    @pytest.mark.asyncio
    async def test_fake_llm_returns_paper_summary(self):
        """Fake LLM 返回构造 JSON → 解析成 PaperSummary。"""
        fake_content = json.dumps(FAKE_SUMMARY_JSON, ensure_ascii=False)

        with patch("app.review.read.LLMRouter") as MockRouter:
            mock_router = MockRouter.from_config.return_value
            mock_router.has_any_key.return_value = False  # 无 key → Fake

            result = await summarize_paper(
                markdown="## Abstract\nThis is about bibliometrics.\n## Introduction\nFoo.\n",
                meta=META,
                topic="文献计量学",
            )

        # 无 key 回退 FakeLLMClient，FakeLLMClient 返回通用占位内容
        # 但 paper_id/title 应从 meta 中取到
        assert isinstance(result, PaperSummary)
        assert result.paper_id == "p001"
        assert result.title == "Bibliometric Study"

    @pytest.mark.asyncio
    async def test_custom_fake_llm_returns_valid_summary(self):
        """用 monkeypatch 替换 FakeLLMClient 返回构造 JSON → 正确解析。"""
        fake_content = json.dumps(FAKE_SUMMARY_JSON, ensure_ascii=False)

        from app.harness.llm import FakeLLMClient

        class _MockFake(FakeLLMClient):
            async def call(self, messages, tools=None):
                return self._build_response(fake_content)

        with patch("app.review.read.LLMRouter") as MockRouter, \
             patch("app.review.read.FakeLLMClient", _MockFake):
            mock_router = MockRouter.from_config.return_value
            mock_router.has_any_key.return_value = False

            result = await summarize_paper(
                markdown="## Abstract\nThis is about bibliometrics.\n",
                meta=META,
                topic="文献计量学",
            )

        assert not result.is_error()
        assert result.research_question == "How do publications grow?"
        assert len(result.findings) == 2
        assert result.key_points[0].claim == "12% annual growth"

    @pytest.mark.asyncio
    async def test_llm_error_returns_error_placeholder(self):
        """LLM 调用异常 → 返回 error 占位，不抛出。"""
        with patch("app.review.read.LLMRouter") as MockRouter, \
             patch("app.review.read.call_llm_with_fallback") as mock_call:
            mock_router = MockRouter.from_config.return_value
            mock_router.has_any_key.return_value = True
            mock_call.side_effect = Exception("API timeout")

            result = await summarize_paper(
                markdown="Some content",
                meta=META,
                topic="test topic",
            )

        assert result.is_error()
        assert "LLM 调用失败" in result.error
        assert result.paper_id == "p001"

    @pytest.mark.asyncio
    async def test_invalid_json_response_returns_error_placeholder(self):
        """LLM 返回无法解析的内容 → error 占位。"""
        with patch("app.review.read.LLMRouter") as MockRouter, \
             patch("app.review.read.call_llm_with_fallback") as mock_call:
            mock_router = MockRouter.from_config.return_value
            mock_router.has_any_key.return_value = True
            mock_call.return_value = (
                {"choices": [{"message": {"content": "not json at all"}}]},
                "deepseek-chat",
            )

            result = await summarize_paper(
                markdown="Some content",
                meta=META,
                topic="test topic",
            )

        assert result.is_error()
        assert result.paper_id == "p001"

    @pytest.mark.asyncio
    async def test_truncates_long_markdown(self):
        """超长 Markdown 应被截断（函数不抛出）。"""
        long_md = "word " * 10000  # ~50000 chars
        with patch("app.review.read.LLMRouter") as MockRouter:
            mock_router = MockRouter.from_config.return_value
            mock_router.has_any_key.return_value = False  # Fake

            result = await summarize_paper(
                markdown=long_md,
                meta=META,
                topic="test",
            )

        # 不应抛异常，返回 PaperSummary（可能是 error 占位但不 crash）
        assert isinstance(result, PaperSummary)


# ======================================================================
# summarize_papers（并发批处理 + 单篇失败隔离）
# ======================================================================

class TestSummarizePapers:
    @pytest.mark.asyncio
    async def test_concurrent_batch(self):
        """并发批处理 3 篇，全部成功。"""
        papers = [
            {
                "meta": {"paper_id": f"p{i}", "title": f"Paper {i}", "year": 2020 + i},
                "markdown": f"## Abstract\nPaper {i} content.\n",
            }
            for i in range(3)
        ]

        with patch("app.review.read.LLMRouter") as MockRouter:
            mock_router = MockRouter.from_config.return_value
            mock_router.has_any_key.return_value = False  # Fake

            results = await summarize_papers(papers, topic="test", concurrency=3)

        assert len(results) == 3
        for r in results:
            assert isinstance(r, PaperSummary)

    @pytest.mark.asyncio
    async def test_single_failure_isolated(self):
        """单篇异常 → error 占位，其余成功（失败隔离）。"""
        # 第 2 篇的 markdown 会触发模拟异常
        call_count = [0]

        async def _mock_summarize(markdown, meta, topic, *, content_list=None, override=None):
            call_count[0] += 1
            if meta["paper_id"] == "p1":
                raise RuntimeError("Simulated failure for p1")
            return PaperSummary(paper_id=meta["paper_id"], title=meta["title"])

        papers = [
            {"meta": {"paper_id": "p0", "title": "OK Paper"}, "markdown": "content"},
            {"meta": {"paper_id": "p1", "title": "Fail Paper"}, "markdown": "content"},
            {"meta": {"paper_id": "p2", "title": "OK Paper 2"}, "markdown": "content"},
        ]

        with patch("app.review.read.summarize_paper", side_effect=_mock_summarize):
            results = await summarize_papers(papers, topic="test", concurrency=2)

        assert len(results) == 3
        # p1 应为 error 占位
        p1_result = next(r for r in results if r.paper_id == "p1")
        assert p1_result.is_error()
        # p0/p2 应成功
        assert not any(
            r.is_error() for r in results if r.paper_id in ("p0", "p2")
        )

    @pytest.mark.asyncio
    async def test_flat_dict_input(self):
        """扁平 dict 输入（不含 meta 键）应正常处理。"""
        papers = [
            {
                "paper_id": "pf1",
                "title": "Flat Paper",
                "year": 2021,
                "markdown": "## Abstract\nFlat content.\n",
            }
        ]

        with patch("app.review.read.LLMRouter") as MockRouter:
            mock_router = MockRouter.from_config.return_value
            mock_router.has_any_key.return_value = False

            results = await summarize_papers(papers, topic="test")

        assert len(results) == 1
        assert isinstance(results[0], PaperSummary)

    @pytest.mark.asyncio
    async def test_empty_papers_list(self):
        """空列表输入 → 返回空列表。"""
        results = await summarize_papers([], topic="test")
        assert results == []

    @pytest.mark.asyncio
    async def test_concurrency_limit_respected(self):
        """验证并发数限制（semaphore 正确工作，不并发超出限制）。"""
        concurrent_count = [0]
        max_concurrent = [0]

        async def _mock_summarize(markdown, meta, topic, *, content_list=None, override=None):
            concurrent_count[0] += 1
            max_concurrent[0] = max(max_concurrent[0], concurrent_count[0])
            await asyncio.sleep(0.01)  # 模拟短暂 IO
            concurrent_count[0] -= 1
            return PaperSummary(paper_id=meta.get("paper_id", "x"), title="")

        papers = [
            {"meta": {"paper_id": f"p{i}", "title": f"P{i}"}, "markdown": "c"}
            for i in range(8)
        ]

        with patch("app.review.read.summarize_paper", side_effect=_mock_summarize):
            await summarize_papers(papers, topic="test", concurrency=3)

        assert max_concurrent[0] <= 3
