"""Crossref 检索源 (net-room)。免鉴权 (配 mailto 进 polite pool)。

实测：Crossref 元数据全但**摘要普遍缺失** (通病)、PDF 多 403/landing → 定位为
**metadata-only** (§4.5)：不产 pdf_url、不参与 PDF 探测。用于补 DOI/题录广度。
"""
from __future__ import annotations

import logging
import re

from ..config import settings
from ..sciverse import normalize_meta_result
from .base import HttpSource, SourceOutcome

logger = logging.getLogger("agent.sources.crossref")

_WORKS_URL = "https://api.crossref.org/works"
_JATS_TAG = re.compile(r"<[^>]+>")


def _clean_abstract(value) -> str | None:
    if not value:
        return None
    text = _JATS_TAG.sub("", str(value)).strip()  # Crossref abstract 是 JATS XML
    return text or None


def _year_from_issued(item: dict):
    for key in ("published", "issued", "published-online", "published-print"):
        parts = ((item.get(key) or {}).get("date-parts")) or []
        if parts and isinstance(parts, list) and parts[0]:
            return parts[0][0]
    return None


def _first(value):
    if isinstance(value, list) and value:
        return value[0]
    return value or None


def map_item(item: dict) -> dict:
    authors = []
    for a in item.get("author") or []:
        if not isinstance(a, dict):
            continue
        name = " ".join(x for x in (a.get("given"), a.get("family")) if x).strip()
        if name:
            authors.append(name)
    doi = (item.get("DOI") or "").strip() or None
    return {
        "title": _first(item.get("title")),
        "doi": doi,
        "abstract": _clean_abstract(item.get("abstract")),
        "author": authors,
        "publication_published_year": _year_from_issued(item),
        "publication_venue_name_unified": _first(item.get("container-title")),
        "citation_count": item.get("is-referenced-by-count"),
        "source_id": doi,
        "source_id_type": "doi",
        "url": f"https://doi.org/{doi}" if doi else item.get("URL"),
        # metadata-only：不设 pdf_url，Crossref 不参与 PDF 探测。
    }


class CrossrefSource(HttpSource):
    source = "crossref"

    async def search(self, query: str, *, limit: int, since: str | None = None) -> SourceOutcome:
        params = {"query": query, "rows": str(max(1, min(100, limit)))}
        if since:
            params["filter"] = f"from-pub-date:{since}"
        mailto = (settings.crossref_mailto or settings.openalex_mailto or "").strip()
        if mailto:
            params["mailto"] = mailto
        try:
            status, body = await self._get_json(_WORKS_URL, params=params)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[crossref] 请求异常: %s", exc)
            return SourceOutcome(self.source, available=True, error=str(exc))
        if status >= 400 or not isinstance(body, dict):
            return SourceOutcome(self.source, available=True, error=f"Crossref HTTP {status}")
        items = ((body.get("message") or {}).get("items")) or []
        candidates = [
            normalize_meta_result(map_item(it), self.source)
            for it in items
            if isinstance(it, dict) and _first(it.get("title"))
        ]
        total = (body.get("message") or {}).get("total-results")
        return SourceOutcome(self.source, available=True, candidates=candidates, total=total)
