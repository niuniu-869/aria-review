"""r-analysis (plumber) 的 httpx 客户端。

约定: 除 health() 外, 方法返回 (status_code, json_body)。
连接失败统一抛 ApiError(503, R_SERVICE_UNAVAILABLE); R 端 5xx (非业务 502) 归一为 502。
共享一个 AsyncClient (连接池, Codex step3-P2) — 由 app lifespan 注入。
"""
from __future__ import annotations
import httpx

from .config import settings
from .errors import ApiError


class RClient:
    def __init__(self, client: httpx.AsyncClient):
        self._c = client  # base_url 指向 r-analysis, 由 lifespan 配置

    async def health(self) -> bool:
        try:
            r = await self._c.get("/healthz", timeout=settings.health_timeout)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def parse(self, content: bytes, filename: str, dbsource: str) -> tuple[int, dict | None]:
        try:
            r = await self._c.post(
                "/parse",
                params={"dbsource": dbsource},
                files={"file": (filename, content)},
                data={"dbsource": dbsource},
            )
        except httpx.HTTPError:
            raise ApiError(503, "R_SERVICE_UNAVAILABLE", "R 分析服务不可达")
        if r.status_code >= 500:
            raise ApiError(502, "ANALYSIS_FAILED", "R 解析返回坏响应")
        return r.status_code, _safe_json(r)

    async def search_openalex(self, query: str, n: int,
                              since: str) -> tuple[int, dict | None]:
        """调 R 端 POST /search/openalex，只检索不建库，返回规范化候选列表。"""
        return await self._post(
            "/search/openalex",
            {"query": query, "n": n, "since": since},
        )

    async def from_topic(self, query: str, n: int, since: str,
                         with_refs: bool) -> tuple[int, dict | None]:
        return await self._post(
            "/corpus/from-topic",
            {"query": query, "n": n, "since": since, "withRefs": with_refs})

    async def from_refs(self, papers: list[dict],
                        with_refs: bool) -> tuple[int, dict | None]:
        return await self._post(
            "/corpus/from-refs", {"papers": papers, "withRefs": with_refs})

    async def parse_from_records(self, records: list[dict]) -> tuple[int, dict | None]:
        """直接从结构化题录构建语料（路径 C: Postgres included papers → bibliometrix，保真不绕 OpenAlex）。"""
        return await self._post("/parse-from-records", {"records": records})

    async def get_corpus(self, corpus_id: str) -> tuple[int, dict | None]:
        return await self._get(f"/corpus/{corpus_id}")

    async def get_overview(self, corpus_id: str) -> tuple[int, dict | None]:
        return await self._get(f"/corpus/{corpus_id}/overview")

    async def get_records(self, corpus_id: str, limit: int = 40) -> tuple[int, dict | None]:
        return await self._get(f"/corpus/{corpus_id}/records?limit={int(limit)}")

    async def get_sources(self, corpus_id: str) -> tuple[int, dict | None]:
        return await self._get(f"/corpus/{corpus_id}/sources")

    async def get_authors(self, corpus_id: str) -> tuple[int, dict | None]:
        return await self._get(f"/corpus/{corpus_id}/authors")

    async def get_documents(self, corpus_id: str) -> tuple[int, dict | None]:
        return await self._get(f"/corpus/{corpus_id}/documents")

    # --- A4 高级图 (返回可用性信封) ---
    async def get_author_production(self, corpus_id: str) -> tuple[int, dict | None]:
        return await self._get(f"/corpus/{corpus_id}/authors/production")

    async def get_keyword_trend(self, corpus_id: str) -> tuple[int, dict | None]:
        return await self._get(f"/corpus/{corpus_id}/documents/keyword-trend")

    async def get_cited_refs(self, corpus_id: str) -> tuple[int, dict | None]:
        return await self._get(f"/corpus/{corpus_id}/documents/cited-refs")

    # --- A5 高级图② (返回可用性信封) ---
    async def get_thematic(self, corpus_id: str) -> tuple[int, dict | None]:
        return await self._get(f"/corpus/{corpus_id}/conceptual/thematic")

    async def get_evolution(self, corpus_id: str) -> tuple[int, dict | None]:
        return await self._get(f"/corpus/{corpus_id}/conceptual/evolution")

    async def get_histcite(self, corpus_id: str) -> tuple[int, dict | None]:
        return await self._get(f"/corpus/{corpus_id}/intellectual/histcite")

    async def get_threefield(self, corpus_id: str) -> tuple[int, dict | None]:
        return await self._get(f"/corpus/{corpus_id}/overview/threefield")

    # --- 网络端点 (A5 §4.4: limit 默认 top100, 上限 100; 前端滑块客户端切片) ---
    async def get_conceptual(self, corpus_id: str, limit: int = 100) -> tuple[int, dict | None]:
        return await self._get(f"/corpus/{corpus_id}/conceptual?limit={int(limit)}")

    async def get_intellectual(self, corpus_id: str, limit: int = 100) -> tuple[int, dict | None]:
        return await self._get(f"/corpus/{corpus_id}/intellectual?limit={int(limit)}")

    async def get_social(self, corpus_id: str, limit: int = 100) -> tuple[int, dict | None]:
        return await self._get(f"/corpus/{corpus_id}/social?limit={int(limit)}")

    async def get_cite(self, corpus_id: str, style: str = "apa",
                       limit: int = 200) -> tuple[int, dict | None]:
        return await self._get(f"/corpus/{corpus_id}/cite?style={style}&limit={int(limit)}")

    async def _get(self, path: str) -> tuple[int, dict | None]:
        try:
            r = await self._c.get(path)
        except httpx.HTTPError:
            raise ApiError(503, "R_SERVICE_UNAVAILABLE", "R 分析服务不可达")
        if r.status_code >= 500 and r.status_code != 502:
            return 502, {"code": "ANALYSIS_FAILED", "message": "R 返回坏响应"}
        return r.status_code, _safe_json(r)

    async def _post(self, path: str, payload: dict) -> tuple[int, dict | None]:
        # 接入路径需长超时: OpenAlex 检索 + 引用补全 50 篇可达数十秒
        try:
            r = await self._c.post(path, json=payload, timeout=settings.ingest_timeout)
        except httpx.HTTPError:
            raise ApiError(503, "R_SERVICE_UNAVAILABLE", "R 分析服务不可达")
        if r.status_code >= 500 and r.status_code != 502:
            return 502, {"code": "ANALYSIS_FAILED", "message": "R 返回坏响应"}
        return r.status_code, _safe_json(r)


def _safe_json(r: httpx.Response) -> dict | None:
    try:
        return r.json()
    except Exception:
        return None
