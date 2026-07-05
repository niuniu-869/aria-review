"""多源学术检索适配层 (Frontier Review 集成，见仓库根 NOTICE)。

净室实现：依据各上游源的公开 API 文档 + 实测行为，把 CORE / OpenAlex(直连) /
EuropePMC / Crossref / Semantic Scholar / HAL 归一成与 Sciverse 一致的 SearchCandidate
形状 (经 sciverse.normalize_meta_result(row, source=...))，供聚合层 (M2) fan-out。
Unpaywall 为 DOI 懒加载补 OA PDF，非独立检索源。

信任闸门 (安全下载→MinerU→cite_check→溯源) 原地不动，对所有多源候选一视同仁。
"""
from __future__ import annotations

from .aggregator import AggregateResult, merge_candidates, multi_source_search, prefilter
from .base import PROVIDERS, SourceOutcome
from .registry import available_sources, build_source_clients, search_source

__all__ = [
    "PROVIDERS",
    "SourceOutcome",
    "AggregateResult",
    "available_sources",
    "build_source_clients",
    "search_source",
    "multi_source_search",
    "merge_candidates",
    "prefilter",
]
