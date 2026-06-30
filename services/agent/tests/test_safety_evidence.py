"""测试 EvidenceRef 结构 (app/safety/evidence.py)。

覆盖:
  - 基本构造和字段赋值
  - from_record 工厂方法
  - record_hash 一致性 (同记录 → 同 hash)
  - record_hash 变动敏感性 (不同 title/doi → 不同 hash)
  - to_dict / from_dict 往返序列化
  - created_at 格式
"""
from __future__ import annotations

import datetime
import pytest

from app.safety.evidence import EvidenceRef


RECORD_A = {
    "idx": 1,
    "title": "Bibliometric study of research trends",
    "authors": "ARIA M;CUCCURULLO C",
    "year": 2017,
    "doi": "10.1016/j.joi.2017.08.007",
}

RECORD_B = {
    "idx": 2,
    "title": "Science mapping with VOSviewer",
    "authors": "SMITH J",
    "year": 2020,
    "doi": "",
}


class TestEvidenceRefConstruct:
    def test_from_record_basic_fields(self):
        ref = EvidenceRef.from_record(
            paper_id=1,
            record=RECORD_A,
            span="10.1016/j.joi.2017.08.007",
            claim="见 10.1016/j.joi.2017.08.007 的研究。",
            cite_type="doi",
            match_quality="green",
        )
        assert ref.paper_id == 1
        assert ref.span == "10.1016/j.joi.2017.08.007"
        assert ref.cite_type == "doi"
        assert ref.match_quality == "green"
        assert ref.corpus_id == "local_corpus"

    def test_from_record_sets_created_at(self):
        ref = EvidenceRef.from_record(paper_id=1, record=RECORD_A)
        # created_at 应是合法 ISO8601 格式
        ts = ref.created_at.replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(ts)
        assert isinstance(dt, datetime.datetime)

    def test_corpus_id_custom(self):
        ref = EvidenceRef.from_record(
            paper_id=2, record=RECORD_B, corpus_id="proj_xyz"
        )
        assert ref.corpus_id == "proj_xyz"


class TestRecordHash:
    def test_same_record_same_hash(self):
        h1 = EvidenceRef._hash_record(RECORD_A)
        h2 = EvidenceRef._hash_record(RECORD_A)
        assert h1 == h2

    def test_different_doi_different_hash(self):
        rec_b = {**RECORD_A, "doi": "10.9999/different"}
        h1 = EvidenceRef._hash_record(RECORD_A)
        h2 = EvidenceRef._hash_record(rec_b)
        assert h1 != h2

    def test_different_title_different_hash(self):
        rec_b = {**RECORD_A, "title": "Totally different title"}
        h1 = EvidenceRef._hash_record(RECORD_A)
        h2 = EvidenceRef._hash_record(rec_b)
        assert h1 != h2

    def test_hash_is_64_hex_chars(self):
        h = EvidenceRef._hash_record(RECORD_A)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_empty_record_deterministic(self):
        h1 = EvidenceRef._hash_record({})
        h2 = EvidenceRef._hash_record({})
        assert h1 == h2

    def test_doi_case_insensitive_via_hash(self):
        """hash 内部对 doi 做 lower(), 大小写不影响。"""
        rec_upper = {**RECORD_A, "doi": "10.1016/J.JOI.2017.08.007"}
        rec_lower = {**RECORD_A, "doi": "10.1016/j.joi.2017.08.007"}
        assert EvidenceRef._hash_record(rec_upper) == EvidenceRef._hash_record(rec_lower)


class TestSerialization:
    def test_to_dict_keys(self):
        ref = EvidenceRef.from_record(
            paper_id=1,
            record=RECORD_A,
            span="10.1016/j.joi.2017.08.007",
            claim="测试句子。",
            cite_type="doi",
            match_quality="green",
        )
        d = ref.to_dict()
        for key in ("paper_id", "corpus_id", "record_hash", "span", "claim",
                    "created_at", "cite_type", "match_quality"):
            assert key in d, f"缺少字段: {key}"

    def test_round_trip(self):
        ref = EvidenceRef.from_record(
            paper_id=1,
            record=RECORD_A,
            span="Smith (2020)",
            claim="Smith (2020) 指出...",
            corpus_id="proj_test",
            cite_type="en",
            match_quality="yellow",
        )
        d = ref.to_dict()
        ref2 = EvidenceRef.from_dict(d)
        assert ref2.paper_id == ref.paper_id
        assert ref2.corpus_id == ref.corpus_id
        assert ref2.record_hash == ref.record_hash
        assert ref2.span == ref.span
        assert ref2.claim == ref.claim
        assert ref2.cite_type == ref.cite_type
        assert ref2.match_quality == ref.match_quality

    def test_from_dict_defaults(self):
        """from_dict 对可选字段有合理默认值。"""
        minimal = {
            "paper_id": 3,
            "record_hash": "a" * 64,
        }
        ref = EvidenceRef.from_dict(minimal)
        assert ref.paper_id == 3
        assert ref.corpus_id == "local_corpus"
        assert ref.span is None
        assert ref.claim is None
        assert ref.cite_type == "unknown"
        assert ref.match_quality == "green"
        # P3-2: 缺失 source_content_sha256 → None（容错）
        assert ref.source_content_sha256 is None


# ======================================================================
# P3-2: source_content_sha256（文档内容溯源）
# ======================================================================

_FAKE_SHA = "b" * 64


class TestSourceContentSha256:
    def test_from_record_explicit_arg(self):
        """from_record 显式传 source_content_sha256 → 写入实例。"""
        ref = EvidenceRef.from_record(
            paper_id=1,
            record=RECORD_A,
            source_content_sha256=_FAKE_SHA,
        )
        assert ref.source_content_sha256 == _FAKE_SHA

    def test_from_record_fallback_to_record_content_sha256(self):
        """from_record 未显式传 → 回退读 record['content_sha256']。"""
        rec = {**RECORD_A, "content_sha256": _FAKE_SHA}
        ref = EvidenceRef.from_record(paper_id=1, record=rec)
        assert ref.source_content_sha256 == _FAKE_SHA

    def test_from_record_explicit_overrides_record(self):
        """显式参数优先于 record 内字段。"""
        rec = {**RECORD_A, "content_sha256": "c" * 64}
        ref = EvidenceRef.from_record(
            paper_id=1, record=rec, source_content_sha256=_FAKE_SHA,
        )
        assert ref.source_content_sha256 == _FAKE_SHA

    def test_to_dict_includes_field(self):
        ref = EvidenceRef.from_record(
            paper_id=1, record=RECORD_A, source_content_sha256=_FAKE_SHA,
        )
        d = ref.to_dict()
        assert "source_content_sha256" in d
        assert d["source_content_sha256"] == _FAKE_SHA

    def test_round_trip_preserves_field(self):
        ref = EvidenceRef.from_record(
            paper_id=1, record=RECORD_A, source_content_sha256=_FAKE_SHA,
        )
        ref2 = EvidenceRef.from_dict(ref.to_dict())
        assert ref2.source_content_sha256 == _FAKE_SHA

    def test_hash_record_unchanged_by_content_sha(self):
        """source_content_sha256 是另一维，不影响题录哈希 record_hash。"""
        rec = {**RECORD_A, "content_sha256": _FAKE_SHA}
        assert EvidenceRef._hash_record(rec) == EvidenceRef._hash_record(RECORD_A)
