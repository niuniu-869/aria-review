"""EuropePMC 检索源 (net-room)。无鉴权。

关键 (§4.2 point 2)：**必须 resultType=core**，否则默认 lite 只回标题/无摘要/无 fulltextUrls
(实测证实的朋友漏配)。core 下摘要、期刊、PDF 直链齐全。
"""
from __future__ import annotations

import logging

from ..sciverse import normalize_meta_result
from .base import HttpSource, SourceOutcome

logger = logging.getLogger("agent.sources.europepmc")

_SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def _pdf_url(row: dict) -> str | None:
    urls = ((row.get("fullTextUrlList") or {}).get("fullTextUrl")) or []
    if not isinstance(urls, list):
        return None
    # 优先 documentStyle=pdf 且 availability 开放的直链。
    for u in urls:
        if isinstance(u, dict) and str(u.get("documentStyle") or "").lower() == "pdf" and u.get("url"):
            return u["url"]
    return None


def map_result(row: dict) -> dict:
    authors = [
        a.get("fullName")
        for a in ((row.get("authorList") or {}).get("author") or [])
        if isinstance(a, dict) and a.get("fullName")
    ]
    venue = ((row.get("journalInfo") or {}).get("journal") or {}).get("title")
    keywords = (row.get("keywordList") or {}).get("keyword") or None
    doi = (row.get("doi") or "").strip() or None
    return {
        "title": row.get("title"),
        "doi": doi,
        "abstract": row.get("abstractText"),
        "author": authors,
        "keywords": keywords,
        "publication_published_year": row.get("pubYear"),
        "publication_published_date": row.get("firstPublicationDate"),
        "publication_venue_name_unified": venue,
        "citation_count": row.get("citedByCount"),
        "source_id": row.get("id") or row.get("pmid"),
        "source_id_type": "pmid" if row.get("source") == "MED" else "epmc_id",
        "url": f"https://europepmc.org/article/{row.get('source')}/{row.get('id')}" if row.get("id") else None,
        "pdf_url": _pdf_url(row),
    }


class EuropePmcSource(HttpSource):
    source = "europepmc"

    async def search(self, query: str, *, limit: int, since: str | None = None) -> SourceOutcome:
        q = query
        if since:
            year = str(since)[:4]
            if year.isdigit():
                q = f"({query}) AND PUB_YEAR:[{year} TO 2100]"
        params = {
            "query": q,
            "resultType": "core",  # 硬前置：否则摘要=0、无 PDF
            "format": "json",
            "pageSize": str(max(1, min(100, limit))),
        }
        try:
            status, body = await self._get_json(_SEARCH_URL, params=params)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[europepmc] 请求异常: %s", exc)
            return SourceOutcome(self.source, available=True, error=str(exc))
        if status >= 400 or not isinstance(body, dict):
            return SourceOutcome(self.source, available=True, error=f"EuropePMC HTTP {status}")
        results = ((body.get("resultList") or {}).get("result")) or []
        candidates = [
            normalize_meta_result(map_result(r), self.source)
            for r in results
            if isinstance(r, dict) and (r.get("title") or "").strip()
        ]
        return SourceOutcome(
            self.source, available=True, candidates=candidates, total=body.get("hitCount"),
        )
