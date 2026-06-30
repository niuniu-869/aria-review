"""引用存在性校验器 — 阶段 4b 安全带核心校验逻辑。

架构:
  1. check_citations_against_records() — 公开入口
     - 调用底层 check_citations 做引用存在性判断
     - 按 strategy 处理失败: ANNOTATE 追加警告, REJECT 抛错, NOOP 保留原文
     - 从 cites 中产出 EvidenceRef 列表
     - 返回 CitationCheckResult

为什么不用 Guardrails 运行时依赖:
  GuardedStream 已自行实现句/章边界缓冲, 本模块只需要同步校验单段文本。
  直接复用项目内 check_citations 能保持安全带语义, 并避免部署依赖不可获取的旧版包。
"""
from __future__ import annotations

import re
import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Optional

from app.cite_check import check_citations
from .evidence import EvidenceRef

logger = logging.getLogger("agent_safety.citation")

# ======================================================================
# 策略枚举
# ======================================================================

class CitationFailStrategy:
    """引用校验失败时的处理策略。

    ANNOTATE: 在文本末尾追加警告注释, 允许输出继续
    REJECT:   拒绝该段, 抛出异常
    NOOP:     不修改文本, 仅在元数据中标记
    """
    ANNOTATE = "annotate"
    REJECT = "reject"
    NOOP = "noop"


# 阶段 5-2b 修复: 原模块级全局 _records_context (list[dict]) 并发不安全。
# 改用 contextvars.ContextVar: 每个 asyncio Task / 线程拥有独立的 records 上下文,
# 两路并发校验使用不同 records 不会互串。
#
# 使用方式:
#   token = _records_context_var.set(records)
#   try:
#       ... 调用 check_citations_against_records ...
#   finally:
#       _records_context_var.reset(token)
_records_context_var: ContextVar[list[dict]] = ContextVar(
    "_records_context_var", default=[]
)


# ======================================================================
# 返回结构
# ======================================================================

@dataclass
class CitationCheckResult:
    """引用存在性校验结果。

    Attributes:
        annotated:      标注了引用状态的文本 (✅/⚠️/❌ 标记)
        validated_output: 策略处理后的文本 (ANNOTATE 策略下含警告注释)
        summary:        各类引用计数 {green, yellow, red}
        fabricated:     疑似伪造的引用字符串列表
        evidence_refs:  命中语料记录的 EvidenceRef 列表
        validation_passed: 是否通过校验 (无 red 引用)
    """
    annotated: str
    validated_output: str
    summary: dict
    fabricated: list[str] = field(default_factory=list)
    evidence_refs: list[EvidenceRef] = field(default_factory=list)
    validation_passed: bool = True


# ======================================================================
# 公开校验入口
# ======================================================================

def check_citations_against_records(
    text: str,
    records: list[dict],
    strategy: str = CitationFailStrategy.ANNOTATE,
    corpus_id: str = "local_corpus",
) -> CitationCheckResult:
    """引用存在性校验: 将文本中的引用解析到 records, 返回校验结果。

    核心逻辑:
      1. 调用 app.cite_check.check_citations 提取所有引用并做三色判定
      2. 按 strategy 处理 red 引用
         - strategy=ANNOTATE → 追加警告注释
         - strategy=REJECT   → 引发异常
         - strategy=NOOP     → 不修改文本
      3. 对 green/yellow 引用逐条产出 EvidenceRef

    Args:
        text:       待校验文本 (通常是 assistant 输出的一个段落或完整综述)
        records:    语料记录列表, 每条 {title, authors, year, doi, pmid?, idx?, ...}
        strategy:   失败策略 (ANNOTATE / REJECT / NOOP)
        corpus_id:  语料/项目标识, 写入 EvidenceRef.corpus_id

    Returns:
        CitationCheckResult

    Raises:
        ValueError: 当 strategy=REJECT 且有伪造引用时
    """
    # 阶段 5-2b: 使用 ContextVar 取代全局变量，并发安全。
    # set() 返回 Token，用于在 finally 中 reset() 恢复上层上下文（支持嵌套调用）。
    _ctx_token = _records_context_var.set(records)
    try:
        return _check_citations_impl(
            text=text,
            records=records,
            strategy=strategy,
            corpus_id=corpus_id,
        )
    finally:
        _records_context_var.reset(_ctx_token)


def _check_citations_impl(
    text: str,
    records: list[dict],
    strategy: str = CitationFailStrategy.ANNOTATE,
    corpus_id: str = "local_corpus",
) -> "CitationCheckResult":
    """check_citations_against_records 的实际实现（被 ContextVar 包装层调用）。"""
    # 空文本快速返回 (避免 Guard.validate("") 产生误报 validation_passed=False)
    if not text or not text.strip():
        return CitationCheckResult(
            annotated="",
            validated_output="",
            summary={"green": 0, "yellow": 0, "red": 0},
            fabricated=[],
            evidence_refs=[],
            validation_passed=True,
        )

    # 步骤 1: 底层引用提取 + 三色判定 (产出 annotated + cites + summary)
    raw_result = check_citations(text, records)

    fabricated = [c["text"] for c in raw_result["cites"] if c["status"] == "red"]
    validation_passed = raw_result["summary"]["red"] == 0
    validated_output = _apply_fail_strategy(text, fabricated, strategy)

    # 步骤 4: 从 cites 提取 EvidenceRef (仅 green/yellow, 即命中语料的)
    evidence_refs: list[EvidenceRef] = []
    for cite in raw_result["cites"]:
        if cite["status"] in ("green", "yellow") and cite["matched_idx"] is not None:
            idx = cite["matched_idx"]  # 1-based
            if 1 <= idx <= len(records):
                record = records[idx - 1]
                # codex P1-3：优先用真实 DB paper.id（record["paper_id"]），证据可可靠回指
                # 库内论文；无 paper_id 时回退引用序号 idx（record["idx"] / matched idx），
                # 向后兼容仅含 idx 的旧 records。
                paper_id_val = record.get("paper_id", record.get("idx", idx))
                ref = EvidenceRef.from_record(
                    paper_id=int(paper_id_val) if paper_id_val is not None else idx,
                    record=record,
                    span=cite["text"],
                    claim=_extract_sentence_context(text, cite["text"]),
                    corpus_id=corpus_id,
                    cite_type=cite["type"],
                    match_quality=cite["status"],
                    # P3-2: 透传文档内容溯源哈希（records 由项目加载函数填充 content_sha256）
                    source_content_sha256=record.get("content_sha256"),
                )
                evidence_refs.append(ref)

    return CitationCheckResult(
        annotated=raw_result["annotated"],
        validated_output=validated_output,
        summary=raw_result["summary"],
        fabricated=fabricated,
        evidence_refs=evidence_refs,
        validation_passed=validation_passed,
    )


# ======================================================================
# 工具函数
# ======================================================================

def _apply_fail_strategy(text: str, fabricated: list[str], strategy: str) -> str:
    """按失败策略处理文本, 保持 GuardedStream 期望的放行/拒绝语义。"""
    if not fabricated:
        return text

    error_msg = (
        f"发现 {len(fabricated)} 条疑似伪造引用: "
        + ", ".join(f'"{t}"' for t in fabricated[:5])
        + ("..." if len(fabricated) > 5 else "")
    )

    if strategy == CitationFailStrategy.REJECT:
        raise ValueError(error_msg)
    if strategy == CitationFailStrategy.NOOP:
        return text
    return text + f"\n\n> ⚠️ **引用警告**: {error_msg}"

def _extract_sentence_context(text: str, span: str, window: int = 120) -> Optional[str]:
    """提取包含 span 的上下文句子 (用于 EvidenceRef.claim)。"""
    if not text or not span:
        return None
    pos = text.find(span)
    if pos < 0:
        return None
    # 扩展到句子边界 (中英文句号/换行)
    start = max(0, pos - window)
    end = min(len(text), pos + len(span) + window)
    snippet = text[start:end].strip()
    # 截取最近的句子分隔符
    for sep in ("。", ".", "\n"):
        parts = snippet.split(sep)
        for part in parts:
            if span in part:
                return part.strip()[:200]
    return snippet[:200]
