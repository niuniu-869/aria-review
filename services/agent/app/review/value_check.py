"""A4 · 研究方向价值二次验证 — 确定性 resolver + 计量结构佐证 + subagent 编排。

设计依据：

  分层铁律：
    - LLM（value-evidence subagent）只攒证据：把 GAP 论断转检索式做**反向检索证伪**，
      逐字回传命中（title/year/doi），**绝不下结论**。
    - 确定性代码做 ①计量结构佐证（用已算好的 R 共现网络查两概念断层，不重算）
      ②最终裁决（resolve_value_verdict，decided_by 恒 "deterministic"）。

  反"LLM 拍脑袋"命门：
    valuable      ← (hit_count ≤ reverse_hit_low) AND structural_hole
    likely_filled ← (hit_count ≥ reverse_hit_high)        # 伪空白：检索没做全
    inconclusive  ← 其余
    阈值透明、可配、可按领域传参（§0.3 领域无关：工程领域文献密度不同，阈值走参数）。

  本模块零商科/会计领域词（§0.3）。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.agent.dispatch import OUTCOME_OK, dispatch_to_skill

logger = logging.getLogger("agent.review.value_check")

# 透明默认阈值（契约 ValueThresholds）。可按领域传参覆盖。
DEFAULT_THRESHOLDS: dict[str, int] = {"reverse_hit_high": 25, "reverse_hit_low": 3}

# 共现边权 < 此值视为「结构断层」（两概念几乎不共现）。领域无关的结构信号。
DEFAULT_COOCCURRENCE_MIN_WEIGHT = 1.0

# 价值核验 subagent skill 名（A3 subagent_specs 已注册）。
VALUE_EVIDENCE_SKILL = "value-evidence"


class ValueCheckError(RuntimeError):
    """价值核验不可用（subagent fail-loud 出错 / 证据缺失）。

    fail-loud：subagent 派发非 ok（超时/越权/异常）或回传畸形证据时抛出，
    上游据此产 error 事件，绝不静默捏造 verdict（铁律 §5）。
    """


# ======================================================================
# 工具：概念归一化与节点匹配（确定性，零 LLM）
# ======================================================================

def _norm(s: Any) -> str:
    return str(s or "").strip().casefold()


def _match_node_id(concept: str, nodes: list[dict]) -> Optional[str]:
    """在网络节点里按 label 定位概念，返回 node id；找不到→None。

    匹配优先级（确定性）：归一化精确 == > 双向子串包含（取 label 最短的命中，避免宽泛词误命中）。
    """
    target = _norm(concept)
    if not target:
        return None
    exact = [n for n in nodes if _norm(n.get("label")) == target]
    if exact:
        return str(exact[0].get("id"))
    contains = [
        n for n in nodes
        if target in _norm(n.get("label")) or _norm(n.get("label")) in target
    ]
    if contains:
        contains.sort(key=lambda n: len(_norm(n.get("label"))))
        return str(contains[0].get("id"))
    return None


def _edge_weight(a_id: str, b_id: str, edges: list[dict]) -> Optional[float]:
    """两节点间共现边权（无向，取最大）；无边→None。"""
    weights: list[float] = []
    for e in edges:
        s, t = str(e.get("source")), str(e.get("target"))
        if {s, t} == {str(a_id), str(b_id)}:
            try:
                weights.append(float(e.get("weight", 0.0)))
            except (TypeError, ValueError):
                continue
    return max(weights) if weights else None


# ======================================================================
# 计量结构佐证（确定性：用已算好的 R 共现网络，不重算）
# ======================================================================

def structural_hole(
    concept_a: str,
    concept_b: str,
    graph: dict | None,
    *,
    min_weight: float = DEFAULT_COOCCURRENCE_MIN_WEIGHT,
    source_view: str | None = "conceptual",
) -> tuple[dict, bool]:
    """查两核心概念在共现网络中是否存在「结构断层」。

    返回 (BiblioStructure dict, hole: bool)。BiblioStructure 字段对齐契约（必填全给）。

    判定（确定性，零 LLM）：
      - 任一概念未在网络定位到节点 → **无法佐证**（hole=False）：不能把"没找到"当"有断层"。
      - 两概念都在、但无连边或边权 < min_weight → **结构断层**（hole=True）：几乎不共现。
      - 边权 ≥ min_weight → 无断层（hole=False）：两概念已被一起研究。
    """
    nodes = (graph or {}).get("nodes") or []
    edges = (graph or {}).get("edges") or []

    a_id = _match_node_id(concept_a, nodes)
    b_id = _match_node_id(concept_b, nodes)

    if not a_id or not b_id:
        missing = concept_a if not a_id else concept_b
        return (
            {
                "metric": "cooccurrence_gap",
                "value": 0.0,
                "interpretation": f"共现网络中未定位到概念「{missing}」，无法佐证结构断层（不计为断层）",
                "source_view": None,
            },
            False,
        )

    w = _edge_weight(a_id, b_id, edges)
    if w is None or w < min_weight:
        wtxt = "无共现连边" if w is None else f"共现边权 {w:.2f} < 阈值 {min_weight:.2f}"
        return (
            {
                "metric": "cooccurrence_gap",
                "value": float(w or 0.0),
                "interpretation": f"「{concept_a}」与「{concept_b}」{wtxt}，存在共现断层（结构性空白佐证）",
                "source_view": source_view,
            },
            True,
        )
    return (
        {
            "metric": "low_coupling",
            "value": float(w),
            "interpretation": f"「{concept_a}」与「{concept_b}」共现边权 {w:.2f} ≥ 阈值，两概念已被共同研究（不支持空白）",
            "source_view": source_view,
        },
        False,
    )


# ======================================================================
# 确定性裁决（纯函数，零 LLM）— 反"LLM 拍脑袋"命门
# ======================================================================

def _is_hole(biblio_structure: dict | None) -> bool:
    """从 BiblioStructure 判断是否结构断层（确定性）。"""
    bs = biblio_structure or {}
    if bs.get("metric") == "cooccurrence_gap":
        # cooccurrence_gap 且 value < min → 断层；value==0 且 interpretation 标注"未定位"→ 非断层
        if "未定位" in str(bs.get("interpretation") or ""):
            return False
        return True
    return False


def resolve_value_verdict(evidence_pack: dict, thresholds: dict | None = None) -> dict:
    """按 §2.3 确定性规则，从 EvidencePack 出 ValueVerdict。

    decided_by 恒 "deterministic"；LLM 绝不参与本裁决。纯函数、可单测、阈值可配。
    """
    if not isinstance(evidence_pack, dict):
        raise ValueCheckError("evidence_pack 必须是 dict")
    rs = evidence_pack.get("reverse_search")
    if not isinstance(rs, dict) or "hit_count" not in rs:
        raise ValueCheckError("evidence_pack.reverse_search.hit_count 缺失（无法裁决，fail-loud）")

    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    high, low = int(th["reverse_hit_high"]), int(th["reverse_hit_low"])
    if low > high:
        raise ValueCheckError(f"阈值非法：reverse_hit_low({low}) > reverse_hit_high({high})")

    try:
        hit = int(rs.get("hit_count"))
    except (TypeError, ValueError):
        raise ValueCheckError(f"hit_count 非整数: {rs.get('hit_count')!r}")
    hit = max(0, hit)

    hole = _is_hole(evidence_pack.get("biblio_structure"))
    gap_id = str(evidence_pack.get("gap_id") or "")

    # 连续新颖度分（透明、可解释）：hit≤low→1，hit≥high→0，线性内插；结构断层加权。
    span = max(1, high - low)
    novelty = max(0.0, min(1.0, (high - hit) / span))
    score = round(0.7 * novelty + 0.3 * (1.0 if hole else 0.0), 3)

    if hit >= high:
        verdict = "likely_filled"
        rationale = (
            f"反向检索命中 {hit} ≥ 阈值 {high}：该方向已有大量研究，疑为「伪空白」"
            f"（检索未做全所致）。结构断层={hole}。"
        )
    elif hit <= low and hole:
        verdict = "valuable"
        rationale = (
            f"反向检索命中 {hit} ≤ 阈值 {low}（鲜有研究）且共现网络显示两核心概念存在结构断层 → "
            f"研究方向价值成立（真空白）。"
        )
    else:
        verdict = "inconclusive"
        reason = []
        if hit > low:
            reason.append(f"命中 {hit} 介于 {low}~{high}（既非鲜有亦非饱和）")
        if hit <= low and not hole:
            reason.append("命中虽少但计量网络未佐证结构断层（缺一不可）")
        rationale = "证据不足以判定：" + "；".join(reason or ["证据不充分"]) + "。"

    return {
        "gap_id": gap_id,
        "verdict": verdict,
        "score": score,
        "thresholds": {"reverse_hit_high": high, "reverse_hit_low": low},
        "rationale": rationale,
        "decided_by": "deterministic",
    }


# ======================================================================
# 编排：派 value-evidence subagent 攒证 → 计量佐证 → 确定性裁决
# ======================================================================

def _build_reverse_search(pack_data: list[dict]) -> dict:
    """从 subagent 提交的 evidence pack 数据里组装 ReverseSearch（去重防虚高）。

    去重键：doi（小写）优先，无 doi 用归一化 title。hit_count = 去重后命中数。
    """
    query = ""
    provider = "openalex"
    raw_hits: list[dict] = []
    for pack in pack_data or []:
        rs = (pack or {}).get("reverse_search") or {}
        if rs.get("query"):
            query = str(rs["query"])
        if rs.get("provider") in ("sciverse", "openalex"):
            provider = rs["provider"]
        for h in rs.get("hits") or rs.get("top_hits") or []:
            if isinstance(h, dict):
                raw_hits.append(h)

    seen: set[str] = set()
    deduped: list[dict] = []
    for h in raw_hits:
        doi = _norm(h.get("doi"))
        key = f"doi:{doi}" if doi else f"title:{_norm(h.get('title'))}"
        if not key or key in ("doi:", "title:") or key in seen:
            continue
        seen.add(key)
        deduped.append({
            "title": str(h.get("title") or ""),
            "year": h.get("year") if isinstance(h.get("year"), int) else None,
            "doi": h.get("doi") or None,
            "relevance": float(h.get("relevance", 0.0) or 0.0),
        })
    return {
        "query": query,
        "provider": provider,
        "hit_count": len(deduped),       # 去重后计数（防同一文献多源虚高，契约 §2.4-1/A4 codex 二审项）
        "top_hits": deduped[:10],
    }


async def verify_gap_value(
    gap: dict,
    *,
    registry: Any,
    llm_router: Any,
    base_context: dict,
    graph: dict | None = None,
    thresholds: dict | None = None,
    concept_a: str | None = None,
    concept_b: str | None = None,
    depth: int = 0,
    deadline: float | None = None,
    publisher: Any = None,
    llm_override: Any = None,
) -> dict:
    """对一条 GapCandidate 做价值二次验证，返回 GapVerdictResult {gap_id, verdict, evidence}。

    流程（铁律：LLM 攒证 / 确定性裁决）：
      1. 派 value-evidence subagent（最小授权 read_paper+search+submit_evidence_pack）攒反向检索证据。
      2. 组装 ReverseSearch（去重）。
      3. 确定性计量结构佐证（用传入的共现 graph 查两概念断层；concept 缺省取 gap 的 theme/statement 提示）。
      4. resolve_value_verdict 出 ValueVerdict（decided_by=deterministic）。
    fail-loud：subagent 非 ok（超时/越权/异常）→ 抛 ValueCheckError，绝不捏造 verdict。
    """
    gap_id = str(gap.get("gap_id") or "")
    statement = str(gap.get("statement") or "")
    theme = str(gap.get("theme") or "")

    task = (
        f"针对以下研究空白（GAP）做反向检索证伪，调 submit_evidence_pack 回传命中，绝不下结论：\n"
        f"主题: {theme}\nGAP 论断: {statement}\n"
        f"把论断转成检索式，查近年文献看「声称的空白」是否其实已有大量研究。"
    )
    result = await dispatch_to_skill(
        skill_id=VALUE_EVIDENCE_SKILL,
        task=task,
        registry=registry,
        llm_router=llm_router,
        base_context={**base_context, "gap": gap},
        depth=depth,
        deadline=deadline,
        publisher=publisher,
        llm_override=llm_override,
    )
    if result.outcome != OUTCOME_OK:
        raise ValueCheckError(
            f"value-evidence subagent 未正常完成（outcome={result.outcome}, "
            f"failures={result.tool_failures}: {result.tool_failure_reasons[:3]}）"
        )

    reverse_search = _build_reverse_search(result.data)

    # 概念：显式入参优先；否则尝试从 subagent 回传的 biblio_structure 线索取；最后回退 theme 切分。
    ca, cb = concept_a, concept_b
    if not (ca and cb):
        for pack in result.data or []:
            bs = (pack or {}).get("biblio_structure") or {}
            ca = ca or bs.get("concept_a")
            cb = cb or bs.get("concept_b")
    if not (ca and cb):
        parts = [p for p in statement.replace("，", ",").split() if p][:2] or [theme, theme]
        ca = ca or (parts[0] if parts else theme)
        cb = cb or (parts[-1] if len(parts) > 1 else theme)

    biblio_structure, _hole = structural_hole(str(ca), str(cb), graph)

    skipped: list[dict] = []
    for pack in result.data or []:
        for s in (pack or {}).get("skipped") or []:
            if isinstance(s, dict) and s.get("reason"):
                skipped.append({"reason": str(s["reason"])})

    evidence_pack = {
        "gap_id": gap_id,
        "reverse_search": reverse_search,
        "biblio_structure": biblio_structure,
        "gathered_by": "subagent",
        "skipped": skipped,
    }
    verdict = resolve_value_verdict(evidence_pack, thresholds)
    return {"gap_id": gap_id, "verdict": verdict, "evidence": evidence_pack}
