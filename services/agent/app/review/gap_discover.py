"""A5 · GAP 发现编排 — gap-finder subagent + scratchpad 迭代产出 GapCandidate。

设计依据：
  - 输入：一批论文结构化摘要（PaperSummary，来自 review/read.py map 阶段）+ 主题。
  - gap-finder subagent（最小授权 read_paper + scratchpad）按 concept/method/theory 三 lens
    发现 GAP，逐字溯源，经 scratchpad.add 落库（gap_candidate 表）。
  - 权威产物 = scratchpad store.list(run_id)（不解析 LLM 自由文本，铁律 §4）。
  - 本层只发现不裁决；价值由 value_check 确定性 resolver 决定。领域无关。
"""
from __future__ import annotations

import logging
from typing import Any

from app.agent.dispatch import dispatch_to_skill
from app.agent.scratchpad import Scratchpad, ScratchpadStore

logger = logging.getLogger("agent.review.gap_discover")

GAP_FINDER_SKILL = "gap-finder"

# 喂给 subagent 的摘要上限（防 context 撑爆；需深读由 read_paper 按需导航补足）。
_MAX_SUMMARIES_IN_PROMPT = 60


def _format_summaries(paper_summaries: list[dict]) -> str:
    """把 PaperSummary 列表压成紧凑提示文本。

    关键：必须带上 key_points 的 anchor_id + 逐字 source_quote —— gap-finder 据此为
    GapCandidate.supporting_papers 提供合法 anchor（无 anchor 的 GAP 会被 scratchpad fail-loud 拒）。
    缺了这些锚点, 模型只能去 read_paper 现找, 易在轮数内产 0 条(A5 接手实测复现的坑)。
    """
    lines: list[str] = []
    # 跳过 error 占位摘要（summarize 失败隔离产生 error_placeholder）：喂进去只会让
    # gap-finder 对无内容的论文去 read_paper 现找，徒耗轮次（codex 二审确定性因素）。
    valid = [ps for ps in (paper_summaries or []) if not ps.get("error")]
    for ps in valid[:_MAX_SUMMARIES_IN_PROMPT]:
        pid = ps.get("paper_id")
        title = str(ps.get("title") or "")[:120]
        rq = str(ps.get("research_question") or "")[:180]
        method = str(ps.get("method") or "")[:100]
        findings = "; ".join(str(f) for f in (ps.get("findings") or [])[:3])[:240]
        line = f"[paper_id={pid}] {title}\n  研究问题: {rq}\n  方法: {method}\n  主要发现: {findings}"
        anchors = []
        for kp in (ps.get("key_points") or [])[:3]:
            aid = kp.get("anchor_id")
            quote = str(kp.get("source_quote") or kp.get("claim") or "")[:160]
            if aid and quote:
                anchors.append(f'    · anchor_id={aid} quote="{quote}"')
        if anchors:
            line += "\n  可引用锚点(直接用于 supporting_papers):\n" + "\n".join(anchors)
        lines.append(line)
    return "\n".join(lines)


async def discover_gaps(
    *,
    topic: str,
    paper_summaries: list[dict],
    registry: Any,
    llm_router: Any,
    base_context: dict,
    run_id: str,
    store: ScratchpadStore,
    project_id: int | None = None,
    max_candidates: int = 12,
    depth: int = 0,
    deadline: float | None = None,
    publisher: Any = None,
    llm_override: Any = None,
) -> dict:
    """发现 GAP，写入 scratchpad，返回 {run_id, gaps, outcome, tool_failures}。

    gaps = store.list(run_id) 的权威产物（GapCandidate dict 列表）。
    fail-loud：subagent 非 ok 时 outcome 透出（不抛断父 run），调用方据此置 job 状态。
    """
    pad = Scratchpad(run_id, store, project_id=project_id)
    ctx = {
        **base_context,
        "scratchpad": pad,
        "run_id": run_id,
        "project_id": project_id,
        "topic": topic,
        "paper_summaries": paper_summaries,
    }
    # 合法 paper_id 白名单（过滤 error 占位，与 _format_summaries 同口径）：约束 gap-finder
    # 的 read_paper 只能读本批论文，杜绝幻觉/越界 id（dogfood 实测 LLM 去读不在本项目的
    # paper 571 → tool failure → 耗尽轮次产 0 条；codex 二审：prompt 增强降概率，非根治）。
    valid_ids = [str(ps.get("paper_id")) for ps in (paper_summaries or [])
                 if not ps.get("error") and ps.get("paper_id") is not None]
    # codex review P1：无有效摘要（全 error 占位 / 缺 paper_id）→ 不派发 gap-finder，
    # fail-loud outcome=error。否则空任务可能返回 outcome=ok 被标 done_empty，把"上游精读
    # 全失败"静默伪装成"正常跑完未发现"（与问题3 同类静默吞错）。
    if not valid_ids:
        logger.error(
            "[gap_discover] run=%s 无有效论文摘要（全部 error 占位/缺 paper_id），不派发 gap-finder，fail-loud",
            run_id,
        )
        return {
            "run_id": run_id,
            "gaps": [],
            "outcome": "error",
            "tool_failures": 0,
            "tool_failure_reasons": ["无有效论文摘要可供发现 GAP（上游精读可能全部失败）"],
        }
    task = (
        f"主题：{topic}\n"
        f"目标：从以下 {len(valid_ids)} 篇论文摘要中发现至多 {max_candidates} 条结构化研究空白（GAP），"
        f"按 concept/method/theory 三视角，每条逐字溯源（supporting_papers 须带 paper_id+anchor_id+quote），"
        f"调 scratchpad.add 落库。\n"
        f"**优先直接使用摘要中已给出的 anchor_id（“可引用锚点”）填 supporting_papers，通常无需再调 read_paper**；"
        f"确需核实逐字证据时，read_paper 的 paper_id **只能取自下面白名单，禁止编造或使用其它项目的 id**："
        f"[{', '.join(valid_ids)}]。只发现不裁决。\n\n"
        f"论文摘要：\n{_format_summaries(paper_summaries)}"
    )
    result = await dispatch_to_skill(
        skill_id=GAP_FINDER_SKILL,
        task=task,
        registry=registry,
        llm_router=llm_router,
        base_context=ctx,
        depth=depth,
        deadline=deadline,
        publisher=publisher,
        llm_override=llm_override,
    )
    gaps = await store.list(run_id)
    logger.info(
        "[gap_discover] run=%s outcome=%s gaps=%d failures=%d",
        run_id, result.outcome, len(gaps), result.tool_failures,
    )
    return {
        "run_id": run_id,
        "gaps": [g.to_dict() for g in gaps],
        "outcome": result.outcome,
        "tool_failures": result.tool_failures,
        # 透出失败原因摘要供调用方写 job.error（问题3：outcome 非 ok 时显式 failed）。
        "tool_failure_reasons": list(getattr(result, "tool_failure_reasons", []) or []),
    }
