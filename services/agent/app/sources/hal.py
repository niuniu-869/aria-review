"""HAL (archives-ouvertes.fr) 检索源 (net-room)。无鉴权，Solr API。

待生产复测 (§3)：我方沙箱到法国服务器超时；生产机 (101.33.32.162) 复测连通性。
自带全文 PDF (fileMain_s)。
"""
from __future__ import annotations

import logging

from ..sciverse import normalize_meta_result
from .base import HttpSource, SourceOutcome

logger = logging.getLogger("agent.sources.hal")

_SEARCH_URL = "https://api.archives-ouvertes.fr/search/"
_FL = "docid,doiId_s,title_s,abstract_s,authFullName_s,producedDateY_i,journalTitle_s,fileMain_s,uri_s"


def _first(value):
    if isinstance(value, list) and value:
        return value[0]
    return value or None


def map_doc(doc: dict) -> dict:
    authors = doc.get("authFullName_s") or []
    if isinstance(authors, str):
        authors = [authors]
    doi = (_first(doc.get("doiId_s")) or "").strip() or None
    return {
        "title": _first(doc.get("title_s")),
        "doi": doi,
        "abstract": _first(doc.get("abstract_s")),
        "author": [a for a in authors if a],
        "publication_published_year": doc.get("producedDateY_i"),
        "publication_venue_name_unified": _first(doc.get("journalTitle_s")),
        "source_id": doc.get("docid"),
        "source_id_type": "hal_docid",
        "url": doc.get("uri_s"),
        "pdf_url": doc.get("fileMain_s"),
    }


class HalSource(HttpSource):
    source = "hal"

    async def search(self, query: str, *, limit: int, since: str | None = None) -> SourceOutcome:
        q = query
        if since:
            year = str(since)[:4]
            if year.isdigit():
                q = f"({query}) AND producedDateY_i:[{year} TO 2100]"
        params = {
            "q": q,
            "rows": str(max(1, min(100, limit))),
            "fl": _FL,
            "wt": "json",
        }
        try:
            status, body = await self._get_json(_SEARCH_URL, params=params)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[hal] 请求异常 (沙箱常超时，生产复测): %s", exc)
            return SourceOutcome(self.source, available=True, error=str(exc))
        if status >= 400 or not isinstance(body, dict):
            return SourceOutcome(self.source, available=True, error=f"HAL HTTP {status}")
        docs = ((body.get("response") or {}).get("docs")) or []
        candidates = [
            normalize_meta_result(map_doc(d), self.source)
            for d in docs
            if isinstance(d, dict) and _first(d.get("title_s"))
        ]
        total = (body.get("response") or {}).get("numFound")
        return SourceOutcome(self.source, available=True, candidates=candidates, total=total)
