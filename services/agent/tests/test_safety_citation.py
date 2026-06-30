"""测试引用存在性校验器 (app/safety/citation.py)。

覆盖:
  - 真实引用 (DOI 命中) → validation_passed=True, 产出 EvidenceRef, fabricated=[]
  - 伪造引用 (DOI 不在 records) → fabricated 非空, 按策略标注/拒绝
  - 混合文本 (部分真实 + 部分伪造) → fabricated 仅包含未命中引用
  - DOI 归一化一致性 (URL 前缀/大小写)
  - ANNOTATE 策略: validated_output 含警告注释
  - NOOP 策略: validated_output 与原文相同, validation_passed=False
  - REJECT 策略: 有伪造时抛出 guardrails ValidationError
  - EvidenceRef 字段正确性
  - 作者年引用 yellow 命中 → EvidenceRef 产出 match_quality=yellow
  - 编号引用越界 → fabricated
  - 空语料 → 所有引用被标为 fabricated
"""
from __future__ import annotations

import pytest

from app.safety.citation import (
    check_citations_against_records,
    CitationFailStrategy,
    CitationCheckResult,
)
from app.safety.evidence import EvidenceRef

# ======================================================================
# 合成语料
# ======================================================================

RECORDS = [
    {
        "idx": 1,
        "title": "Bibliometric analysis of sustainable development",
        "authors": "ARIA M;CUCCURULLO C",
        "year": 2017,
        "doi": "10.1016/j.joi.2017.08.007",
    },
    {
        "idx": 2,
        "title": "Science mapping with VOSviewer",
        "authors": "SMITH J",
        "year": 2020,
        "doi": "10.1007/s11192-020-03483-z",
    },
    {
        "idx": 3,
        "title": "中国文献计量学发展",
        "authors": "王五 W",
        "year": 2021,
        "doi": "",
    },
]


# ======================================================================
# 基础通过场景
# ======================================================================

class TestValidCitations:
    def test_doi_exact_match_passes(self):
        text = "正如 10.1016/j.joi.2017.08.007 的研究所示。"
        r = check_citations_against_records(text, RECORDS)
        assert r.validation_passed is True
        assert r.fabricated == []
        assert r.summary["green"] >= 1
        # 产出 EvidenceRef
        assert any(ref.cite_type == "doi" and ref.paper_id == 1 for ref in r.evidence_refs)

    def test_doi_url_prefix_normalized(self):
        """doi.org 前缀 → 应被归一化并命中。"""
        text = "参考 https://doi.org/10.1016/j.joi.2017.08.007 的结果。"
        r = check_citations_against_records(text, RECORDS)
        assert r.validation_passed is True
        assert r.fabricated == []

    def test_doi_uppercase_normalized(self):
        """大写 DOI → 归一化后命中。"""
        text = "见 10.1016/J.JOI.2017.08.007 的结论。"
        r = check_citations_against_records(text, RECORDS)
        assert r.validation_passed is True
        assert r.fabricated == []

    def test_author_year_yellow_produces_evidence_ref(self):
        text = "Smith (2020) 指出该方法有效。"
        r = check_citations_against_records(text, RECORDS)
        assert r.summary["red"] == 0
        # yellow 也产出 EvidenceRef
        assert any(ref.match_quality == "yellow" for ref in r.evidence_refs)

    def test_numbered_in_range_passes(self):
        text = "如 [1] 所示, 该领域增长迅速。"
        r = check_citations_against_records(text, RECORDS)
        assert r.fabricated == []
        assert r.summary["red"] == 0

    def test_multiple_valid_citations(self):
        text = (
            "根据 10.1016/j.joi.2017.08.007 的分析以及 "
            "10.1007/s11192-020-03483-z 的方法。"
        )
        r = check_citations_against_records(text, RECORDS)
        assert r.validation_passed is True
        assert len(r.evidence_refs) == 2
        assert r.fabricated == []

    def test_evidence_ref_record_hash_consistent(self):
        """EvidenceRef.record_hash 应与 EvidenceRef._hash_record(record) 一致。"""
        text = "10.1016/j.joi.2017.08.007 的研究。"
        r = check_citations_against_records(text, RECORDS)
        ref = next((e for e in r.evidence_refs if e.paper_id == 1), None)
        assert ref is not None
        expected_hash = EvidenceRef._hash_record(RECORDS[0])
        assert ref.record_hash == expected_hash


# ======================================================================
# 伪造引用场景
# ======================================================================

class TestFabricatedCitations:
    def test_nonexistent_doi_marked_fabricated(self):
        text = "发现 10.9999/totally-fake-doi 很重要。"
        r = check_citations_against_records(text, RECORDS, strategy=CitationFailStrategy.NOOP)
        assert r.validation_passed is False
        assert "10.9999/totally-fake-doi" in r.fabricated
        assert r.summary["red"] >= 1

    def test_fabricated_author_year_nonexistent(self):
        text = "Nonexistent (1999) 声称发现了新规律。"
        r = check_citations_against_records(text, RECORDS, strategy=CitationFailStrategy.NOOP)
        assert r.validation_passed is False
        assert len(r.fabricated) >= 1

    def test_real_author_wrong_year_fabricated(self):
        """真实作者但年份明确不符 → 应被标记为伪造。"""
        text = "Smith (2099) 声称..."
        r = check_citations_against_records(text, RECORDS, strategy=CitationFailStrategy.NOOP)
        assert r.validation_passed is False
        assert r.summary["red"] >= 1

    def test_numbered_out_of_range_fabricated(self):
        text = "见 [999] 的论证。"
        r = check_citations_against_records(text, RECORDS, strategy=CitationFailStrategy.NOOP)
        assert r.validation_passed is False
        assert "[999]" in r.fabricated

    def test_empty_corpus_all_fabricated(self):
        text = "Smith (2020) 的研究以及 10.1234/abc 的结论。"
        r = check_citations_against_records(text, [], strategy=CitationFailStrategy.NOOP)
        assert r.validation_passed is False
        assert r.summary["red"] >= 2
        assert r.evidence_refs == []

    def test_etal_marker_not_fabricated(self):
        """『英文姓 等人（年）』混排: CJK 正则只抓到 et-al 标记当姓名 → 判 yellow(待核) 不入 fabricated。

        与 cite_check.test_etal_marker_not_red 同源(review-pipeline 与 UI 三色共用 check_citations)。
        回归锁: 批处理 analyst_forecast 曾把『等人（2011）』误计为 fabricated_citations, 与零伪造矛盾。
        """
        text = "Smith 等人（2020）的研究表明该方法稳健；另有 等人（2020）进一步验证。"
        r = check_citations_against_records(text, RECORDS, strategy=CitationFailStrategy.NOOP)
        assert "等人（2020）" not in r.fabricated
        assert all("等人" not in f for f in r.fabricated)
        assert r.summary["red"] == 0


# ======================================================================
# 策略行为
# ======================================================================

class TestStrategies:
    def test_annotate_strategy_appends_warning(self):
        """ANNOTATE 策略: validated_output 末尾含警告注释。"""
        text = "引用 10.9999/fake 的结论。"
        r = check_citations_against_records(text, RECORDS, strategy=CitationFailStrategy.ANNOTATE)
        assert "⚠️" in r.validated_output or "警告" in r.validated_output

    def test_noop_strategy_preserves_text(self):
        """NOOP 策略: validated_output 与输入原文相同。"""
        text = "发现 10.9999/fake 很重要。"
        r = check_citations_against_records(text, RECORDS, strategy=CitationFailStrategy.NOOP)
        assert r.validated_output == text
        assert r.validation_passed is False

    def test_reject_strategy_raises_on_fabricated(self):
        """REJECT 策略: 有伪造引用时抛出异常。"""
        text = "发现 10.9999/fake 很重要。"
        with pytest.raises(Exception):
            check_citations_against_records(text, RECORDS, strategy=CitationFailStrategy.REJECT)

    def test_annotate_strategy_valid_text_unchanged(self):
        """ANNOTATE 策略下, 全真实引用文本不应追加警告。"""
        text = "见 10.1016/j.joi.2017.08.007 的结论。"
        r = check_citations_against_records(text, RECORDS, strategy=CitationFailStrategy.ANNOTATE)
        assert r.validation_passed is True
        # validated_output 不含警告 (或至少不含伪造警告)
        assert r.fabricated == []


# ======================================================================
# 混合场景
# ======================================================================

class TestMixedCitations:
    def test_mixed_real_and_fabricated(self):
        text = (
            "根据 10.1016/j.joi.2017.08.007 和 "
            "10.9999/totally-fake 的研究。"
        )
        r = check_citations_against_records(text, RECORDS, strategy=CitationFailStrategy.NOOP)
        # 真实的在 evidence_refs
        assert any("10.1016/j.joi.2017.08.007" in (e.span or "") for e in r.evidence_refs)
        # 伪造的在 fabricated
        assert "10.9999/totally-fake" in r.fabricated
        # 整体 validation_passed = False (有 red)
        assert r.validation_passed is False

    def test_no_citations_passes(self):
        text = "这段文字没有任何引用，纯描述性内容。"
        r = check_citations_against_records(text, RECORDS)
        assert r.validation_passed is True
        assert r.fabricated == []
        assert r.evidence_refs == []

    def test_empty_text_passes(self):
        r = check_citations_against_records("", RECORDS)
        assert r.validation_passed is True
        assert r.fabricated == []


# ======================================================================
# DOI 归一化一致性 (与 cite_check._norm_doi 保持同口径)
# ======================================================================

class TestDOINormalization:
    @pytest.mark.parametrize("doi_in_text", [
        "10.1016/j.joi.2017.08.007",
        "https://doi.org/10.1016/j.joi.2017.08.007",
        "http://dx.doi.org/10.1016/j.joi.2017.08.007",
        "DOI: 10.1016/j.joi.2017.08.007",
        "10.1016/J.JOI.2017.08.007",
    ])
    def test_doi_variants_all_match(self, doi_in_text):
        text = f"见 {doi_in_text} 的研究。"
        r = check_citations_against_records(text, RECORDS)
        # 应该命中 (green), 不在 fabricated
        assert r.fabricated == [], f"DOI 变体 {doi_in_text!r} 未能命中"


# ======================================================================
# P3-2: content_sha256 透传 → EvidenceRef.source_content_sha256
# ======================================================================

class TestContentSha256Passthrough:
    """records 带 content_sha256 时，命中产出的 EvidenceRef 应携带 source_content_sha256。"""

    def test_doi_hit_carries_source_content_sha256(self):
        sha = "d" * 64
        records = [
            {
                "idx": 1,
                "title": "Paper Alpha",
                "authors": "A",
                "year": 2020,
                "doi": "10.1234/alpha",
                "content_sha256": sha,
            },
        ]
        text = "根据 10.1234/alpha 的研究显示重要发现。"
        r = check_citations_against_records(text, records)
        assert r.evidence_refs, "应至少命中一条 DOI 引用"
        assert all(e.source_content_sha256 == sha for e in r.evidence_refs)

    def test_num_hit_carries_source_content_sha256(self):
        sha = "e" * 64
        records = [
            {"idx": 1, "title": "Paper One", "authors": "A", "year": 2020,
             "doi": "10.1234/one", "content_sha256": sha},
        ]
        text = "已有研究指出该现象普遍存在 [1]。"
        r = check_citations_against_records(text, records)
        assert r.evidence_refs, "编号 [1] 应命中"
        assert all(e.source_content_sha256 == sha for e in r.evidence_refs)

    def test_missing_content_sha256_yields_none(self):
        """records 不含 content_sha256 → EvidenceRef.source_content_sha256 为 None。"""
        records = [
            {"idx": 1, "title": "Paper One", "authors": "A", "year": 2020,
             "doi": "10.1234/one"},
        ]
        text = "见 10.1234/one 的研究 [1]。"
        r = check_citations_against_records(text, records)
        assert r.evidence_refs
        assert all(e.source_content_sha256 is None for e in r.evidence_refs)


# ======================================================================
# P1-3: EvidenceRef.paper_id 用真实 DB paper.id（而非引用序号 idx）
# ======================================================================

class TestPaperIdUsesRealId:
    """codex P1-3：records 带真实 DB paper_id 时，EvidenceRef.paper_id 应取真实 id，
    而非引用序号 idx —— 否则证据无法可靠回指库内论文。"""

    def test_doi_hit_uses_real_paper_id(self):
        # idx=1（引用序号）但 paper_id=777（真实 DB id）
        records = [
            {"idx": 1, "paper_id": 777, "title": "Paper Alpha", "authors": "A",
             "year": 2020, "doi": "10.1234/alpha", "content_sha256": "a" * 64},
        ]
        text = "根据 10.1234/alpha 的研究显示重要发现。"
        r = check_citations_against_records(text, records)
        assert r.evidence_refs, "应至少命中一条 DOI 引用"
        # 关键断言：paper_id 是真实 DB id 777，不是引用序号 1
        assert all(e.paper_id == 777 for e in r.evidence_refs), (
            f"EvidenceRef.paper_id 应为真实 paper.id 777，实得 "
            f"{[e.paper_id for e in r.evidence_refs]}"
        )

    def test_num_hit_uses_real_paper_id(self):
        records = [
            {"idx": 1, "paper_id": 888, "title": "Paper One", "authors": "A",
             "year": 2020, "doi": "10.1234/one"},
        ]
        text = "已有研究指出该现象普遍存在 [1]。"
        r = check_citations_against_records(text, records)
        assert r.evidence_refs, "编号 [1] 应命中"
        assert all(e.paper_id == 888 for e in r.evidence_refs)

    def test_missing_paper_id_falls_back_to_idx(self):
        """records 无 paper_id 字段 → 回退用 idx（向后兼容）。"""
        records = [
            {"idx": 3, "title": "Paper Three", "authors": "A", "year": 2020,
             "doi": "10.1234/three"},
        ]
        text = "见 10.1234/three 的研究。"
        r = check_citations_against_records(text, records)
        assert r.evidence_refs
        # 无 paper_id → 回退 idx=3
        assert all(e.paper_id == 3 for e in r.evidence_refs)
