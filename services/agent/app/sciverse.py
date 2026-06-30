"""Sciverse API client and normalizers.

The client is deliberately small: it exposes metadata search, RAG chunk search,
and doc_id-based content loading without writing to the database.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import httpx

from .config import settings
from .errors import ApiError
from .net_safety import normalize_external_url


@dataclass(frozen=True)
class SciverseConfig:
    base_url: str
    api_token: str


def _strip_slash(url: str) -> str:
    return url.rstrip("/")


def sciverse_config(base_url: str | None = None, api_token: str | None = None) -> SciverseConfig:
    raw_base = _strip_slash((base_url or settings.sciverse_base_url or "").strip())
    resolved_token = (api_token if api_token is not None else settings.sciverse_api_token).strip()
    if resolved_token.lower().startswith("bearer "):
        resolved_token = resolved_token[7:].strip()
    if not raw_base:
        raise ApiError(400, "SCIVERSE_NOT_CONFIGURED", "Sciverse Base URL 未配置")
    if not resolved_token:
        raise ApiError(400, "SCIVERSE_NOT_CONFIGURED", "Sciverse API Token 未配置")
    try:
        resolved_base = normalize_external_url(raw_base)
    except ValueError as exc:
        raise ApiError(400, "SCIVERSE_BASE_URL_INVALID", "Sciverse Base URL 非法或指向非公网地址") from exc
    return SciverseConfig(base_url=resolved_base, api_token=resolved_token)


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _raise_sciverse(status: int, body: Any | None) -> None:
    if status < 400:
        return
    code = "SCIVERSE_ERROR"
    message = f"Sciverse 返回 HTTP {status}"
    if isinstance(body, dict):
        code = str(body.get("code") or body.get("error") or code)
        message = str(body.get("message") or body.get("error") or message)
    mapped_status = 502 if status >= 500 else status
    raise ApiError(mapped_status, code, message)


def _candidate_id(row: dict[str, Any]) -> str:
    for key in ("unique_id", "doc_id", "doi"):
        value = str(row.get(key) or "").strip()
        if value:
            prefix = "doi" if key == "doi" else "sciverse"
            return f"{prefix}:{hashlib.sha256(value.encode()).hexdigest()[:16]}"
    title = str(row.get("title") or "").strip()
    return "sciverse:" + hashlib.sha256(title.encode()).hexdigest()[:16]


def _author_display_name(v: Any) -> str:
    """从作者条目提取显示名。Sciverse 现返回 [{'orcid','name'},...]，旧版 str(v) 会把整个
    字典塞进作者名 → 引用/作者统计出现原始字典串乱码。dict 取 name/display_name/literal/
    family+given；str 原样返回。"""
    if isinstance(v, dict):
        for k in ("name", "display_name", "literal", "full_name"):
            val = v.get(k)
            if val and str(val).strip():
                return str(val).strip()
        fam = str(v.get("family") or v.get("lastName") or "").strip()
        giv = str(v.get("given") or v.get("firstName") or "").strip()
        return f"{giv} {fam}".strip() if (fam or giv) else ""
    return str(v).strip()


def _au_parts(name: str) -> tuple[str, list[str]]:
    """解析作者名 → (surname, given tokens)。surname 取最长 token：对 'LASTNAME FI'(姓在前)、
    'First Last'(姓在后)、'Last, First'(逗号) 都稳——姓通常比名/缩写长。"""
    n = name.strip()
    fam = n.split(",", 1)[0].strip() if "," in n else ""
    toks = [t for t in "".join(c if c.isalnum() else " " for c in n).lower().split() if t]
    if fam:
        fam = "".join(c for c in fam.lower() if c.isalnum())
    elif toks:
        fam = max(toks, key=len)
    given = [t for t in toks if t != fam]
    return fam, given


def _given_compatible(g1: list[str], g2: list[str]) -> bool:
    """given 是否同一人：短的一方每个 token 都能在长的一方找到 相等 或 缩写前缀 匹配。
    'john' vs 'jane' 不兼容(都全词、不等、非单字母) → 不误并同姓不同人(codex P1)。"""
    short, long_ = (g1, g2) if len(g1) <= len(g2) else (g2, g1)
    if not short:
        return True
    for s in short:
        if not any(
            s == l or (len(s) == 1 and l.startswith(s)) or (len(l) == 1 and s.startswith(l))
            for l in long_
        ):
            return False
    return True


def _dedup_authors(names: list[str]) -> list[str]:
    """去重同一作者的多写法(全名/缩写/姓在前)。Sciverse 常返回同人 2-4 种变体。保守：
    匹配既有组时检查"组的姓是否在本名 token 里"(等长平局也能正确归并, 如 'Ester Manik' ↔ 'Manik, Ester')，
    且 given 兼容才并(防误并同姓不同人, codex P1)；同组优先 'Last, First' 形式, 其次更长。"""
    out: list[str] = []
    gsur: list[str] = []
    ggiv: list[list[str]] = []
    for n in names:
        fam, given = _au_parts(n)
        toks = [t for t in "".join(c if c.isalnum() else " " for c in n).lower().split() if t]
        hit = -1
        for j in range(len(gsur)):
            if gsur[j] and gsur[j] in toks and _given_compatible([t for t in toks if t != gsur[j]], ggiv[j]):
                hit = j
                break
        if hit < 0:
            out.append(n)
            gsur.append(fam)
            ggiv.append(given)
        else:
            cur = out[hit]
            n_c, c_c = "," in n, "," in cur
            if (n_c and not c_c) or (n_c == c_c and len(n) > len(cur)):
                out[hit] = n
            if n_c and fam:  # 逗号形式 family 更可靠
                gsur[hit] = fam
                ggiv[hit] = given
    return out


def _authors(row: dict[str, Any]) -> list[str]:
    value = row.get("author") or row.get("authors") or []
    if isinstance(value, str):
        names = [value.strip()] if value.strip() else []
    elif isinstance(value, list):
        names = [s for s in (_author_display_name(v) for v in value) if s]
    else:
        names = []
    return _dedup_authors(names)


def _string_items(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.replace(",", ";").split(";")
        return [part.strip() for part in parts if part.strip()]
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("display_name") or item.get("name") or item.get("term") or item.get("value")
            else:
                text = item
            items.extend(_string_items(text))
        return items
    return [str(value).strip()] if str(value).strip() else []


def _keywords(row: dict[str, Any]) -> str | None:
    values: list[str] = []
    for key in ("keywords", "keyword", "author_keywords", "subjects", "subject"):
        values.extend(_string_items(row.get(key)))
    seen: set[str] = set()
    unique: list[str] = []
    for item in values:
        norm = item.casefold()
        if norm in seen:
            continue
        seen.add(norm)
        unique.append(item)
    return "; ".join(unique) if unique else None


def normalize_meta_result(row: dict[str, Any]) -> dict[str, Any]:
    """Map Sciverse meta-search row to the existing SearchCandidate shape."""
    title = str(row.get("title") or "").strip()
    doi = row.get("doi")
    doc_id = row.get("doc_id")
    unique_id = row.get("unique_id")
    venue = row.get("publication_venue_name_unified")
    year = row.get("publication_published_year")
    url = f"https://doi.org/{doi}" if doi else None
    external_ids: list[dict[str, Any]] = []
    if unique_id:
        external_ids.append({
            "provider": "sciverse",
            "id_type": "unique_id",
            "external_id": str(unique_id),
            "raw": row,
        })
    if doc_id:
        external_ids.append({
            "provider": "sciverse",
            "id_type": "doc_id",
            "external_id": str(doc_id),
            "raw": row,
        })
    if doi:
        external_ids.append({
            "provider": "doi",
            "id_type": "doi",
            "external_id": str(doi),
            "url": url,
            "raw": row,
        })

    return {
        "candidate_id": _candidate_id(row),
        "title": title,
        "doi": doi,
        "authors": _authors(row),
        "year": year,
        "abstract": row.get("abstract"),
        "keywords": _keywords(row),
        "containerTitle": venue,
        "url": url,
        "publicationDate": row.get("publication_published_date"),
        "citedByCount": row.get("citation_count"),
        "source": "sciverse",
        "provider": "sciverse",
        "sciverseDocId": doc_id,
        "sciverseUniqueId": unique_id,
        "externalIds": external_ids,
        "raw": row,
    }


def normalize_agentic_hit(row: dict[str, Any]) -> dict[str, Any]:
    doc_id = row.get("doc_id")
    return {
        "chunkId": row.get("chunk_id"),
        "chunk": row.get("chunk"),
        "docId": doc_id,
        "title": row.get("title"),
        "abstract": row.get("abstract"),
        "score": row.get("score"),
        "sourceType": row.get("source_type"),
        "offset": row.get("offset"),
        "pageNo": row.get("page_no"),
        "modelName": row.get("model_name"),
        "modelVersion": row.get("model_version"),
        "externalIds": [
            {
                "provider": "sciverse",
                "id_type": "doc_id",
                "external_id": str(doc_id),
                "raw": row,
            }
        ] if doc_id else [],
    }


class SciverseClient:
    def __init__(self, config: SciverseConfig, client: httpx.AsyncClient | None = None):
        self._cfg = config
        self._client = client

    async def _request(self, method: str, path: str, **kwargs) -> tuple[int, Any | None]:
        close_client = False
        client = self._client
        if client is None:
            timeout = httpx.Timeout(settings.sciverse_timeout, connect=10.0)
            client = httpx.AsyncClient(base_url=self._cfg.base_url, timeout=timeout)
            close_client = True
        try:
            resp = await client.request(method, path, headers=_headers(self._cfg.api_token), **kwargs)
            body = _safe_json(resp)
            _raise_sciverse(resp.status_code, body)
            return resp.status_code, body
        except httpx.HTTPError as exc:
            raise ApiError(503, "SCIVERSE_UNAVAILABLE", f"Sciverse 服务不可达: {exc}") from exc
        finally:
            if close_client:
                await client.aclose()

    async def meta_search(
        self,
        query: str | None = None,
        *,
        filters: list[dict] | None = None,
        sort: list[dict] | None = None,
        fields: list[str] | None = None,
        page: int = 1,
        page_size: int = 25,
        cursor: str | None = None,
        freshness_boost: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "page_size": max(1, min(100, int(page_size))),
        }
        if query:
            payload["query"] = query
        if filters:
            payload["filters"] = filters
        if sort:
            payload["sort"] = sort
        if fields:
            payload["fields"] = fields
        if cursor:
            payload["cursor"] = cursor
        else:
            payload["page"] = max(1, int(page))
        if freshness_boost:
            payload["freshness_boost"] = freshness_boost
        _, body = await self._request("POST", "/meta-search", json=payload)
        return body or {}

    async def agentic_search(self, query: str, top_k: int = 10, sub_queries: int = 0) -> dict[str, Any]:
        payload = {
            "query": query,
            "top_k": max(1, min(100, int(top_k))),
            "sub_queries": max(0, int(sub_queries)),
        }
        _, body = await self._request("POST", "/agentic-search", json=payload)
        return body or {}

    async def content(self, doc_id: str, offset: int | None = None, limit: int | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"doc_id": doc_id}
        if offset is not None:
            params["offset"] = max(0, int(offset))
            params["limit"] = max(1, int(limit or settings.sciverse_content_chunk_chars))
        _, body = await self._request("GET", "/content", params=params)
        return body or {}


def _safe_json(resp: httpx.Response) -> Any | None:
    try:
        return resp.json()
    except Exception:
        return None
