"""Semantic Scholar 检索源 (net-room)。随缘源：无 key 易被 429 限流。

有 SEMANTIC_SCHOLAR_API_KEY 走 x-api-key；无 key 也尝试 (可能 429，作为 error 回传，
不静默 return [])。openAccessPdf 提供 OA 直链。
"""
from __future__ import annotations

import logging

from ..config import settings
from ..sciverse import normalize_meta_result
from .base import HttpSource, SourceOutcome

logger = logging.getLogger("agent.sources.semantic")

_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
_FIELDS = "title,abstract,year,publicationDate,authors,externalIds,openAccessPdf,venue,citationCount"


def map_paper(row: dict) -> dict:
    authors = [
        a.get("name")
        for a in (row.get("authors") or [])
        if isinstance(a, dict) and a.get("name")
    ]
    external = row.get("externalIds") or {}
    doi = (external.get("DOI") or "").strip() or None
    pdf = (row.get("openAccessPdf") or {}).get("url")
    return {
        "title": row.get("title"),
        "doi": doi,
        "abstract": row.get("abstract"),
        "author": authors,
        "publication_published_year": row.get("year"),
        "publication_published_date": row.get("publicationDate"),
        "publication_venue_name_unified": row.get("venue"),
        "citation_count": row.get("citationCount"),
        "source_id": row.get("paperId"),
        "source_id_type": "s2_paper_id",
        "url": f"https://www.semanticscholar.org/paper/{row.get('paperId')}" if row.get("paperId") else None,
        "pdf_url": pdf,
    }


class SemanticScholarSource(HttpSource):
    source = "semantic"

    def configured(self) -> tuple[bool, str | None]:
        # 无 key 仍可尝试 (免鉴权但强限流)；标注为可用但提示随缘。
        return True, None

    async def search(self, query: str, *, limit: int, since: str | None = None) -> SourceOutcome:
        params = {"query": query, "limit": str(max(1, min(100, limit))), "fields": _FIELDS}
        if since:
            year = str(since)[:4]
            if year.isdigit():
                params["year"] = f"{year}-"
        headers = {}
        key = (settings.semantic_scholar_api_key or "").strip()
        if key:
            headers["x-api-key"] = key
        try:
            status, body = await self._get_json(_SEARCH_URL, params=params, headers=headers or None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[semantic] 请求异常: %s", exc)
            return SourceOutcome(self.source, available=True, error=str(exc))
        if status == 429:
            return SourceOutcome(self.source, available=True, error="Semantic Scholar 限流 (429)，建议配置 API key")
        if status >= 400 or not isinstance(body, dict):
            return SourceOutcome(self.source, available=True, error=f"Semantic Scholar HTTP {status}")
        results = body.get("data") or []
        candidates = [
            normalize_meta_result(map_paper(r), self.source)
            for r in results
            if isinstance(r, dict) and (r.get("title") or "").strip()
        ]
        return SourceOutcome(self.source, available=True, candidates=candidates, total=body.get("total"))
