"""多源聚合层 (§4.1/§4.3/§4.4)。

职责：并发 fan-out 选定源 (只产候选，不写 DB) → 入库前跨源合并择优 → 确定性预过滤
+ 稳定排序 + 截断。**不做 ML/相关性/质量打分**——相关性判断交给 Agent + 人 (双级筛)；
本层只做防 token 爆的工程必需 (去重/年份/去空/配额/稳定排序)。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from ..config import settings
from ..repositories.library import _normalize_doi, _normalize_title_for_dedup
from .base import SEARCH_SOURCES, SourceOutcome
from .registry import _SOURCE_CLASSES, available_sources, search_source

logger = logging.getLogger("agent.sources.aggregator")

# 稳定排序的来源优先级 (非质量分，仅确定性 tiebreak；广度强/溯源全的源靠前)。
_SOURCE_PRIORITY = {
    "sciverse": 0, "openalex": 0, "europepmc": 1,
    "core": 2, "crossref": 3, "semantic": 4, "hal": 5,
}


@dataclass
class AggregateResult:
    candidates: list[dict] = field(default_factory=list)
    query: str = ""
    per_source: list[dict] = field(default_factory=list)  # [{source, available, count, error, reason}]
    total_before_merge: int = 0
    total_after_merge: int = 0
    truncated: int = 0

    @property
    def count(self) -> int:
        return len(self.candidates)


def resolve_sources(sources) -> tuple[list[str], list[dict]]:
    """解析请求源清单 → (要真正 fan-out 的源名, 未配置/未知源的 per_source 提示)。

    "auto"/None/[] → 全部**已配置**检索源；显式列表 → 已知且过滤未知源。
    """
    known = list(SEARCH_SOURCES)
    if not sources or sources == "auto" or sources == ["auto"]:
        selected = []
        skipped: list[dict] = []
        for row in available_sources():
            if row.get("role") != "search":
                continue
            if row["configured"]:
                selected.append(row["source"])
            else:
                skipped.append({
                    "source": row["source"], "available": False,
                    "count": 0, "error": None, "reason": row.get("reason"),
                })
        return selected, skipped
    # 显式列表：保留已知源；未知源显式标出。
    selected, skipped = [], []
    for name in sources:
        if name in known:
            selected.append(name)
        else:
            skipped.append({
                "source": name, "available": False, "count": 0,
                "error": None, "reason": f"未知检索源: {name}",
            })
    return selected, skipped


def _merge_key(cand: dict) -> str:
    """跨源合并键：normalized DOI 优先，其次 normalized title+**明确** year (§4.3)。

    codex P2：仅双方都有具体 year 才走 title+year 合并；缺 year/缺 title 退候选级唯一键
    (cid)，保守不合——防止把同标题、都缺年份的**不同**论文误合成一条。
    """
    doi = (cand.get("doi") or "").strip()
    if doi:
        return f"doi:{_normalize_doi(doi)}"
    title = _normalize_title_for_dedup(cand.get("title") or "")
    year = cand.get("year")
    if title and isinstance(year, int):
        return f"title:{title}:{year}"
    return f"cid:{cand.get('candidate_id')}"


def _longer(a, b) -> str | None:
    """择优取更长非空文本 (确定性：等长取 a)。"""
    a_s = (a or "").strip() if isinstance(a, str) else ""
    b_s = (b or "").strip() if isinstance(b, str) else ""
    if not a_s:
        return b_s or None
    if not b_s:
        return a_s or None
    return a_s if len(a_s) >= len(b_s) else b_s


def _max_num(a, b):
    nums = [x for x in (a, b) if isinstance(x, (int, float))]
    return max(nums) if nums else (a if a is not None else b)


def _union_external_ids(a, b) -> list[dict]:
    out: list[dict] = []
    seen: set[tuple] = set()
    for lst in (a or [], b or []):
        for item in lst:
            if not isinstance(item, dict):
                continue
            key = (item.get("provider"), item.get("id_type"), str(item.get("external_id")))
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
    return out


def _merge_two(base: dict, other: dict) -> dict:
    """跨源择优合并两条同键候选。base 的 candidate_id/source 保留 (导入匹配稳定)。"""
    m = dict(base)
    m["abstract"] = _longer(base.get("abstract"), other.get("abstract"))
    m["doi"] = base.get("doi") or other.get("doi")
    m["containerTitle"] = _longer(base.get("containerTitle"), other.get("containerTitle"))
    m["year"] = base.get("year") or other.get("year")
    m["url"] = base.get("url") or other.get("url")
    m["keywords"] = _longer(base.get("keywords"), other.get("keywords"))
    m["publicationDate"] = base.get("publicationDate") or other.get("publicationDate")
    if not m.get("pdfUrl") and other.get("pdfUrl"):
        m["pdfUrl"] = other["pdfUrl"]
    if not m.get("oaStatus") and other.get("oaStatus"):
        m["oaStatus"] = other["oaStatus"]
    if len(other.get("authors") or []) > len(base.get("authors") or []):
        m["authors"] = other["authors"]
    m["citedByCount"] = _max_num(base.get("citedByCount"), other.get("citedByCount"))
    m["externalIds"] = _union_external_ids(base.get("externalIds"), other.get("externalIds"))
    # 溯源：记录合并涉及的所有源 (如 ['core','openalex'])。
    srcs = set(base.get("mergedSources") or ([base["source"]] if base.get("source") else []))
    if other.get("source"):
        srcs.add(other["source"])
    m["mergedSources"] = sorted(s for s in srcs if s)
    return m


def merge_candidates(candidates: list[dict]) -> list[dict]:
    """入库前跨源合并 (§4.3)：同键择优，保留首现顺序。不写 DB。"""
    merged: dict[str, dict] = {}
    order: list[str] = []
    for cand in candidates:
        key = _merge_key(cand)
        if key in merged:
            merged[key] = _merge_two(merged[key], cand)
        else:
            merged[key] = dict(cand)
            order.append(key)
    return [merged[k] for k in order]


def _sort_key(cand: dict):
    """确定性稳定排序键 (非质量/相关性打分，§4.4)：年份倒序 → 有全文优先 → 来源优先级 → id。"""
    year = cand.get("year")
    year_rank = -int(year) if isinstance(year, int) else 1  # 有年份靠前(负值)，无年份垫底
    has_pdf = 0 if cand.get("pdfUrl") else 1
    src_names = cand.get("mergedSources") or ([cand["source"]] if cand.get("source") else [])
    src_rank = min((_SOURCE_PRIORITY.get(s, 9) for s in src_names), default=9)
    return (year_rank, has_pdf, src_rank, str(cand.get("candidate_id") or ""))


def prefilter(candidates: list[dict], *, since: str | None, total_cap: int) -> tuple[list[dict], int]:
    """确定性预过滤 + 稳定排序 + 截断。返回 (candidates, truncated_count)。"""
    year_floor = None
    if since:
        head = str(since)[:4]
        if head.isdigit():
            year_floor = int(head)
    kept: list[dict] = []
    for cand in candidates:
        if not (cand.get("title") or "").strip():
            continue
        year = cand.get("year")
        # 硬过滤：有明确年份且低于下限才丢；year=None 保留 (缺年份不等于不相关，保召回)。
        if year_floor and isinstance(year, int) and year < year_floor:
            continue
        kept.append(cand)
    kept.sort(key=_sort_key)
    truncated = max(0, len(kept) - total_cap)
    return kept[:total_cap], truncated


async def multi_source_search(
    sources,
    query: str,
    *,
    limit: int | None = None,
    since: str | None = None,
    total_cap: int | None = None,
) -> AggregateResult:
    """多源检索聚合入口：并发 fan-out → 合并 → 预过滤。只产候选，不写 DB。"""
    query = (query or "").strip()
    if not query:
        return AggregateResult(query=query)
    per_source_limit = int(limit or settings.multisource_per_source_limit)
    cap = int(total_cap or settings.multisource_total_cap)

    selected, per_source = resolve_sources(sources)
    if not selected:
        return AggregateResult(query=query, per_source=per_source)

    # return_exceptions=True (codex P2)：某源意外抛异常不应拖垮整批、丢掉其它源已成功
    # 的候选；转成该源的 available=False + error，其它源继续合并。
    raw_outcomes = await asyncio.gather(
        *(search_source(name, query, limit=per_source_limit, since=since) for name in selected),
        return_exceptions=True,
    )
    outcomes: list[SourceOutcome] = []
    for name, res in zip(selected, raw_outcomes):
        if isinstance(res, SourceOutcome):
            outcomes.append(res)
        else:
            logger.warning("[aggregator] 源 %s 检索异常: %s", name, res)
            outcomes.append(SourceOutcome(name, available=False, error=str(res)))

    flat: list[dict] = []
    for outcome in outcomes:
        per_source.append({
            "source": outcome.source,
            "available": outcome.available,
            "count": outcome.count,
            "error": outcome.error,
            "reason": outcome.unconfigured_reason,
        })
        if outcome.available:
            flat.extend(outcome.candidates)

    total_before = len(flat)
    merged = merge_candidates(flat)
    candidates, truncated = prefilter(merged, since=since, total_cap=cap)
    return AggregateResult(
        candidates=candidates,
        query=query,
        per_source=per_source,
        total_before_merge=total_before,
        total_after_merge=len(merged),
        truncated=truncated,
    )
