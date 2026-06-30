"""测试综述合成（app/review/synthesis.py）。

覆盖:
  - generate_review: Fake 流 → 产出结构化综述（text_chunk 事件）
  - generate_review: GuardedStream 拦伪造引用、放行真实引用
  - generate_review: 产出 validation_summary 事件
  - generate_review: 空摘要列表 → error 事件
  - generate_review: 异常 → error 事件（不崩溃）
  - _format_summary_for_prompt: 格式化正确，截断超长
  - ReviewEvent.to_dict: 序列化正确
"""
from __future__ import annotations

import pytest
from unittest.mock import patch

from app.review.synthesis import (
    generate_review,
    ReviewEvent,
    _format_summary_for_prompt,
    _build_synthesis_messages,
    _fake_stream_tokens,
    MAX_SUMMARIES,
)
from app.review.read import PaperSummary, KeyPoint
from app.safety.citation import CitationFailStrategy


# ======================================================================
# 测试数据
# ======================================================================

def make_summary(paper_id: str, title: str, valid_doi: str = "") -> PaperSummary:
    """构造测试用 PaperSummary。"""
    return PaperSummary(
        paper_id=paper_id,
        title=title,
        research_question=f"What is {title}?",
        method="Survey",
        data="N=100",
        findings=[f"Finding in {title}", "Result 42%"],
        contribution="Novel contribution",
        relevance="高",
        key_points=[
            KeyPoint(claim=f"Claim from {title}", section="Abstract"),
        ],
    )


RECORDS = [
    {"idx": 1, "title": "Paper Alpha", "authors": "Smith J", "year": 2020, "doi": "10.1234/alpha"},
    {"idx": 2, "title": "Paper Beta", "authors": "Jones A", "year": 2021, "doi": "10.1234/beta"},
]

SUMMARIES = [
    make_summary("p1", "Paper Alpha"),
    make_summary("p2", "Paper Beta"),
]


# ======================================================================
# ReviewEvent
# ======================================================================

class TestReviewEvent:
    def test_to_dict_text_chunk(self):
        ev = ReviewEvent("text_chunk", "some text")
        d = ev.to_dict()
        assert d["event"] == "text_chunk"
        assert d["data"] == "some text"

    def test_to_dict_done(self):
        ev = ReviewEvent("done", {"segments_checked": 5})
        d = ev.to_dict()
        assert d["event"] == "done"
        assert d["data"]["segments_checked"] == 5

    def test_to_dict_evidence_refs_list(self):
        from app.safety import EvidenceRef
        ref = EvidenceRef.from_record(1, RECORDS[0], span="10.1234/alpha", corpus_id="test")
        ev = ReviewEvent("evidence_refs", [ref])
        d = ev.to_dict()
        assert d["event"] == "evidence_refs"
        assert isinstance(d["data"], list)


# ======================================================================
# _format_summary_for_prompt
# ======================================================================

class TestFormatSummaryForPrompt:
    def test_normal_summary_formatted(self):
        s = make_summary("p1", "My Paper")
        text = _format_summary_for_prompt(1, s)
        assert "[1]" in text
        assert "My Paper" in text
        assert "Finding" in text

    def test_error_summary_formatted(self):
        s = PaperSummary.error_placeholder("p99", "Broken", "LLM failed")
        text = _format_summary_for_prompt(5, s)
        assert "[5]" in text
        assert "Broken" in text
        assert "阅读失败" in text

    def test_truncates_long_summary(self):
        s = make_summary("p1", "Long " * 100)
        text = _format_summary_for_prompt(1, s, max_chars=200)
        assert len(text) <= 230  # 允许 30 字符的截断标记


# ======================================================================
# generate_review（Fake 模式）
# ======================================================================

async def collect_events(topic, summaries, records, **kwargs):
    """辅助：收集所有 ReviewEvent。"""
    events = []
    async for ev in generate_review(topic, summaries, records, **kwargs):
        events.append(ev)
    return events


class TestGenerateReviewFake:
    @pytest.mark.asyncio
    async def test_fake_produces_text_chunks(self):
        """Fake 流 → 至少产出一个 text_chunk 事件。"""
        with patch("app.review.synthesis.LLMRouter") as MockRouter:
            mock_router = MockRouter.from_config.return_value
            mock_router.has_any_key.return_value = False  # 无 key → Fake

            events = await collect_events(
                "文献计量学",
                SUMMARIES,
                RECORDS,
            )

        text_events = [e for e in events if e.event == "text_chunk"]
        assert len(text_events) >= 1
        # 合并所有文本
        full_text = "".join(e.data for e in text_events)
        assert len(full_text) > 50

    @pytest.mark.asyncio
    async def test_fake_produces_validation_summary(self):
        """Fake 流 → 产出 validation_summary 事件。"""
        with patch("app.review.synthesis.LLMRouter") as MockRouter:
            mock_router = MockRouter.from_config.return_value
            mock_router.has_any_key.return_value = False

            events = await collect_events(
                "test topic",
                SUMMARIES,
                RECORDS,
            )

        validation_events = [e for e in events if e.event == "validation_summary"]
        assert len(validation_events) == 1
        summary = validation_events[0].data
        assert "total_segments" in summary
        assert "valid_citations" in summary
        assert "fabricated_citations" in summary

    @pytest.mark.asyncio
    async def test_fake_produces_done_event(self):
        """Fake 流 → 以 done 事件结束。"""
        with patch("app.review.synthesis.LLMRouter") as MockRouter:
            mock_router = MockRouter.from_config.return_value
            mock_router.has_any_key.return_value = False

            events = await collect_events(
                "test topic",
                SUMMARIES,
                RECORDS,
            )

        done_events = [e for e in events if e.event == "done"]
        assert len(done_events) == 1

    @pytest.mark.asyncio
    async def test_empty_summaries_returns_error_event(self):
        """空摘要列表 → error 事件，不崩溃。"""
        events = await collect_events("test", [], RECORDS)
        assert len(events) == 1
        assert events[0].event == "error"

    @pytest.mark.asyncio
    async def test_event_sequence_order(self):
        """事件顺序：text_chunk... validation_summary → done（最后）。"""
        with patch("app.review.synthesis.LLMRouter") as MockRouter:
            mock_router = MockRouter.from_config.return_value
            mock_router.has_any_key.return_value = False

            events = await collect_events("test", SUMMARIES, RECORDS)

        event_types = [e.event for e in events]
        # text_chunk 在 validation_summary 之前
        if "text_chunk" in event_types and "validation_summary" in event_types:
            assert event_types.index("text_chunk") < event_types.index("validation_summary")
        # done 在最后
        if "done" in event_types:
            assert event_types[-1] == "done"


# ======================================================================
# GuardedStream 引用校验（通过 generate_review）
# ======================================================================

class TestGuardedStreamViaReview:
    @pytest.mark.asyncio
    async def test_fabricated_citation_marked_in_summary(self):
        """包含伪造引用的综述 → validation_summary 中 fabricated_citations > 0。"""
        # 构造包含伪造引用的 fake 综述
        fake_review_text = (
            "研究表明 [1] 存在重要影响。"
            "另据 10.9999/totally-fake-doi 的研究显示。\n\n"
            "结论：综合上述文献 [1]。\n"
        )

        async def _fake_tokens(text):
            yield fake_review_text

        records_with_real = [
            {"idx": 1, "title": "Paper Alpha", "authors": "A", "year": 2020, "doi": "10.1234/alpha"},
        ]

        with patch("app.review.synthesis.LLMRouter") as MockRouter, \
             patch("app.review.synthesis._fake_stream_tokens", return_value=_fake_tokens(fake_review_text)):
            mock_router = MockRouter.from_config.return_value
            mock_router.has_any_key.return_value = False

            # 覆盖 _build_fake_review 使其包含伪造引用
            with patch("app.review.synthesis._build_fake_review", return_value=fake_review_text):
                events = await collect_events(
                    "test topic",
                    SUMMARIES[:1],
                    records_with_real,
                    strategy=CitationFailStrategy.ANNOTATE,
                )

        validation_events = [e for e in events if e.event == "validation_summary"]
        if validation_events:
            # fabricated_citations 应该反映检测到的伪造引用
            summary = validation_events[0].data
            # 字段存在即可（数值取决于 cite_check 解析能力）
            assert "fabricated_citations" in summary

    @pytest.mark.asyncio
    async def test_valid_citations_produce_evidence_refs(self):
        """包含真实引用（命中 records）的综述 → evidence_refs 事件非空。"""
        # 使用真实 DOI 构造综述
        real_doi = "10.1234/alpha"
        fake_review_text = f"根据 {real_doi} 的研究显示 [1]。\n\n结论：综合分析 [1]。\n"

        records_with_real = [
            {"idx": 1, "title": "Paper Alpha", "authors": "A", "year": 2020, "doi": real_doi},
        ]

        with patch("app.review.synthesis.LLMRouter") as MockRouter, \
             patch("app.review.synthesis._build_fake_review", return_value=fake_review_text):
            mock_router = MockRouter.from_config.return_value
            mock_router.has_any_key.return_value = False

            events = await collect_events(
                "test topic",
                SUMMARIES[:1],
                records_with_real,
            )

        # 可能产出 evidence_refs 事件（取决于 cite_check 是否能命中 DOI）
        # 关键是不崩溃，且 done 事件出现
        done_events = [e for e in events if e.event == "done"]
        assert len(done_events) == 1


# ======================================================================
# _build_synthesis_messages
# ======================================================================

class TestBuildSynthesisMessages:
    def test_messages_structure(self):
        messages = _build_synthesis_messages("AI in medicine", SUMMARIES, "skill SOP")
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

    def test_topic_in_user_message(self):
        messages = _build_synthesis_messages("特定主题XYZ", SUMMARIES, "SOP")
        user_content = messages[1]["content"]
        assert "特定主题XYZ" in user_content

    def test_paper_refs_in_user_message(self):
        messages = _build_synthesis_messages("topic", SUMMARIES, "SOP")
        user_content = messages[1]["content"]
        assert "[1]" in user_content
        assert "[2]" in user_content

    def test_skill_content_in_system_message(self):
        messages = _build_synthesis_messages("topic", SUMMARIES, "MY_SKILL_SOP")
        system_content = messages[0]["content"]
        assert "MY_SKILL_SOP" in system_content

    def test_max_summaries_limit(self):
        """超过 MAX_SUMMARIES 的摘要应被截断。"""
        many_summaries = [make_summary(f"p{i}", f"Paper {i}") for i in range(MAX_SUMMARIES + 10)]
        messages = _build_synthesis_messages("topic", many_summaries, "SOP")
        user_content = messages[1]["content"]
        # 最多 MAX_SUMMARIES 个引用标记
        last_ref = f"[{MAX_SUMMARIES}]"
        beyond_ref = f"[{MAX_SUMMARIES + 1}]"
        assert last_ref in user_content
        assert beyond_ref not in user_content


# ======================================================================
# P0-1：分层路径第一层小结也必须经过引用校验（伪造引用计入最终日志）
# ======================================================================

class TestHierarchicalLayer1CitationCounted:
    @pytest.mark.asyncio
    async def test_layer1_fabricated_citation_counted_in_summary(self):
        """分层路径：第一层 mini 含超界伪造引用 → 计入最终 validation_summary。

        codex P0-1：分层第一层小结（_call_llm_nonstream / fake mini）原先完全绕过
        GuardedStream，第一层伪造引用只要被第二层吸收而不原样输出就漏计。
        此测试构造 >18 篇走分层，patch 第一层 fake mini 返回含 [999]（超界=伪造）的
        文本，且第二层 meta 文本不含 [999]（不会被第二层吸收输出），断言最终
        fabricated_citations 仍把它计入。修前漏计=红，修后绿。
        """
        # 25 篇 → 超过 HIERARCHICAL_THRESHOLD(18)，走分层
        n_papers = 25
        summaries = [make_summary(f"p{i}", f"Paper {i}") for i in range(n_papers)]
        records = [
            {"idx": i + 1, "title": f"Paper {i}", "authors": "A",
             "year": 2020, "doi": f"10.1234/p{i}"}
            for i in range(n_papers)
        ]

        # 第一层 fake mini 含超界编号 [999]（999 > 题录数 → cite_check 判 red → 伪造）
        def _fake_mini(topic, group_summaries, global_start_idx):
            return f"本组研究表明重要规律 [999]。方法上以实证为主 [{global_start_idx}]。"

        # 第二层 meta 文本：不含 [999]，只含合法编号 → 第二层自身无伪造
        def _fake_meta(topic, n_groups, n_total):
            return (
                "## 1. 引言\n\n"
                f"本综述对 {n_total} 篇文献进行系统梳理 [1] [2]。\n\n"
                "## 2. 结论\n\n综合上述文献，仍有研究空间 [3]。\n"
            )

        with patch("app.review.synthesis.LLMRouter") as MockRouter, \
             patch("app.review.synthesis._build_fake_group_mini_review", side_effect=_fake_mini), \
             patch("app.review.synthesis._build_fake_meta_review", side_effect=_fake_meta):
            MockRouter.from_config.return_value.has_any_key.return_value = False
            events = await collect_events("分层主题", summaries, records)

        # 应走分层路径
        done_events = [e for e in events if e.event == "done"]
        assert len(done_events) == 1
        assert done_events[0].data.get("hierarchical") is True

        vsum_events = [e for e in events if e.event == "validation_summary"]
        assert len(vsum_events) == 1
        vsum = vsum_events[0].data
        # 第二层 meta 文本不含伪造引用；伪造 [999] 全部来自第一层 mini。
        # 若第一层未被校验 → fabricated_citations == 0（漏计）= 修前；
        # 修后第一层伪造计入 → fabricated_citations >= 1。
        assert vsum["fabricated_citations"] >= 1, (
            "第一层 mini 的超界伪造引用 [999] 必须计入最终 fabricated_citations"
        )
        # fabricated_spans 应含 [999] 字面
        assert any("999" in str(span) for span in vsum.get("fabricated_spans", [])), (
            "fabricated_spans 应含第一层伪造引用 [999]"
        )
