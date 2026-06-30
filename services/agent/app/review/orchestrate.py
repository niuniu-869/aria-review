"""全文综述端到端编排 — 阶段 5-3a

核心功能：
  run_review(topic, paper_markdowns, records, *, concurrency, override)
    → dict: review_md, summaries, validation_summary, evidence_refs, stats

管线：
  paper_markdowns (list[dict])
      ↓  map 阶段（并发）
  summarize_papers(...)   → list[PaperSummary]
      ↓  reduce 阶段（流式）
  generate_review(...)    → AsyncIterator[ReviewEvent]
      ↓  消费事件流
  review_md + stats

设计决策：
  - map 与 reduce 解耦：map 失败的占位摘要仍传入 reduce（不拖垮 reduce 阶段）
  - stats 包含：论文数、成功摘要数、综述字数、引用数、耗时
  - 完全兼容 FakeLLMClient（无 key 离线可运行）
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.harness.llm import OverrideLLMConfig
from app.review.read import PaperSummary, summarize_papers
from app.review.synthesis import (
    ReviewEvent,
    build_provenance_and_anchors,
    generate_review,
)
from app.review.templates import Template

logger = logging.getLogger("agent.review.orchestrate")


def _strip_preamble(md: str) -> str:
    """去掉 LLM 在综述正文前可能输出的寒暄/确认语。

    保留从第一个 markdown 标题行 (以 # 开头) 起的内容; 无标题则原样返回。
    """
    lines = md.splitlines()
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            return "\n".join(lines[i:]).strip()
    return md.strip()


async def run_review(
    topic: str,
    paper_markdowns: list[dict],
    records: list[dict],
    *,
    template: Template | None = None,
    concurrency: int = 4,
    override: OverrideLLMConfig | None = None,
) -> dict[str, Any]:
    """端到端综述编排入口。

    Args:
        topic:           综述研究主题
        paper_markdowns: 论文列表，每条 dict 至少含：
                           - meta: dict (paper_id/title/authors/year)
                           - markdown: str (论文全文)
                         也接受扁平格式（含 title/markdown 等字段）
        records:         题录列表，供 GuardedStream 引用校验使用
                         格式：[{idx, title, authors, year, doi}, ...]
        template:        论型模板（可选）；给定时单遍注入章节大纲 + 抗幻觉指令。
                         None 时旧行为完全不变。
        concurrency:     map 阶段最大并发数（Semaphore）
        override:        per-request LLM 配置覆盖（用户自带 key）

    Returns:
        dict:
          review_md:         综述 Markdown 全文
          summaries:         list[PaperSummary]（含失败占位）
          validation_summary: dict  {total_segments, valid_citations,
                                     fabricated_citations, fabricated_spans}
          evidence_refs:     list[EvidenceRef]
          provenance_map:    dict[str, dict]  # B4b/B4c：occurrence anchor_id →
                                              # {paper_id, attachment_id, page_no,
                                              #  block_idx, bbox, section_title, quote, ...}
                                              # reduce 校验链失败时为空 dict。
          error:             str | None  # codex P0-2：非 None = reduce 阶段校验链
                                         # 失败（如校验器崩溃 fail-closed）；调用方据此判失败
          stats:             dict  {
                               total_papers: int,          # 输入论文总数
                               success_summaries: int,     # 成功摘要数
                               error_summaries: int,       # 失败摘要数（占位）
                               review_chars: int,          # 综述字数
                               valid_citations: int,       # 有效引用数
                               fabricated_citations: int,  # 伪造引用数
                               elapsed_map_s: float,       # map 阶段耗时（秒）
                               elapsed_reduce_s: float,    # reduce 阶段耗时（秒）
                               elapsed_total_s: float,     # 总耗时（秒）
                             }
    """
    t0 = time.monotonic()

    # ====================================================================
    # 阶段 1：map（并发逐篇精读）
    # ====================================================================
    logger.info(
        f"[run_review] 开始 map 阶段：{len(paper_markdowns)} 篇论文，"
        f"并发数={concurrency}，主题={topic[:40]!r}"
    )

    t_map_start = time.monotonic()
    summaries: list[PaperSummary] = await summarize_papers(
        papers=paper_markdowns,
        topic=topic,
        concurrency=concurrency,
        override=override,
    )
    t_map_end = time.monotonic()
    elapsed_map = t_map_end - t_map_start

    success_count = sum(1 for s in summaries if not s.is_error())
    error_count = sum(1 for s in summaries if s.is_error())
    logger.info(
        f"[run_review] map 阶段完成：成功={success_count}，失败={error_count}，"
        f"耗时={elapsed_map:.1f}s"
    )

    # ====================================================================
    # 阶段 2：reduce（流式生成综述）
    # ====================================================================
    logger.info(
        f"[run_review] 开始 reduce 阶段：摘要数={len(summaries)}，records={len(records)}"
    )

    t_reduce_start = time.monotonic()

    review_chunks: list[str] = []
    validation_summary: dict = {}
    evidence_refs: list = []
    reduce_error: str | None = None  # codex P0-2：reduce 阶段 error 事件 → 上抛给调用方

    async for event in generate_review(
        topic=topic,
        summaries=summaries,
        records=records,
        template=template,
        override=override,
    ):
        if event.event == "text_chunk":
            review_chunks.append(event.data or "")
        elif event.event == "validation_summary":
            validation_summary = event.data or {}
        elif event.event == "evidence_refs":
            evidence_refs = event.data or []
        elif event.event == "error":
            # codex P0-2：reduce 阶段错误（含 GuardedStream fail-closed 的
            # ValidationUnavailableError）不能被静默吞掉 —— 记录并上报给调用方，
            # 由 ReviewTool 据此判失败、不发 review_complete（绝不放未校验综述）。
            logger.error(f"[run_review] reduce 阶段错误: {event.data}")
            reduce_error = str(event.data)
        elif event.event == "done":
            logger.info(f"[run_review] reduce 阶段完成: {event.data}")

    t_reduce_end = time.monotonic()
    elapsed_reduce = t_reduce_end - t_reduce_start
    t_total = t_reduce_end - t0

    review_md = _strip_preamble("".join(review_chunks))

    # ====================================================================
    # 阶段 3：provenance 后处理（B4b/B4c，纯函数、确定性，不调 LLM）
    #   把已定位 key_points 汇总成 provenance_map，并把 review_md 里每个"已定位"
    #   引用出现位置包裹 [[anchor:<id>]][n][[/anchor]]（occurrence anchor，不去重）。
    #   仅在正常路径（reduce_error 为 None）执行；reduce 校验链失败时不引用半成品综述，
    #   provenance_map 保持空 dict。
    # ====================================================================
    provenance_map: dict[str, dict] = {}
    if reduce_error is None:
        annotated_md, provenance_map = build_provenance_and_anchors(
            review_md, summaries, records
        )
        review_md = annotated_md

    valid_count = validation_summary.get("valid_citations", 0)
    fabricated_count = validation_summary.get("fabricated_citations", 0)

    stats: dict[str, Any] = {
        "total_papers": len(paper_markdowns),
        "success_summaries": success_count,
        "error_summaries": error_count,
        "review_chars": len(review_md),
        "valid_citations": valid_count,
        "fabricated_citations": fabricated_count,
        # B4b/B4c：本次注入的 occurrence anchor（= provenance 条目）数。
        "provenance_entries": len(provenance_map),
        "elapsed_map_s": round(elapsed_map, 2),
        "elapsed_reduce_s": round(elapsed_reduce, 2),
        "elapsed_total_s": round(t_total, 2),
    }

    logger.info(
        f"[run_review] 完成：综述 {len(review_md)} 字符，"
        f"有效引用={valid_count}，伪造引用={fabricated_count}，"
        f"总耗时={t_total:.1f}s"
    )

    return {
        "review_md": review_md,
        "summaries": summaries,
        "validation_summary": validation_summary,
        "evidence_refs": evidence_refs,
        # B4b/B4c：occurrence anchor → 溯源条目映射（前端凭 anchor_id 点回原文 block/page）。
        # reduce 校验链失败时为空 dict（不引用半成品综述）。
        "provenance_map": provenance_map,
        "stats": stats,
        # codex P0-2：非 None 表示 reduce 阶段校验链失败（如校验器崩溃 fail-closed）。
        # ReviewTool 据此判失败、不发 review_complete。
        "error": reduce_error,
    }
