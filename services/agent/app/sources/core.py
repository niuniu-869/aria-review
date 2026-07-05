"""CORE (core.ac.uk) v3 检索源 (net-room)。需 Bearer key；缺 key 显式"未配置"。"""
from __future__ import annotations

import logging

from ..config import settings
from ..sciverse import normalize_meta_result
from .base import HttpSource, SourceOutcome

logger = logging.getLogger("agent.sources.core")

_SEARCH_URL = "https://api.core.ac.uk/v3/search/works"


def _venue(row: dict) -> str | None:
    """取期刊名。修复朋友原实现 `publisher or journals` 在 journals=[] 时落成 '[]' 的 bug
    (§4.2 point 3)：只取 journals 中首个非空 title，否则回退 publisher，绝不 str(空列表)。
    """
    journals = row.get("journals")
    if isinstance(journals, list):
        for j in journals:
            if isinstance(j, dict):
                title = (j.get("title") or "").strip()
                if title:
                    return title
    publisher = (row.get("publisher") or "").strip() if row.get("publisher") else ""
    return publisher or None


def map_work(row: dict) -> dict:
    authors = [
        a.get("name") if isinstance(a, dict) else a
        for a in (row.get("authors") or [])
    ]
    return {
        "title": row.get("title"),
        "doi": (row.get("doi") or "").strip() or None,
        "abstract": row.get("abstract"),
        "author": [a for a in authors if a and str(a).strip()],
        "publication_published_year": row.get("yearPublished"),
        "publication_published_date": row.get("publishedDate"),
        "publication_venue_name_unified": _venue(row),
        "citation_count": row.get("citationCount"),
        "source_id": row.get("id"),
        "source_id_type": "core_id",
        # 只用字符串直链 downloadUrl；sourceFulltextUrls 可能是列表，回退会污染 url。
        "url": row.get("downloadUrl"),
        "pdf_url": row.get("downloadUrl"),
    }


class CoreSource(HttpSource):
    source = "core"

    def configured(self) -> tuple[bool, str | None]:
        if not (settings.core_api_key or "").strip():
            return False, "未配置 CORE_API_KEY (CORE 需 Bearer key)"
        return True, None

    async def search(self, query: str, *, limit: int, since: str | None = None) -> SourceOutcome:
        ok, reason = self.configured()
        if not ok:
            return SourceOutcome(self.source, available=False, unconfigured_reason=reason)
        # CORE v3 年份下限：`... AND yearPublished>=YYYY`（实测：加括号或 [TO] 区间会 0/500）。
        q = query
        if since:
            year = str(since)[:4]
            if year.isdigit():
                q = f"{query} AND yearPublished>={year}"
        headers = {"Authorization": f"Bearer {settings.core_api_key.strip()}"}
        body_req = {"q": q, "limit": max(1, min(100, limit)), "offset": 0}
        try:
            status, body = await self._get_json(
                _SEARCH_URL, method="POST", json_body=body_req, headers=headers,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[core] 请求异常: %s", exc)
            return SourceOutcome(self.source, available=True, error=str(exc))
        if status >= 400 or not isinstance(body, dict):
            return SourceOutcome(self.source, available=True, error=f"CORE HTTP {status}")
        results = body.get("results") or []
        candidates = [
            normalize_meta_result(map_work(r), self.source)
            for r in results
            if isinstance(r, dict) and (r.get("title") or "").strip()
        ]
        return SourceOutcome(
            self.source, available=True, candidates=candidates, total=body.get("totalHits"),
        )
