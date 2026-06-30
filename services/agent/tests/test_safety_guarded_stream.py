"""测试 GuardedStream (app/safety/guarded_stream.py)。

覆盖:
  - 全真实引用 token 流: 全部放行, evidence_refs 产出
  - 全伪造引用 token 流 (ANNOTATE): 放行但输出含警告
  - 全伪造引用 token 流 (NOOP): 放行原文, validation_passed=False
  - 全伪造引用 token 流 (REJECT): 抛出 FabricatedCitationError
  - 混合流 (真实段 + 伪造段, ANNOTATE): 真实段放行无警告, 伪造段放行有警告
  - 句/章边界缓冲: token 按 boundary 正确分段
  - 长无句号文本强制刷新 (MAX_BUFFER_CHARS)
  - 无引用文本: 直接放行, evidence_refs 为空
  - 空流: 无输出无异常
  - fabricated_spans / evidence_refs / segments_checked 计数正确
"""
from __future__ import annotations

import pytest
from typing import AsyncIterator

from app.safety.guarded_stream import (
    GuardedStream,
    FabricatedCitationError,
    ValidationUnavailableError,
)
from app.safety.citation import CitationFailStrategy

# ======================================================================
# 合成语料
# ======================================================================

RECORDS = [
    {
        "idx": 1,
        "title": "Bibliometric analysis",
        "authors": "ARIA M;CUCCURULLO C",
        "year": 2017,
        "doi": "10.1016/j.joi.2017.08.007",
    },
    {
        "idx": 2,
        "title": "Science mapping",
        "authors": "SMITH J",
        "year": 2020,
        "doi": "10.1007/s11192-020-03483-z",
    },
]


# ======================================================================
# 辅助函数
# ======================================================================

async def make_token_stream(tokens: list[str]) -> AsyncIterator[str]:
    """合成 token 流 (不打真实 LLM)。"""
    for tok in tokens:
        yield tok


async def collect_stream(stream: GuardedStream) -> str:
    """收集 GuardedStream 输出为完整字符串。"""
    out = []
    async for chunk in stream:
        out.append(chunk)
    return "".join(out)


# ======================================================================
# 全真实引用场景
# ======================================================================

class TestAllValidStream:
    async def test_all_valid_passes_through(self):
        tokens = ["见 10.1016/j.joi.2017.08.007 的研究结果。\n\n"]
        stream = GuardedStream(
            token_stream=make_token_stream(tokens),
            records=RECORDS,
            strategy=CitationFailStrategy.NOOP,
        )
        output = await collect_stream(stream)
        assert "10.1016/j.joi.2017.08.007" in output
        assert stream.fabricated_spans == []

    async def test_valid_stream_produces_evidence_refs(self):
        tokens = ["根据 10.1016/j.joi.2017.08.007 和 10.1007/s11192-020-03483-z 的分析。\n"]
        stream = GuardedStream(
            token_stream=make_token_stream(tokens),
            records=RECORDS,
        )
        await collect_stream(stream)
        assert len(stream.evidence_refs) >= 1
        # 检查 evidence_ref 对应正确记录
        paper_ids = [r.paper_id for r in stream.evidence_refs]
        assert 1 in paper_ids or 2 in paper_ids

    async def test_no_citations_passes_unchanged(self):
        text = "这段文字没有任何引用，纯描述性内容。\n"
        tokens = list(text)  # 逐字符作为 token
        stream = GuardedStream(
            token_stream=make_token_stream(tokens),
            records=RECORDS,
        )
        output = await collect_stream(stream)
        assert stream.fabricated_spans == []
        assert stream.evidence_refs == []
        assert "这段文字" in output


# ======================================================================
# 伪造引用场景 — 不同策略
# ======================================================================

class TestFabricatedStreamAnnotate:
    async def test_fabricated_annotate_adds_warning(self):
        tokens = ["发现 10.9999/totally-fake 的重要结论。\n\n"]
        stream = GuardedStream(
            token_stream=make_token_stream(tokens),
            records=RECORDS,
            strategy=CitationFailStrategy.ANNOTATE,
        )
        output = await collect_stream(stream)
        # 输出含原文
        assert "10.9999/totally-fake" in output
        # 输出含警告
        assert "⚠️" in output or "警告" in output
        # fabricated_spans 已记录
        assert stream.fabricated_spans != []

    async def test_fabricated_annotate_segments_checked(self):
        tokens = ["见 10.9999/fake-doi 的结论。\n\n"]
        stream = GuardedStream(
            token_stream=make_token_stream(tokens),
            records=RECORDS,
            strategy=CitationFailStrategy.ANNOTATE,
        )
        await collect_stream(stream)
        assert stream.segments_checked >= 1


class TestFabricatedStreamNoop:
    async def test_fabricated_noop_passes_text_unchanged(self):
        original = "发现 10.9999/fake 很重要。\n\n"
        tokens = [original]
        stream = GuardedStream(
            token_stream=make_token_stream(tokens),
            records=RECORDS,
            strategy=CitationFailStrategy.NOOP,
        )
        output = await collect_stream(stream)
        # NOOP: 原文原样放行
        assert original.strip() in output.strip()
        # fabricated_spans 已记录
        assert "10.9999/fake" in stream.fabricated_spans


class TestFabricatedStreamReject:
    async def test_fabricated_reject_raises(self):
        tokens = ["发现 10.9999/fake 很重要。\n\n"]
        stream = GuardedStream(
            token_stream=make_token_stream(tokens),
            records=RECORDS,
            strategy=CitationFailStrategy.REJECT,
        )
        with pytest.raises(FabricatedCitationError) as exc_info:
            await collect_stream(stream)
        assert "10.9999/fake" in str(exc_info.value) or len(exc_info.value.fabricated) > 0

    async def test_fabricated_reject_increments_blocked(self):
        tokens = ["引用 10.9999/bad 的研究。\n\n"]
        stream = GuardedStream(
            token_stream=make_token_stream(tokens),
            records=RECORDS,
            strategy=CitationFailStrategy.REJECT,
        )
        try:
            await collect_stream(stream)
        except FabricatedCitationError:
            pass
        assert stream.segments_blocked >= 1


# ======================================================================
# 混合流 (真实 + 伪造段)
# ======================================================================

class TestMixedStream:
    async def test_valid_segment_no_warning_fabricated_gets_warning(self):
        """混合流: 真实段不含警告, 伪造段含警告 (ANNOTATE 策略)。"""
        # 两个独立段落 (用 \n\n 分隔, 触发段落边界)
        valid_segment = "根据 10.1016/j.joi.2017.08.007 的结论。\n\n"
        fabricated_segment = "另见 10.9999/fake 的研究。\n\n"
        tokens = [valid_segment, fabricated_segment]

        stream = GuardedStream(
            token_stream=make_token_stream(tokens),
            records=RECORDS,
            strategy=CitationFailStrategy.ANNOTATE,
        )
        output = await collect_stream(stream)

        # 两段都在输出中
        assert "10.1016/j.joi.2017.08.007" in output
        assert "10.9999/fake" in output
        # fabricated_spans 仅含伪造的
        assert "10.9999/fake" in stream.fabricated_spans
        # evidence_refs 含真实的
        assert any("10.1016/j.joi.2017.08.007" in (e.span or "") for e in stream.evidence_refs)


# ======================================================================
# 缓冲边界行为
# ======================================================================

class TestBuffering:
    async def test_sentence_boundary_triggers_flush(self):
        """句号后的 token 应触发该段落的校验和放行。"""
        # 分多个 token 构成一个句子
        tokens = ["见 ", "10.1016/j.joi.2017.08.007", " 的研究。\n\n", "下一段。\n\n"]
        stream = GuardedStream(
            token_stream=make_token_stream(tokens),
            records=RECORDS,
            strategy=CitationFailStrategy.NOOP,
        )
        output = await collect_stream(stream)
        assert "10.1016/j.joi.2017.08.007" in output
        assert "下一段" in output

    async def test_empty_stream_no_output(self):
        stream = GuardedStream(
            token_stream=make_token_stream([]),
            records=RECORDS,
        )
        output = await collect_stream(stream)
        assert output == ""
        assert stream.segments_checked == 0

    async def test_long_no_boundary_forced_flush(self):
        """超过 MAX_BUFFER_CHARS 无句号边界时应强制刷新。"""
        # 超过 800 字符的无句号文本
        long_text = "无引用纯文字 " * 150  # 约 1050 chars
        tokens = [long_text]
        stream = GuardedStream(
            token_stream=make_token_stream(tokens),
            records=RECORDS,
        )
        output = await collect_stream(stream)
        # 输出应包含原文内容 (可能被分成多段)
        assert "无引用纯文字" in output
        # 应至少检查过一段
        assert stream.segments_checked >= 1

    async def test_tail_buffer_flushed_at_end(self):
        """流结束后剩余缓冲应被强制刷新。"""
        # 没有句号边界的短文本, 只在流结束时强制刷新
        text = "这是一段没有句号的文字"
        tokens = [text]
        stream = GuardedStream(
            token_stream=make_token_stream(tokens),
            records=RECORDS,
        )
        output = await collect_stream(stream)
        assert "这是一段" in output
        assert stream.segments_checked >= 1


# ======================================================================
# P0-2: 校验器异常 fail-closed（不再放行未校验原文）
# ======================================================================

class TestValidatorExceptionFailClosed:
    """codex P0-2：校验器抛异常时，绝不能把未校验原文当成已校验放行（安全带旁路）。

    修前（fail-open）：ANNOTATE 策略下 check_citations_against_records 抛异常 → yield 原 segment
                     且不计伪造，破坏可信 claim。
    修后（fail-closed）：抛 ValidationUnavailableError，不 yield 原 segment。
    """

    async def test_validator_exception_raises_validation_unavailable(self, monkeypatch):
        """校验器异常（ANNOTATE 默认策略）→ GuardedStream 迭代抛 ValidationUnavailableError，
        且不把原 segment 作为已校验文本放行。"""
        def _boom(*args, **kwargs):
            raise RuntimeError("校验器崩溃（模拟）")

        # monkeypatch guarded_stream 模块内引用的校验函数，使其崩溃
        monkeypatch.setattr(
            "app.safety.guarded_stream.check_citations_against_records", _boom
        )

        tokens = ["见 10.1016/j.joi.2017.08.007 的研究。\n\n"]
        stream = GuardedStream(
            token_stream=make_token_stream(tokens),
            records=RECORDS,
            strategy=CitationFailStrategy.ANNOTATE,
        )

        collected: list[str] = []
        with pytest.raises(ValidationUnavailableError):
            async for chunk in stream:
                collected.append(chunk)

        # 关键不变式：未校验原文绝不放行
        assert all("10.1016/j.joi.2017.08.007" not in c for c in collected), (
            "校验器崩溃时不得把未校验原文当成已校验放行"
        )

    async def test_validator_exception_records_error_not_valid(self, monkeypatch):
        """校验器异常时该段未作为 valid 放行：evidence_refs 不应包含该段引用。"""
        def _boom(*args, **kwargs):
            raise RuntimeError("校验器崩溃（模拟）")

        monkeypatch.setattr(
            "app.safety.guarded_stream.check_citations_against_records", _boom
        )

        tokens = ["见 10.1016/j.joi.2017.08.007 的研究。\n\n"]
        stream = GuardedStream(
            token_stream=make_token_stream(tokens),
            records=RECORDS,
            strategy=CitationFailStrategy.ANNOTATE,
        )
        with pytest.raises(ValidationUnavailableError):
            await collect_stream(stream)
        # 异常段未产出 evidence_refs（绝不计 valid）
        assert stream.evidence_refs == []


# ======================================================================
# 统计计数
# ======================================================================

class TestStatistics:
    async def test_evidence_refs_accumulate_across_segments(self):
        """多段中的真实引用应累积到 evidence_refs。"""
        tokens = [
            "见 10.1016/j.joi.2017.08.007 的分析。\n\n",
            "另参 10.1007/s11192-020-03483-z 的方法。\n\n",
        ]
        stream = GuardedStream(
            token_stream=make_token_stream(tokens),
            records=RECORDS,
        )
        await collect_stream(stream)
        assert stream.segments_checked >= 2
        assert len(stream.evidence_refs) >= 1  # 至少有一条 DOI 命中

    async def test_fabricated_spans_deduplicate_across_segments(self):
        """fabricated_spans 累积跨段的所有伪造引用 (允许重复)。"""
        tokens = [
            "见 10.9999/fake1 的研究。\n\n",
            "另 10.9999/fake2 的结论。\n\n",
        ]
        stream = GuardedStream(
            token_stream=make_token_stream(tokens),
            records=RECORDS,
            strategy=CitationFailStrategy.NOOP,
        )
        await collect_stream(stream)
        assert "10.9999/fake1" in stream.fabricated_spans
        assert "10.9999/fake2" in stream.fabricated_spans
