"""P0-2 — 零伪造闭环 demo 脚本的契约测试（离线、DB 回放真实哈希链）。

锁定三段口径（作战方案 §10.3），防回归：
  正常路径   : 引用全部命中语料 → fabricated=0 → verify(--max-fabricated 0) PASS
  检出路径   : 默认 ANNOTATE 注入 1 条伪造 → status=done（继续输出，**不拒绝**）
               → verify(0) FAIL / verify(1) PASS（阈值可配）
  阻断路径   : 显式 REJECT 注入 1 条伪造 → status=error（整份拒绝）
               → fabricated=1 → verify(0) FAIL

主路径校验带 corpus_content_hashes（对应 CLI 的 --corpus-hashes），启用 evidence_traceable；
另有一个负例（test_clean_path_without_corpus_hashes_skips_traceable）证明不带时会跳过该校验。

本测试直接调脚本的 _build_one_runlog（造真实哈希链 runlog）+ runlog_verify（与 CLI 同一函数），
不起子进程、不打真实 OCR/LLM，完全离线确定。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from app.agent.runlog import RUNLOG_SCHEMA_VERSION
from app.agent.runlog_verify import verify_runlog
from app.safety.citation import CitationFailStrategy

import demo_zero_fabrication as demo  # noqa: E402

_CORPUS_HASHES = set(demo.CORPUS_HASHES)


@pytest.mark.asyncio
async def test_clean_path_zero_fabrication_passes(session_factory):
    """正常路径：全部命中语料 → fabricated=0 → verify(0) PASS，所有 check 绿。"""
    runlog, info = await demo._build_one_runlog(
        session_factory,
        review_text=demo.REVIEW_CLEAN,
        strategy=CitationFailStrategy.ANNOTATE,
        prompt="clean review",
        label="test_clean",
    )
    assert runlog["schema_version"] == RUNLOG_SCHEMA_VERSION
    assert info["fabricated"] == []
    assert info["final_status"] == "done"
    assert info["evidence_count"] == 3
    assert runlog["manifest"]["fabricated_count"] == 0

    rep = verify_runlog(
        runlog, corpus_content_hashes=_CORPUS_HASHES, max_fabricated=0
    )
    assert rep.ok is True, rep.errors
    # 带 corpus-hashes 才会启用溯源校验，且应通过
    assert rep.checks["evidence_traceable"] is True
    assert rep.checks["hash_chain"] is True
    assert rep.checks["content_hash_match"] is True
    assert rep.checks["zero_fabrication"] is True


@pytest.mark.asyncio
async def test_injected_annotate_detects_but_continues(session_factory):
    """检出路径（默认 ANNOTATE）：注入 1 条伪造 → 检出标红 + 计入日志，但**继续输出整份**。

    口径核心：status 仍 done（不是 error/拒绝）；fabricated_count=1。
    verify(0) FAIL（超过零容忍），verify(1) PASS（阈值可配，比口号更可信）。
    """
    runlog, info = await demo._build_one_runlog(
        session_factory,
        review_text=demo.REVIEW_INJECTED,
        strategy=CitationFailStrategy.ANNOTATE,
        prompt="injected annotate",
        label="test_injected_annotate",
    )
    # 默认 ANNOTATE：检出但不拒绝整份
    assert info["rejected"] is False
    assert info["final_status"] == "done"
    assert info["fabricated"] == [demo._FAKE_DOI]
    assert runlog["manifest"]["fabricated_count"] == 1
    # 伪造引用记入日志的 fabricated_spans
    assert demo._FAKE_DOI in runlog["fabricated_spans"]
    # 命中的真引用照常进证据（伪造引用绝不进 evidence_refs）
    assert info["evidence_count"] == 3
    # "标红"由产物证明（codex A-1）：annotated 文本里伪造 DOI 紧跟 inline ❌
    # （_annotate 在引用 end 位后插 " ❌"），该文本落库于 validation_summary.annotated_with_marks
    annotated = info["annotated_with_marks"]
    assert f"{demo._FAKE_DOI} ❌" in annotated, annotated
    # 真引用不会被标红（绿/黄），❌ 数量恰为 1（仅伪造那条）
    assert annotated.count("❌") == 1, annotated

    rep0 = verify_runlog(
        runlog, corpus_content_hashes=_CORPUS_HASHES, max_fabricated=0
    )
    assert rep0.ok is False
    assert rep0.checks["zero_fabrication"] is False
    # 哈希链/溯源仍自洽（伪造的是引用内容，不破坏日志结构）
    assert rep0.checks["hash_chain"] is True
    assert rep0.checks["evidence_traceable"] is True

    rep1 = verify_runlog(
        runlog, corpus_content_hashes=_CORPUS_HASHES, max_fabricated=1
    )
    assert rep1.ok is True, rep1.errors
    assert rep1.checks["zero_fabrication"] is True


@pytest.mark.asyncio
async def test_injected_reject_blocks_entire_review(session_factory):
    """阻断路径（显式 REJECT）：注入 1 条伪造 → 整份拒绝（status=error），区别于 ANNOTATE。"""
    runlog, info = await demo._build_one_runlog(
        session_factory,
        review_text=demo.REVIEW_INJECTED,
        strategy=CitationFailStrategy.REJECT,
        prompt="injected reject",
        label="test_injected_reject",
    )
    # REJECT：整份拒绝
    assert info["rejected"] is True
    assert info["final_status"] == "error"
    assert demo._FAKE_DOI in info["reject_msg"]
    assert runlog["run"]["status"] == "error"
    assert str(runlog["run"]["final_output"]).startswith("[REJECTED]")
    assert runlog["manifest"]["fabricated_count"] == 1

    rep = verify_runlog(
        runlog, corpus_content_hashes=_CORPUS_HASHES, max_fabricated=0
    )
    assert rep.ok is False
    assert rep.checks["zero_fabrication"] is False


@pytest.mark.asyncio
async def test_clean_path_without_corpus_hashes_skips_traceable(session_factory):
    """不带 corpus-hashes → 不启用 evidence_traceable 校验（§10.3：verify 必带 --corpus-hashes）。"""
    runlog, _ = await demo._build_one_runlog(
        session_factory,
        review_text=demo.REVIEW_CLEAN,
        strategy=CitationFailStrategy.ANNOTATE,
        prompt="clean no hashes",
        label="test_clean_no_hashes",
    )
    rep = verify_runlog(runlog, max_fabricated=0)
    assert "evidence_traceable" not in rep.checks
    # 其余结构/链/零伪造仍应通过
    assert rep.ok is True, rep.errors


def test_corpus_records_have_source_hashes():
    """预置语料每条都带 content_sha256，且与对外的 CORPUS_HASHES 一致（溯源闭环前提）。"""
    hashes = {r["content_sha256"] for r in demo.CORPUS_RECORDS}
    assert hashes == set(demo.CORPUS_HASHES)
    assert all(len(h) == 64 for h in hashes)  # sha256 hex
