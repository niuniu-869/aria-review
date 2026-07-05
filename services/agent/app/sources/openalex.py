"""OpenAlex 直连检索源 (net-room)。

与现有"经 R 的 OpenAlex 路径"并存、互不影响：本模块供多源引擎并发 fan-out，
直连 https://api.openalex.org/works，不依赖 R 服务。免鉴权，配 mailto 进 polite pool。
"""
from __future__ import annotations

import logging

from ..config import settings
from ..sciverse import normalize_meta_result
from .base import HttpSource, SourceOutcome, reconstruct_abstract

logger = logging.getLogger("agent.sources.openalex")

_WORKS_URL = "https://api.openalex.org/works"


def _bare_doi(doi_url: str | None) -> str | None:
    """OpenAlex 的 doi 是完整 URL (https://doi.org/10.x)，剥成裸 DOI。"""
    if not doi_url:
        return None
    doi = str(doi_url).strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi.org/"):
        if doi.lower().startswith(prefix):
            return doi[len(prefix):]
    return doi or None


def _work_id(work_url: str | None) -> str | None:
    if not work_url:
        return None
    wid = str(work_url).strip()
    return wid.rsplit("/", 1)[-1] if wid.startswith("http") else wid


def map_work(work: dict) -> dict:
    """OpenAlex work → Sciverse-convention row (供 normalize_meta_result(row, "openalex"))。"""
    authorships = work.get("authorships") or []
    authors = [
        a.get("author", {}).get("display_name")
        for a in authorships
        if isinstance(a, dict) and a.get("author")
    ]
    primary = work.get("primary_location") or {}
    venue = (primary.get("source") or {}).get("display_name")
    best_oa = work.get("best_oa_location") or {}
    open_access = work.get("open_access") or {}
    # 只认真正的 PDF 直链 (codex P2)：open_access.oa_url 常是 OA 落地页非 PDF，
    # 当 pdf_url 会让 M2 resolve_pdf 把 HTML 当 PDF 探测、浪费额度 + 假"有PDF"信号。
    pdf_url = best_oa.get("pdf_url") or primary.get("pdf_url")
    keywords = [
        k.get("display_name")
        for k in (work.get("keywords") or [])
        if isinstance(k, dict) and k.get("display_name")
    ]
    return {
        "title": work.get("title") or work.get("display_name"),
        "doi": _bare_doi(work.get("doi")),
        "abstract": reconstruct_abstract(work.get("abstract_inverted_index")),
        "author": [a for a in authors if a],
        "keywords": keywords or None,
        "publication_published_year": work.get("publication_year"),
        "publication_published_date": work.get("publication_date"),
        "publication_venue_name_unified": venue,
        "citation_count": work.get("cited_by_count"),
        "source_id": _work_id(work.get("id")),
        "source_id_type": "work_id",
        "url": primary.get("landing_page_url") or work.get("id"),
        "pdf_url": pdf_url,
        "oa_status": open_access.get("oa_status"),
        "referenced_works": work.get("referenced_works") or [],
    }


class OpenAlexSource(HttpSource):
    source = "openalex"

    async def search(self, query: str, *, limit: int, since: str | None = None) -> SourceOutcome:
        params: dict[str, str] = {
            "search": query,
            "per-page": str(max(1, min(200, limit))),
        }
        if since:
            params["filter"] = f"from_publication_date:{since}"
        mailto = (settings.openalex_mailto or "").strip()
        if mailto:
            params["mailto"] = mailto
        try:
            status, body = await self._get_json(_WORKS_URL, params=params)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[openalex] 请求异常: %s", exc)
            return SourceOutcome(self.source, available=True, error=str(exc))
        if status >= 400 or not isinstance(body, dict):
            return SourceOutcome(self.source, available=True, error=f"OpenAlex HTTP {status}")
        results = body.get("results") or []
        candidates = [
            normalize_meta_result(map_work(w), self.source)
            for w in results
            if isinstance(w, dict) and (w.get("title") or w.get("display_name"))
        ]
        total = (body.get("meta") or {}).get("count")
        return SourceOutcome(self.source, available=True, candidates=candidates, total=total)
