"""Mock 单测：app/review/orchestrate.py run_review 编排逻辑

覆盖:
  - run_review: 正常路径 → review_md 拼接正确、stats 字段完整
  - run_review: validation_summary / evidence_refs 从事件流正确提取
  - run_review: 失败隔离 — 部分 summarize_papers 返回 error 占位不拖垮整体
  - run_review: 空 paper_markdowns → review_md 为空字符串（error 事件）
  - run_review: stats 字段 total_papers / success_summaries / error_summaries 正确
  - run_review: stats elapsed_* 字段均 >= 0

不打真实 API / MinerU。所有 LLM 调用通过 mock 替代。
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.review.read import PaperSummary, KeyPoint
from app.review.synthesis import ReviewEvent
from app.review.orchestrate import run_review


# ======================================================================
# 测试数据构造器
# ======================================================================

def make_paper_markdown(paper_id: str, title: str) -> dict:
    """构造 paper_markdowns 列表中的单条 dict（meta + markdown）。"""
    return {
        "meta": {
            "paper_id": paper_id,
            "title": title,
            "authors": "测试作者",
            "year": 2022,
        },
        "markdown": f"# {title}\n\n## Abstract\n\n这是 {title} 的摘要内容。\n\n## 结论\n\n结论文字。",
    }


def make_good_summary(paper_id: str, title: str) -> PaperSummary:
    """构造正常 PaperSummary（无错误）。"""
    return PaperSummary(
        paper_id=paper_id,
        title=title,
        research_question=f"{title} 的核心研究问题",
        method="DID",
        data="中国 A 股 2010-2020",
        findings=["发现 1", "发现 2"],
        contribution="理论贡献",
        relevance="高",
        key_points=[KeyPoint(claim="关键论断", section="4.1 结果")],
        error=None,
    )


def make_error_summary(paper_id: str, title: str) -> PaperSummary:
    """构造失败占位 PaperSummary（has error）。"""
    return PaperSummary.error_placeholder(
        paper_id=paper_id,
        title=title,
        error="LLM 调用超时（mock 失败）",
    )


SAMPLE_RECORDS = [
    {"idx": 1, "title": "Paper Alpha", "authors": "Smith J", "year": "2020", "doi": "10.1234/alpha"},
    {"idx": 2, "title": "Paper Beta", "authors": "Jones A", "year": "2021", "doi": "10.1234/beta"},
]


# ======================================================================
# 辅助：构造一个 AsyncGenerator，yield 指定的 ReviewEvent 列表
# ======================================================================

async def _fake_generate_review_ok(topic, summaries, records, *, template=None, override=None, strategy=None):
    """模拟 generate_review：正常产出 text_chunk + validation_summary + done。"""
    yield ReviewEvent("text_chunk", "## 引言\n\n本综述围绕主题 ")
    yield ReviewEvent("text_chunk", f"「{topic}」")
    yield ReviewEvent("text_chunk", " 对文献进行梳理。[1]\n\n## 主要发现\n\n发现若干重要规律。[2]\n")
    yield ReviewEvent("validation_summary", {
        "total_segments": 3,
        "valid_citations": 2,
        "fabricated_citations": 0,
        "fabricated_spans": [],
    })
    yield ReviewEvent("evidence_refs", [])
    yield ReviewEvent("done", {
        "segments_checked": 3,
        "evidence_count": 2,
        "fabricated_count": 0,
    })


async def _fake_generate_review_error(topic, summaries, records, *, template=None, override=None, strategy=None):
    """模拟 generate_review：返回 error 事件（无 summaries 场景）。"""
    yield ReviewEvent("error", "未提供任何 PaperSummary，无法生成综述")


async def _fake_generate_review_with_fabricated(topic, summaries, records, *, template=None, override=None, strategy=None):
    """模拟 generate_review：含伪造引用的场景。"""
    yield ReviewEvent("text_chunk", "研究表明 [99] 存在影响。\n")
    yield ReviewEvent("validation_summary", {
        "total_segments": 1,
        "valid_citations": 0,
        "fabricated_citations": 1,
        "fabricated_spans": ["[99]"],
    })
    yield ReviewEvent("done", {
        "segments_checked": 1,
        "evidence_count": 0,
        "fabricated_count": 1,
    })


# ======================================================================
# 测试：正常路径
# ======================================================================

class TestRunReviewNormalPath:

    @pytest.mark.asyncio
    async def test_review_md_concatenated_from_chunks(self):
        """text_chunk 事件正确拼接为 review_md。"""
        papers = [make_paper_markdown("p1", "Paper Alpha")]
        summaries = [make_good_summary("p1", "Paper Alpha")]

        with patch("app.review.orchestrate.summarize_papers", new=AsyncMock(return_value=summaries)), \
             patch("app.review.orchestrate.generate_review", side_effect=_fake_generate_review_ok):

            result = await run_review(
                topic="分析师跟踪测试",
                paper_markdowns=papers,
                records=SAMPLE_RECORDS,
            )

        assert "review_md" in result
        assert "## 引言" in result["review_md"]
        assert "分析师跟踪测试" in result["review_md"]
        assert "## 主要发现" in result["review_md"]

    @pytest.mark.asyncio
    async def test_validation_summary_extracted(self):
        """validation_summary 正确从事件流提取。"""
        papers = [make_paper_markdown("p1", "Paper Alpha")]
        summaries = [make_good_summary("p1", "Paper Alpha")]

        with patch("app.review.orchestrate.summarize_papers", new=AsyncMock(return_value=summaries)), \
             patch("app.review.orchestrate.generate_review", side_effect=_fake_generate_review_ok):

            result = await run_review(
                topic="test topic",
                paper_markdowns=papers,
                records=SAMPLE_RECORDS,
            )

        vs = result["validation_summary"]
        assert vs["total_segments"] == 3
        assert vs["valid_citations"] == 2
        assert vs["fabricated_citations"] == 0

    @pytest.mark.asyncio
    async def test_evidence_refs_extracted(self):
        """evidence_refs 字段存在且为 list。"""
        papers = [make_paper_markdown("p1", "Paper Alpha")]
        summaries = [make_good_summary("p1", "Paper Alpha")]

        with patch("app.review.orchestrate.summarize_papers", new=AsyncMock(return_value=summaries)), \
             patch("app.review.orchestrate.generate_review", side_effect=_fake_generate_review_ok):

            result = await run_review(
                topic="test topic",
                paper_markdowns=papers,
                records=SAMPLE_RECORDS,
            )

        assert "evidence_refs" in result
        assert isinstance(result["evidence_refs"], list)

    @pytest.mark.asyncio
    async def test_summaries_returned(self):
        """summaries 字段为 PaperSummary 列表。"""
        papers = [
            make_paper_markdown("p1", "Paper Alpha"),
            make_paper_markdown("p2", "Paper Beta"),
        ]
        summaries = [
            make_good_summary("p1", "Paper Alpha"),
            make_good_summary("p2", "Paper Beta"),
        ]

        with patch("app.review.orchestrate.summarize_papers", new=AsyncMock(return_value=summaries)), \
             patch("app.review.orchestrate.generate_review", side_effect=_fake_generate_review_ok):

            result = await run_review(
                topic="test topic",
                paper_markdowns=papers,
                records=SAMPLE_RECORDS,
            )

        assert result["summaries"] == summaries
        assert len(result["summaries"]) == 2


# ======================================================================
# 测试：stats 字段
# ======================================================================

class TestRunReviewStats:

    @pytest.mark.asyncio
    async def test_stats_total_papers(self):
        """stats.total_papers 等于输入论文数。"""
        papers = [make_paper_markdown(f"p{i}", f"Paper {i}") for i in range(3)]
        summaries = [make_good_summary(f"p{i}", f"Paper {i}") for i in range(3)]

        with patch("app.review.orchestrate.summarize_papers", new=AsyncMock(return_value=summaries)), \
             patch("app.review.orchestrate.generate_review", side_effect=_fake_generate_review_ok):

            result = await run_review(
                topic="test",
                paper_markdowns=papers,
                records=SAMPLE_RECORDS,
            )

        assert result["stats"]["total_papers"] == 3

    @pytest.mark.asyncio
    async def test_stats_success_summaries(self):
        """stats.success_summaries = 无 error 的摘要数。"""
        papers = [make_paper_markdown(f"p{i}", f"Paper {i}") for i in range(3)]
        summaries = [
            make_good_summary("p0", "Paper 0"),
            make_good_summary("p1", "Paper 1"),
            make_good_summary("p2", "Paper 2"),
        ]

        with patch("app.review.orchestrate.summarize_papers", new=AsyncMock(return_value=summaries)), \
             patch("app.review.orchestrate.generate_review", side_effect=_fake_generate_review_ok):

            result = await run_review("test", papers, SAMPLE_RECORDS)

        assert result["stats"]["success_summaries"] == 3
        assert result["stats"]["error_summaries"] == 0

    @pytest.mark.asyncio
    async def test_stats_review_chars(self):
        """stats.review_chars 等于 review_md 实际字数。"""
        papers = [make_paper_markdown("p1", "P1")]
        summaries = [make_good_summary("p1", "P1")]

        with patch("app.review.orchestrate.summarize_papers", new=AsyncMock(return_value=summaries)), \
             patch("app.review.orchestrate.generate_review", side_effect=_fake_generate_review_ok):

            result = await run_review("test", papers, SAMPLE_RECORDS)

        assert result["stats"]["review_chars"] == len(result["review_md"])

    @pytest.mark.asyncio
    async def test_stats_elapsed_fields_non_negative(self):
        """stats 中所有 elapsed_* 字段均 >= 0。"""
        papers = [make_paper_markdown("p1", "P1")]
        summaries = [make_good_summary("p1", "P1")]

        with patch("app.review.orchestrate.summarize_papers", new=AsyncMock(return_value=summaries)), \
             patch("app.review.orchestrate.generate_review", side_effect=_fake_generate_review_ok):

            result = await run_review("test", papers, SAMPLE_RECORDS)

        stats = result["stats"]
        assert stats["elapsed_map_s"] >= 0
        assert stats["elapsed_reduce_s"] >= 0
        assert stats["elapsed_total_s"] >= 0

    @pytest.mark.asyncio
    async def test_stats_citation_counts_from_validation_summary(self):
        """stats 引用计数从 validation_summary 事件正确读取。"""
        papers = [make_paper_markdown("p1", "P1")]
        summaries = [make_good_summary("p1", "P1")]

        with patch("app.review.orchestrate.summarize_papers", new=AsyncMock(return_value=summaries)), \
             patch("app.review.orchestrate.generate_review", side_effect=_fake_generate_review_ok):

            result = await run_review("test", papers, SAMPLE_RECORDS)

        # _fake_generate_review_ok 中 valid=2, fabricated=0
        assert result["stats"]["valid_citations"] == 2
        assert result["stats"]["fabricated_citations"] == 0

    @pytest.mark.asyncio
    async def test_stats_fabricated_citations_counted(self):
        """含伪造引用时 stats.fabricated_citations > 0。"""
        papers = [make_paper_markdown("p1", "P1")]
        summaries = [make_good_summary("p1", "P1")]

        with patch("app.review.orchestrate.summarize_papers", new=AsyncMock(return_value=summaries)), \
             patch("app.review.orchestrate.generate_review",
                   side_effect=_fake_generate_review_with_fabricated):

            result = await run_review("test", papers, SAMPLE_RECORDS)

        assert result["stats"]["fabricated_citations"] == 1
        assert result["stats"]["valid_citations"] == 0


# ======================================================================
# 测试：失败隔离
# ======================================================================

class TestRunReviewFailureIsolation:

    @pytest.mark.asyncio
    async def test_partial_error_summaries_do_not_break_reduce(self):
        """部分摘要为 error 占位时，reduce 阶段仍正常运行。"""
        papers = [
            make_paper_markdown("p1", "Paper Alpha"),
            make_paper_markdown("p2", "Paper Beta"),
            make_paper_markdown("p3", "Paper Gamma"),
        ]
        # p2 摘要失败（error 占位）
        summaries = [
            make_good_summary("p1", "Paper Alpha"),
            make_error_summary("p2", "Paper Beta"),
            make_good_summary("p3", "Paper Gamma"),
        ]

        with patch("app.review.orchestrate.summarize_papers", new=AsyncMock(return_value=summaries)), \
             patch("app.review.orchestrate.generate_review", side_effect=_fake_generate_review_ok):

            result = await run_review("test topic", papers, SAMPLE_RECORDS)

        # reduce 仍产出综述
        assert len(result["review_md"]) > 0
        # stats 正确统计
        assert result["stats"]["success_summaries"] == 2
        assert result["stats"]["error_summaries"] == 1
        assert result["stats"]["total_papers"] == 3

    @pytest.mark.asyncio
    async def test_all_error_summaries_produce_empty_or_error_review(self):
        """所有摘要均失败时，generate_review 仍被调用（含 error 占位），不崩溃。"""
        papers = [make_paper_markdown("p1", "P1")]
        summaries = [make_error_summary("p1", "P1")]

        with patch("app.review.orchestrate.summarize_papers", new=AsyncMock(return_value=summaries)), \
             patch("app.review.orchestrate.generate_review", side_effect=_fake_generate_review_ok):

            result = await run_review("test", papers, SAMPLE_RECORDS)

        # 不崩溃，返回完整结构
        assert "review_md" in result
        assert "stats" in result
        assert result["stats"]["error_summaries"] == 1

    @pytest.mark.asyncio
    async def test_empty_paper_markdowns_returns_empty_review(self):
        """空输入 → review_md 为空（error 事件），stats.total_papers=0，不崩溃。"""
        with patch("app.review.orchestrate.summarize_papers", new=AsyncMock(return_value=[])), \
             patch("app.review.orchestrate.generate_review",
                   side_effect=_fake_generate_review_error):

            result = await run_review("test", [], SAMPLE_RECORDS)

        assert result["review_md"] == ""
        assert result["stats"]["total_papers"] == 0
        assert result["stats"]["success_summaries"] == 0

    @pytest.mark.asyncio
    async def test_generate_review_exception_handled_gracefully(self):
        """generate_review 内部抛出异常时，run_review 不崩溃（error 事件已处理）。"""
        papers = [make_paper_markdown("p1", "P1")]
        summaries = [make_good_summary("p1", "P1")]

        async def _raise_inside(topic, summaries, records, **kwargs):
            yield ReviewEvent("text_chunk", "部分内容 ")
            # 模拟异常后的 error 事件（真实 generate_review 会捕获并 yield error）
            yield ReviewEvent("error", "内部异常: 模拟异常")

        with patch("app.review.orchestrate.summarize_papers", new=AsyncMock(return_value=summaries)), \
             patch("app.review.orchestrate.generate_review", side_effect=_raise_inside):

            result = await run_review("test", papers, SAMPLE_RECORDS)

        # 不崩溃，已产出的部分文本被保留
        assert "review_md" in result
        assert "部分内容" in result["review_md"]


# ======================================================================
# 测试：结果结构完整性
# ======================================================================

class TestRunReviewReturnStructure:

    @pytest.mark.asyncio
    async def test_return_keys_complete(self):
        """run_review 返回 dict 包含所有必需 key。"""
        papers = [make_paper_markdown("p1", "P1")]
        summaries = [make_good_summary("p1", "P1")]

        with patch("app.review.orchestrate.summarize_papers", new=AsyncMock(return_value=summaries)), \
             patch("app.review.orchestrate.generate_review", side_effect=_fake_generate_review_ok):

            result = await run_review("test", papers, SAMPLE_RECORDS)

        required_keys = {"review_md", "summaries", "validation_summary", "evidence_refs", "stats"}
        assert required_keys.issubset(result.keys())

    @pytest.mark.asyncio
    async def test_stats_keys_complete(self):
        """stats 包含所有预期字段。"""
        papers = [make_paper_markdown("p1", "P1")]
        summaries = [make_good_summary("p1", "P1")]

        with patch("app.review.orchestrate.summarize_papers", new=AsyncMock(return_value=summaries)), \
             patch("app.review.orchestrate.generate_review", side_effect=_fake_generate_review_ok):

            result = await run_review("test", papers, SAMPLE_RECORDS)

        stats = result["stats"]
        required_stats_keys = {
            "total_papers",
            "success_summaries",
            "error_summaries",
            "review_chars",
            "valid_citations",
            "fabricated_citations",
            "elapsed_map_s",
            "elapsed_reduce_s",
            "elapsed_total_s",
        }
        assert required_stats_keys.issubset(stats.keys())

    @pytest.mark.asyncio
    async def test_review_md_is_string(self):
        """review_md 始终为字符串类型。"""
        papers = [make_paper_markdown("p1", "P1")]
        summaries = [make_good_summary("p1", "P1")]

        with patch("app.review.orchestrate.summarize_papers", new=AsyncMock(return_value=summaries)), \
             patch("app.review.orchestrate.generate_review", side_effect=_fake_generate_review_ok):

            result = await run_review("test", papers, SAMPLE_RECORDS)

        assert isinstance(result["review_md"], str)

    @pytest.mark.asyncio
    async def test_concurrency_param_passed_through(self):
        """concurrency 参数正确传给 summarize_papers。"""
        papers = [make_paper_markdown("p1", "P1")]
        summaries = [make_good_summary("p1", "P1")]

        mock_summarize = AsyncMock(return_value=summaries)

        with patch("app.review.orchestrate.summarize_papers", new=mock_summarize), \
             patch("app.review.orchestrate.generate_review", side_effect=_fake_generate_review_ok):

            await run_review("test", papers, SAMPLE_RECORDS, concurrency=8)

        # 验证 summarize_papers 被调用时传入了 concurrency=8
        call_kwargs = mock_summarize.call_args.kwargs
        assert call_kwargs.get("concurrency") == 8
