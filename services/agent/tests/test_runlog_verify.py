"""Task P2-5 — verify_runlog 校验可验证日志。

覆盖：
- 良好日志（真实哈希链）→ ok True，全 check 通过
- seq 缺口 → seq_contiguous False
- 篡改 body（改 run.id 不重算 content hash）→ content_hash_match False
- fabricated_count > max → zero_fabrication False
- legacy（空 event_hash）→ hash_chain 不失败（视作 legacy 不可验证）
- manifest 计数被篡改 → manifest_counts False
- evidence_traceable：给 corpus_content_hashes 才校验，缺字段跳过记 note
"""
from __future__ import annotations

import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.agent.runlog import RUNLOG_SCHEMA_VERSION, build_runlog
from app.agent.runlog_verify import VerifyReport, verify_runlog
from app.repositories.agent_run import append_event_chained, create_run
from app.repositories.project import create_project


async def _make_good_runlog(session, *, n_events: int = 3):
    """造一个带真实哈希链的良好 runlog（经 append_event_chained + build_runlog）。"""
    p = await create_project(session, {"name": f"V{os.urandom(4).hex()}"})
    r = await create_run(session, project_id=p.id)
    for i in range(n_events):
        await append_event_chained(session, r.id, f"ev_{i}", {"i": i})
    return await build_runlog(session, r.id)


@pytest.mark.asyncio
async def test_good_runlog_ok(session):
    log = await _make_good_runlog(session)
    rep = verify_runlog(log)
    assert isinstance(rep, VerifyReport)
    assert rep.ok is True, rep.errors
    assert rep.checks["schema_version"] is True
    assert rep.checks["seq_contiguous"] is True
    assert rep.checks["manifest_counts"] is True
    assert rep.checks["hash_chain"] is True
    assert rep.checks["content_hash_match"] is True
    assert rep.checks["zero_fabrication"] is True
    assert rep.errors == []


@pytest.mark.asyncio
async def test_seq_gap_fails(session):
    log = await _make_good_runlog(session)
    # 制造 seq 缺口：删掉中间一条
    del log["events"][1]
    rep = verify_runlog(log)
    assert rep.checks["seq_contiguous"] is False
    assert rep.ok is False


@pytest.mark.asyncio
async def test_tampered_body_content_hash_fails(session):
    log = await _make_good_runlog(session)
    # 篡改 body（改 run.id）但不重算 content_sha256
    log = copy.deepcopy(log)
    log["run"]["id"] = 999999
    rep = verify_runlog(log)
    assert rep.checks["content_hash_match"] is False
    assert rep.ok is False


@pytest.mark.asyncio
async def test_fabricated_over_max_fails(session):
    log = await _make_good_runlog(session)
    log["manifest"]["fabricated_count"] = 3
    rep = verify_runlog(log, max_fabricated=0)
    assert rep.checks["zero_fabrication"] is False
    assert rep.ok is False
    # 允许 max_fabricated 放宽
    rep2 = verify_runlog(log, max_fabricated=3)
    assert rep2.checks["zero_fabrication"] is True


def test_legacy_empty_event_hash_not_failing():
    """legacy 链（event_hash 为空）→ hash_chain 不失败，标 chain_legacy。"""
    body = {
        "schema_version": RUNLOG_SCHEMA_VERSION,
        "run": {"id": 1, "project_id": 1, "status": "done", "prompt": "",
                "model_used": "", "created_at": None, "final_output": None},
        "messages": [],
        "events": [
            {"seq": 1, "type": "a", "payload": {}, "ts": "2026-05-22T00:00:00",
             "prev_hash": None, "event_hash": None},
            {"seq": 2, "type": "b", "payload": {}, "ts": "2026-05-22T00:00:01",
             "prev_hash": None, "event_hash": ""},
        ],
        "tool_invocations": [],
        "evidence_refs": [],
        "fabricated_spans": [],
    }
    from app.agent.runlog import _content_sha256
    manifest = {
        "event_count": 2, "tool_invocation_count": 0, "evidence_count": 0,
        "fabricated_count": 0, "chain_head": "",
    }
    manifest["content_sha256"] = _content_sha256({**body, "manifest": dict(manifest)})
    body["manifest"] = manifest

    rep = verify_runlog(body)
    assert rep.checks["hash_chain"] is True
    assert rep.checks.get("chain_legacy") is True
    assert rep.ok is True


def test_schema_version_mismatch_fails():
    body = {"schema_version": "runlog/v0", "run": {"id": 1}, "messages": [],
            "events": [], "tool_invocations": [], "evidence_refs": [],
            "fabricated_spans": [], "manifest": {"event_count": 0,
            "tool_invocation_count": 0, "evidence_count": 0, "fabricated_count": 0,
            "chain_head": "", "content_sha256": "x"}}
    rep = verify_runlog(body)
    assert rep.checks["schema_version"] is False
    assert rep.ok is False


@pytest.mark.asyncio
async def test_manifest_count_tampered_fails(session):
    log = await _make_good_runlog(session)
    log["manifest"]["event_count"] = 999
    rep = verify_runlog(log)
    assert rep.checks["manifest_counts"] is False
    assert rep.ok is False


def test_evidence_traceable_with_corpus_hashes():
    """给 corpus_content_hashes：green/yellow ref 须含 source_content_sha256 且在集合内。"""
    body = {
        "schema_version": RUNLOG_SCHEMA_VERSION,
        "run": {"id": 1, "project_id": 1, "status": "done", "prompt": "",
                "model_used": "", "created_at": None, "final_output": None},
        "messages": [],
        "events": [],
        "tool_invocations": [],
        "evidence_refs": [
            {"paper_id": 1, "match_quality": "green",
             "source_content_sha256": "hashA"},
            {"paper_id": 2, "match_quality": "yellow",
             "source_content_sha256": "hashB"},
        ],
        "fabricated_spans": [],
    }
    from app.agent.runlog import _content_sha256
    manifest = {"event_count": 0, "tool_invocation_count": 0, "evidence_count": 2,
                "fabricated_count": 0, "chain_head": ""}
    manifest["content_sha256"] = _content_sha256({**body, "manifest": dict(manifest)})
    body["manifest"] = manifest

    # 全在集合内 → ok
    rep = verify_runlog(body, corpus_content_hashes={"hashA", "hashB"})
    assert rep.checks["evidence_traceable"] is True

    # 缺一个 → fail
    rep2 = verify_runlog(body, corpus_content_hashes={"hashA"})
    assert rep2.checks["evidence_traceable"] is False
    assert rep2.ok is False


def test_evidence_traceable_legacy_schema_skips_missing_field():
    """codex P1-6：旧 schema（pre-P3-2，schema_version != runlog/v1）的 ref 无
    source_content_sha256 → 跳过记 note，不失败（向后兼容旧 run）。"""
    body = {
        "schema_version": "runlog/v0",  # 旧 schema → 宽松
        "run": {"id": 1, "project_id": 1, "status": "done", "prompt": "",
                "model_used": "", "created_at": None, "final_output": None},
        "messages": [],
        "events": [],
        "tool_invocations": [],
        "evidence_refs": [
            {"paper_id": 1, "match_quality": "green"},  # 无 source_content_sha256
        ],
        "fabricated_spans": [],
    }
    from app.agent.runlog import _content_sha256
    manifest = {"event_count": 0, "tool_invocation_count": 0, "evidence_count": 1,
                "fabricated_count": 0, "chain_head": ""}
    manifest["content_sha256"] = _content_sha256({**body, "manifest": dict(manifest)})
    body["manifest"] = manifest

    # 注意：旧 schema 的 schema_version check 本身会 False（不影响 evidence_traceable 宽松）
    rep = verify_runlog(body, corpus_content_hashes={"hashA"})
    assert rep.checks["evidence_traceable"] is True


def test_evidence_traceable_v1_missing_sha_fails():
    """codex P1-6：runlog/v1（P3-2 era）green evidence 缺 source_content_sha256 →
    校验失败（把核心溯源保证从可选改为强制）。"""
    body = {
        "schema_version": RUNLOG_SCHEMA_VERSION,  # runlog/v1 → 强制
        "run": {"id": 1, "project_id": 1, "status": "done", "prompt": "",
                "model_used": "", "created_at": None, "final_output": None},
        "messages": [],
        "events": [],
        "tool_invocations": [],
        "evidence_refs": [
            {"paper_id": 1, "match_quality": "green"},  # 缺 source_content_sha256 → 失败
        ],
        "fabricated_spans": [],
    }
    from app.agent.runlog import _content_sha256
    manifest = {"event_count": 0, "tool_invocation_count": 0, "evidence_count": 1,
                "fabricated_count": 0, "chain_head": ""}
    manifest["content_sha256"] = _content_sha256({**body, "manifest": dict(manifest)})
    body["manifest"] = manifest

    rep = verify_runlog(body, corpus_content_hashes={"hashA"})
    assert rep.checks["evidence_traceable"] is False, (
        "runlog/v1 green evidence 缺 source_content_sha256 应判失败"
    )
    assert rep.ok is False


def test_evidence_traceable_v1_yellow_missing_sha_fails():
    """runlog/v1 yellow evidence 缺 source_content_sha256 同样失败。"""
    body = {
        "schema_version": RUNLOG_SCHEMA_VERSION,
        "run": {"id": 1, "project_id": 1, "status": "done", "prompt": "",
                "model_used": "", "created_at": None, "final_output": None},
        "messages": [],
        "events": [],
        "tool_invocations": [],
        "evidence_refs": [
            {"paper_id": 5, "match_quality": "yellow"},  # 缺 sha
        ],
        "fabricated_spans": [],
    }
    from app.agent.runlog import _content_sha256
    manifest = {"event_count": 0, "tool_invocation_count": 0, "evidence_count": 1,
                "fabricated_count": 0, "chain_head": ""}
    manifest["content_sha256"] = _content_sha256({**body, "manifest": dict(manifest)})
    body["manifest"] = manifest

    rep = verify_runlog(body, corpus_content_hashes={"hashA"})
    assert rep.checks["evidence_traceable"] is False
    assert rep.ok is False


def test_evidence_traceable_skipped_when_no_corpus():
    """不给 corpus_content_hashes → 不做 evidence_traceable check。"""
    body = {
        "schema_version": RUNLOG_SCHEMA_VERSION,
        "run": {"id": 1, "project_id": 1, "status": "done", "prompt": "",
                "model_used": "", "created_at": None, "final_output": None},
        "messages": [], "events": [], "tool_invocations": [],
        "evidence_refs": [{"paper_id": 1, "match_quality": "green"}],
        "fabricated_spans": [],
    }
    from app.agent.runlog import _content_sha256
    manifest = {"event_count": 0, "tool_invocation_count": 0, "evidence_count": 1,
                "fabricated_count": 0, "chain_head": ""}
    manifest["content_sha256"] = _content_sha256({**body, "manifest": dict(manifest)})
    body["manifest"] = manifest

    rep = verify_runlog(body)
    assert "evidence_traceable" not in rep.checks
    assert rep.ok is True


@pytest.mark.asyncio
async def test_tampered_event_payload_breaks_chain(session):
    log = await _make_good_runlog(session)
    # 篡改一条事件的 payload 但保留旧 event_hash → recompute 不匹配
    log["events"][1]["payload"] = {"i": 999}
    rep = verify_runlog(log)
    assert rep.checks["hash_chain"] is False
    assert rep.ok is False
