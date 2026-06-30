"""Task P2-5 — verify_runlog 对 RunLog（runlog/v1）做离线可验证校验。

校验项（见 verify_runlog）：
  1. schema_version  schema 版本匹配
  2. seq_contiguous  事件 seq == [1..N]
  3. manifest_counts 计数与实际长度一致
  4. hash_chain      重算哈希链自洽（首条 prev_hash=="" + 逐条 event_hash 可复算）
  5. content_hash_match  body+manifest(去 content_sha256) 重算 == manifest.content_sha256
  6. evidence_traceable  （仅当给 corpus_content_hashes）green/yellow 证据可溯源到语料
                          codex P1-6：runlog/v1 下 source_content_sha256 强制（缺即失败）；
                          旧 schema 宽松跳过缺字段的 ref（向后兼容）
  7. zero_fabrication manifest.fabricated_count <= max_fabricated

诚实声明（重要，"可验证日志" 的边界）：
  本校验器证明的是 *内部一致性*——结构完整、哈希链自洽、引用溯源（给语料哈希时）、
  零伪造计数。哈希链只能证明"日志自身前后链未被独立篡改"；真正的防篡改（tamper-proof）
  需要把 chain_head 做 *外部锚定*（写入不可变存储 / 数字签名 / 时间戳服务），这 *不在本
  任务范围内*。本校验器 *不* 证明 final_output 在逻辑上确由这些工具调用推导得出。

legacy 链处理：早于哈希链落地的旧 run，其 event_hash 为 null/空。此时 hash_chain *不判失败*
  （置 True 并另置 checks["chain_legacy"]=True + 记 note），因为这些链客观上不可验证、
  不应因历史原因把整份日志判为 FAIL。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..repositories.agent_run import _event_hash
from .runlog import RUNLOG_SCHEMA_VERSION, _content_sha256


@dataclass
class VerifyReport:
    """校验报告：ok=全 check 通过；checks=逐项布尔；errors=失败/提示明细。"""

    ok: bool
    checks: dict[str, bool] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def verify_runlog(
    runlog: dict,
    *,
    corpus_content_hashes: set[str] | None = None,
    max_fabricated: int = 0,
) -> VerifyReport:
    """离线校验一份 runlog，返回 VerifyReport。

    Args:
        runlog: build_runlog 产物（或其 JSON 反序列化结果）。
        corpus_content_hashes: 语料内容哈希集合；给定时启用 evidence_traceable 校验。
        max_fabricated: 允许的最大伪造引用数（默认 0 = 零容忍）。
    """
    checks: dict[str, bool] = {}
    errors: list[str] = []

    events = runlog.get("events", []) or []
    manifest = runlog.get("manifest", {}) or {}
    run = runlog.get("run", {}) or {}

    # ---- 1. schema_version ----
    sv = runlog.get("schema_version")
    checks["schema_version"] = sv == RUNLOG_SCHEMA_VERSION
    if not checks["schema_version"]:
        errors.append(
            f"schema_version 不匹配: 期望 {RUNLOG_SCHEMA_VERSION!r}, 实得 {sv!r}"
        )

    # ---- 2. seq_contiguous: events seq == [1..N] ----
    seqs = [e.get("seq") for e in events]
    expected = list(range(1, len(events) + 1))
    checks["seq_contiguous"] = seqs == expected
    if not checks["seq_contiguous"]:
        errors.append(f"事件 seq 不连续: 期望 {expected}, 实得 {seqs}")

    # ---- 3. manifest_counts ----
    counts_ok = (
        manifest.get("event_count") == len(events)
        and manifest.get("tool_invocation_count") == len(runlog.get("tool_invocations", []) or [])
        and manifest.get("evidence_count") == len(runlog.get("evidence_refs", []) or [])
    )
    checks["manifest_counts"] = counts_ok
    if not counts_ok:
        errors.append(
            "manifest 计数与实际长度不符: "
            f"event_count={manifest.get('event_count')}/actual={len(events)}, "
            f"tool_invocation_count={manifest.get('tool_invocation_count')}/"
            f"actual={len(runlog.get('tool_invocations', []) or [])}, "
            f"evidence_count={manifest.get('evidence_count')}/"
            f"actual={len(runlog.get('evidence_refs', []) or [])}"
        )

    # ---- 4. hash_chain（legacy 容忍） ----
    _verify_hash_chain(events, run, checks, errors)

    # ---- 5. content_hash_match ----
    declared = manifest.get("content_sha256", "")
    manifest_wo_hash = {k: v for k, v in manifest.items() if k != "content_sha256"}
    body_wo_manifest = {k: v for k, v in runlog.items() if k != "manifest"}
    recomputed = _content_sha256({**body_wo_manifest, "manifest": manifest_wo_hash})
    checks["content_hash_match"] = bool(declared) and recomputed == declared
    if not checks["content_hash_match"]:
        errors.append(
            f"content_sha256 不匹配: 声明 {declared!r}, 重算 {recomputed!r}（body 可能被篡改）"
        )

    # ---- 6. evidence_traceable（仅当给 corpus_content_hashes） ----
    if corpus_content_hashes is not None:
        # codex P1-6：runlog/v1（P3-2 era）下 source_content_sha256 由可选改为强制；
        # 旧 schema（sv != runlog/v1）仍宽松跳过缺字段的 ref，向后兼容旧 run。
        strict_source_sha = sv == RUNLOG_SCHEMA_VERSION
        _verify_evidence_traceable(
            runlog.get("evidence_refs", []) or [],
            corpus_content_hashes,
            checks,
            errors,
            strict_source_sha=strict_source_sha,
        )

    # ---- 7. zero_fabrication ----
    fab = manifest.get("fabricated_count", 0) or 0
    checks["zero_fabrication"] = fab <= max_fabricated
    if not checks["zero_fabrication"]:
        errors.append(f"伪造引用数 {fab} 超过上限 {max_fabricated}")

    ok = all(checks.values())
    return VerifyReport(ok=ok, checks=checks, errors=errors)


def _verify_hash_chain(
    events: list[dict], run: dict, checks: dict[str, bool], errors: list[str]
) -> None:
    """重算哈希链；event_hash 缺失（null/空）的链视作 legacy 不可验证（不判失败）。"""
    if not events:
        checks["hash_chain"] = True
        return

    # 任一事件缺 event_hash → legacy 链：不可验证，不判失败
    if any(not e.get("event_hash") for e in events):
        checks["hash_chain"] = True
        checks["chain_legacy"] = True
        errors.append(
            "[info] 存在缺失 event_hash 的事件（legacy run），哈希链不可验证 → 跳过链校验（不判失败）"
        )
        return

    run_id = run.get("id")
    prev = ""
    chain_ok = True
    for e in events:
        if e.get("prev_hash") != prev:
            chain_ok = False
            errors.append(
                f"事件 seq={e.get('seq')} 的 prev_hash 断链: "
                f"期望 {prev!r}, 实得 {e.get('prev_hash')!r}"
            )
            break
        expected_hash = _event_hash(
            prev, run_id, e.get("seq"), e.get("type"), e.get("payload"), e.get("ts")
        )
        if expected_hash != e.get("event_hash"):
            chain_ok = False
            errors.append(
                f"事件 seq={e.get('seq')} 的 event_hash 重算不符: "
                f"期望 {expected_hash!r}, 实得 {e.get('event_hash')!r}（事件可能被篡改）"
            )
            break
        prev = e.get("event_hash")
    checks["hash_chain"] = chain_ok


def _verify_evidence_traceable(
    evidence_refs: list[dict],
    corpus_content_hashes: set[str],
    checks: dict[str, bool],
    errors: list[str],
    *,
    strict_source_sha: bool = False,
) -> None:
    """green/yellow 证据须含 source_content_sha256 且命中语料哈希集合。

    codex P1-6：
      - strict_source_sha=True（runlog/v1，P3-2 era）：green/yellow 证据缺
        source_content_sha256 → 判失败（核心溯源保证从可选改为强制）。
      - strict_source_sha=False（旧 schema）：缺字段的 ref（pre-P3-2）→ 跳过并记 note，
        不判失败（向后兼容旧 run）。
    """
    ok = True
    for ref in evidence_refs:
        if ref.get("match_quality") not in ("green", "yellow"):
            continue
        sch = ref.get("source_content_sha256")
        if sch is None:
            if strict_source_sha:
                ok = False
                errors.append(
                    f"证据 paper_id={ref.get('paper_id')} 缺 source_content_sha256"
                    "（runlog/v1 要求强制溯源 → 失败）"
                )
            else:
                errors.append(
                    f"[info] 证据 paper_id={ref.get('paper_id')} 缺 source_content_sha256"
                    "（pre-P3-2）→ 跳过溯源校验"
                )
            continue
        if sch not in corpus_content_hashes:
            ok = False
            errors.append(
                f"证据 paper_id={ref.get('paper_id')} 的 source_content_sha256 "
                f"{sch!r} 不在语料哈希集合中（不可溯源）"
            )
    checks["evidence_traceable"] = ok
