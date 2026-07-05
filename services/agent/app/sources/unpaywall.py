"""Unpaywall DOI 懒加载补链 (net-room)。非独立检索源。

只走 `/v2/{doi}` (§4.6)：`/search` 端点实测 500 已弃用。仅对**入选、无 pdf_url、有 DOI**
的候选小批量补 OA PDF (M4)。命中率非满 (OA 现实)，不保证全覆盖。需 email (免鉴权)。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ..config import settings
from .base import HttpSource

logger = logging.getLogger("agent.sources.unpaywall")

_BASE = "https://api.unpaywall.org/v2/"


@dataclass
class UnpaywallHit:
    doi: str
    pdf_url: str | None
    landing_url: str | None
    oa_status: str | None


class UnpaywallClient(HttpSource):
    source = "unpaywall"

    def configured(self) -> tuple[bool, str | None]:
        if not (settings.unpaywall_email or "").strip():
            return False, "未配置 UNPAYWALL_EMAIL (Unpaywall /v2 需 email)"
        return True, None

    async def lookup(self, doi: str) -> UnpaywallHit | None:
        ok, _ = self.configured()
        doi = (doi or "").strip()
        if not ok or not doi:
            return None
        try:
            status, body = await self._get_json(
                _BASE + doi, params={"email": settings.unpaywall_email.strip()},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[unpaywall] 查询 %s 异常: %s", doi, exc)
            return None
        if status >= 400 or not isinstance(body, dict):
            return None
        best = body.get("best_oa_location") or {}
        return UnpaywallHit(
            doi=doi,
            pdf_url=best.get("url_for_pdf"),
            landing_url=best.get("url"),
            oa_status=body.get("oa_status"),
        )
