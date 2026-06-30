"""P3-4 — grounding / 质量指标 harness 测试（TDD 红→绿）。

测试覆盖：
  - 正常路径：manifest.fabricated_count 驱动三大指标计算
  - 边界：evidence 空 + fabricated=0（无引用 → 不可评分: 三率 None + insufficient_evidence）
  - 边界：部分 source_content_sha256 不在 corpus_hashes
  - 边界：source_content_sha256=None（算作未命中）
  - 边界：corpus_hashes=None（provenance_hit_rate=None）
  - 原始计数字段验证
  - parse_fidelity_spotcheck 纯函数逻辑
"""
import pytest

from app.agent.metrics import grounding_metrics, parse_fidelity_spotcheck


# ---------------------------------------------------------------------------
# 正常路径：2 evidence（green+yellow）+ 1 fabricated
# ---------------------------------------------------------------------------

def test_grounding_metrics_uses_manifest_fabricated():
    runlog = {
        "evidence_refs": [
            {"match_quality": "green", "source_content_sha256": "h1"},
            {"match_quality": "yellow", "source_content_sha256": "h2"},
        ],
        "manifest": {"fabricated_count": 1, "evidence_count": 2},
    }
    m = grounding_metrics(runlog, corpus_hashes={"h1", "h2"})
    # grounding_accuracy = (green+yellow=2) / (2+fabricated 1=3)
    assert abs(m["grounding_accuracy"] - 2 / 3) < 1e-6
    # provenance_hit_rate = h1,h2 都命中 → 2/2 = 1.0
    assert abs(m["provenance_hit_rate"] - 1.0) < 1e-6
    # zero_fabrication_rate = 1 - 1/(2+1) = 2/3
    assert abs(m["zero_fabrication_rate"] - 2 / 3) < 1e-6


# ---------------------------------------------------------------------------
# 原始计数字段验证
# ---------------------------------------------------------------------------

def test_grounding_metrics_raw_counts():
    runlog = {
        "evidence_refs": [
            {"match_quality": "green", "source_content_sha256": "h1"},
            {"match_quality": "yellow", "source_content_sha256": "h2"},
        ],
        "manifest": {"fabricated_count": 1, "evidence_count": 2},
    }
    m = grounding_metrics(runlog)
    assert m["evidence_count"] == 2
    assert m["fabricated_count"] == 1
    assert m["green_count"] == 1
    assert m["yellow_count"] == 1


# ---------------------------------------------------------------------------
# 边界一：evidence 空 + fabricated=0（无任何引用 → 不可评分, codex P1）
# ---------------------------------------------------------------------------

def test_grounding_metrics_empty_evidence_zero_fabricated():
    """无任何引用时**不可评分**：三率为 None + insufficient_evidence=True。

    codex P1：空 review 不得伪装成 100% 满分（可被 gaming 的报告风险）。
    """
    runlog = {
        "evidence_refs": [],
        "manifest": {"fabricated_count": 0, "evidence_count": 0},
    }
    m = grounding_metrics(runlog, corpus_hashes={"h1"})
    assert m["grounding_accuracy"] is None
    assert m["provenance_hit_rate"] is None
    assert m["zero_fabrication_rate"] is None
    assert m["insufficient_evidence"] is True
    assert m["scoreable"] is False
    assert m["evidence_count"] == 0
    assert m["fabricated_count"] == 0


# ---------------------------------------------------------------------------
# 边界二：部分 source_content_sha256 不在 corpus_hashes
# ---------------------------------------------------------------------------

def test_grounding_metrics_partial_provenance_hit():
    """2 个 evidence，只有 1 个 sha256 在 corpus_hashes 中。"""
    runlog = {
        "evidence_refs": [
            {"match_quality": "green", "source_content_sha256": "h1"},
            {"match_quality": "green", "source_content_sha256": "h_miss"},
        ],
        "manifest": {"fabricated_count": 0, "evidence_count": 2},
    }
    m = grounding_metrics(runlog, corpus_hashes={"h1"})
    assert abs(m["provenance_hit_rate"] - 0.5) < 1e-6
    # grounding_accuracy：2/(2+0) = 1.0
    assert abs(m["grounding_accuracy"] - 1.0) < 1e-6
    assert abs(m["zero_fabrication_rate"] - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# 边界三：source_content_sha256=None（算作未命中）
# ---------------------------------------------------------------------------

def test_grounding_metrics_none_sha256_counts_as_miss():
    """source_content_sha256=None 的 evidence 条目算作 provenance 未命中。"""
    runlog = {
        "evidence_refs": [
            {"match_quality": "green", "source_content_sha256": "h1"},
            {"match_quality": "green", "source_content_sha256": None},   # 无 sha256
            {"match_quality": "green"},                                   # 缺字段同等处理
        ],
        "manifest": {"fabricated_count": 0, "evidence_count": 3},
    }
    m = grounding_metrics(runlog, corpus_hashes={"h1"})
    # 只有 h1 命中，3 条总数 → 1/3
    assert abs(m["provenance_hit_rate"] - 1 / 3) < 1e-6


# ---------------------------------------------------------------------------
# 边界四：corpus_hashes=None → provenance_hit_rate=None
# ---------------------------------------------------------------------------

def test_grounding_metrics_corpus_hashes_none():
    """corpus_hashes=None 时，无法判定溯源命中，provenance_hit_rate 为 None。"""
    runlog = {
        "evidence_refs": [
            {"match_quality": "green", "source_content_sha256": "h1"},
        ],
        "manifest": {"fabricated_count": 0, "evidence_count": 1},
    }
    m = grounding_metrics(runlog, corpus_hashes=None)
    assert m["provenance_hit_rate"] is None
    # 其他指标仍可算
    assert abs(m["grounding_accuracy"] - 1.0) < 1e-6
    assert abs(m["zero_fabrication_rate"] - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# 容错：manifest/validation_summary 缺失时不崩溃
# ---------------------------------------------------------------------------

def test_grounding_metrics_missing_manifest():
    """manifest 缺失或 fabricated_count 缺失时，fabricated_count 默认 0。"""
    runlog = {
        "evidence_refs": [
            {"match_quality": "green", "source_content_sha256": "h1"},
        ],
        # manifest 完全缺失
    }
    m = grounding_metrics(runlog)
    assert m["fabricated_count"] == 0
    assert abs(m["grounding_accuracy"] - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# parse_fidelity_spotcheck 纯函数逻辑
# ---------------------------------------------------------------------------

def test_parse_fidelity_spotcheck_all_good():
    """所有论文字段完整时，各率均为 1.0。"""
    papers = [
        {"title": "论文A", "abstract": "摘要A", "body": "正文内容A" * 10},
        {"title": "论文B", "abstract": "摘要B", "body": "正文内容B" * 10},
    ]
    result = parse_fidelity_spotcheck(papers)
    assert result["title_nonempty_rate"] == 1.0
    assert result["abstract_nonempty_rate"] == 1.0
    assert result["body_length_gt0_rate"] == 1.0
    assert result["sample_size"] == 2


def test_parse_fidelity_spotcheck_partial():
    """部分论文缺字段时，各率反映实际情况。"""
    papers = [
        {"title": "论文A", "abstract": "摘要A", "body": "正文" * 10},
        {"title": "", "abstract": None, "body": ""},  # title/abstract/body 均不合格
        {"title": "论文C", "abstract": "摘要C", "body": None},  # body 缺失
    ]
    result = parse_fidelity_spotcheck(papers)
    assert abs(result["title_nonempty_rate"] - 2 / 3) < 1e-6   # 只有 A、C 有 title
    assert abs(result["abstract_nonempty_rate"] - 2 / 3) < 1e-6  # A、C 有 abstract
    assert abs(result["body_length_gt0_rate"] - 1 / 3) < 1e-6  # 只有 A 有正文
    assert result["sample_size"] == 3


def test_parse_fidelity_spotcheck_empty():
    """空列表时各率为 None，sample_size=0。"""
    result = parse_fidelity_spotcheck([])
    assert result["sample_size"] == 0
    assert result["title_nonempty_rate"] is None
    assert result["abstract_nonempty_rate"] is None
    assert result["body_length_gt0_rate"] is None
