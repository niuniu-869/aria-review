"""多源检索基础设施：provider 枚举、结果容器、共享 HTTP 帮手、通用归一辅助。"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import settings

logger = logging.getLogger("agent.sources")

# provider 命名固定枚举 (codex P2)。Paper.source / PaperExternalId.provider 均 String(40)，
# 这些名字都 < 40 字符。"doi"/"upload" 为既有非检索 provider，一并纳入防漂移。
PROVIDERS = frozenset({
    "sciverse",
    "openalex",
    "core",
    "europepmc",
    "crossref",
    "semantic",
    "hal",
    "base",
    "unpaywall",
    "doi",
    "upload",
})

# 多源检索源 (可被 multi_source_search fan-out 的源；unpaywall 是补链不在此)。
# "base" 虽在 PROVIDERS 预留 (DB provider 枚举/未来接入)，但本期不 fan-out：BASE 需 IP
# 白名单 (§3 实测 403)，设计明确"方案不依赖 BASE，配到即启用"(§10)。故不在此清单，
# available_sources() 不会误报可用，search_source("base") 优雅返回未知源。
SEARCH_SOURCES = ("openalex", "core", "europepmc", "crossref", "semantic", "hal")


@dataclass
class SourceOutcome:
    """单个源一次检索的结果封装。

    available=False 表示该源"未配置/不可用"——聚合层与 Agent/前端据此显式提示，
    不与"检索到 0 篇"混淆 (codex P1：缺 key 不能静默 return [])。
    """

    source: str
    available: bool
    candidates: list[dict] = field(default_factory=list)
    total: int | None = None
    error: str | None = None
    unconfigured_reason: str | None = None

    @property
    def count(self) -> int:
        return len(self.candidates)


def reconstruct_abstract(inverted_index: Any) -> str | None:
    """从 OpenAlex abstract_inverted_index 还原摘要文本。

    OpenAlex 出于版权把摘要存成 {token: [positions...]} 倒排索引；按位置重排即得原文。
    非法/空输入返回 None。
    """
    if not isinstance(inverted_index, dict) or not inverted_index:
        return None
    positioned: list[tuple[int, str]] = []
    for token, positions in inverted_index.items():
        if not isinstance(positions, list):
            continue
        for pos in positions:
            if isinstance(pos, int):
                positioned.append((pos, str(token)))
    if not positioned:
        return None
    positioned.sort(key=lambda x: x[0])
    text = " ".join(tok for _, tok in positioned).strip()
    return text or None


class HttpSource:
    """带超时/重试的共享 HTTP 基座。子类实现 configured()/search()。

    传入 client 便于测试注入 httpx.MockTransport；缺省时每次请求自建短生命周期 client。
    """

    source: str = ""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    def configured(self) -> tuple[bool, str | None]:
        """(是否可用, 未配置原因)。默认永远可用 (免鉴权源)。"""
        return True, None

    # 上游临时性 HTTP 错误：有限重试 (429 限流 + 5xx 网关抖动，如 Semantic 429)。
    _RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

    async def _get_json(
        self,
        url: str,
        *,
        method: str = "GET",
        params: dict | None = None,
        json_body: dict | None = None,
        headers: dict | None = None,
    ) -> tuple[int, Any]:
        """发请求，返回 (status_code, parsed_json | None)。

        对网络异常 (httpx.HTTPError) 与临时性 HTTP 状态 (429/5xx) 都做有限重试
        (MULTISOURCE_MAX_RETRIES + 退避)；重试耗尽后返回末次响应 / 抛末次异常。
        """
        timeout = httpx.Timeout(settings.multisource_timeout, connect=10.0)
        retries = max(0, int(settings.multisource_max_retries))
        last_result: tuple[int, Any] | None = None
        for attempt in range(retries + 1):
            client = self._client
            close = False
            if client is None:
                client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
                close = True
            try:
                resp = await client.request(
                    method, url, params=params, json=json_body, headers=headers,
                )
                try:
                    body = resp.json()
                except Exception:
                    body = None
                # 临时性状态且还有重试额度 → 退避后重试，否则返回该响应。
                if resp.status_code in self._RETRYABLE_STATUS and attempt < retries:
                    last_result = (resp.status_code, body)
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                return resp.status_code, body
            except httpx.HTTPError:
                if attempt < retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise
            finally:
                if close:
                    await client.aclose()
        # 重试耗尽仍是临时状态：返回末次响应 (调用方按 status>=400 走 error 分支)。
        if last_result is not None:
            return last_result
        raise RuntimeError("unreachable")

    async def search(self, query: str, *, limit: int, since: str | None = None) -> SourceOutcome:
        raise NotImplementedError
