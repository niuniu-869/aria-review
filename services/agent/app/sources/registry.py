"""多源注册表：构建源 client、可用性检测、单源检索入口。

可用源清单对 Agent/前端可见 (§4.7)：缺 key 的源显式标 configured=False + reason，
不静默 return [] —— 否则线上会误判"没文献"。
"""
from __future__ import annotations

from .base import SEARCH_SOURCES, SourceOutcome, HttpSource
from .core import CoreSource
from .crossref import CrossrefSource
from .europepmc import EuropePmcSource
from .hal import HalSource
from .openalex import OpenAlexSource
from .semantic_scholar import SemanticScholarSource
from .unpaywall import UnpaywallClient

# source 名 → client 类。unpaywall 是补链不在检索清单内。
_SOURCE_CLASSES: dict[str, type[HttpSource]] = {
    "openalex": OpenAlexSource,
    "core": CoreSource,
    "europepmc": EuropePmcSource,
    "crossref": CrossrefSource,
    "semantic": SemanticScholarSource,
    "hal": HalSource,
}

# 源可用性 (即插即用 / 需配置 / 随缘) —— 供前端分组展示，与实测分级对齐 (§3)。
_TIER: dict[str, str] = {
    "openalex": "ready",
    "europepmc": "ready",
    "crossref": "ready",
    "core": "ready",       # key 已配则即插即用
    "semantic": "flaky",   # 无 key 易 429
    "hal": "flaky",        # 沙箱超时，生产复测
}


def build_source_clients() -> dict[str, HttpSource]:
    """实例化全部检索源 client。"""
    return {name: cls() for name, cls in _SOURCE_CLASSES.items()}


def available_sources() -> list[dict]:
    """返回可用源清单：[{source, role, configured, reason, tier}]。

    role="search" 为检索源；role="enrichment" 为补链源 (Unpaywall)。二者都暴露
    configured/reason，下游 (Agent 选源、前端灰显、区分"无 OA PDF" vs "Unpaywall 未配置")
    据此显式提示，绝不静默 (§4.7, codex P2)。
    """
    out: list[dict] = []
    for name in SEARCH_SOURCES:
        cls = _SOURCE_CLASSES.get(name)
        if cls is None:
            continue
        configured, reason = cls().configured()
        out.append({
            "source": name,
            "role": "search",
            "configured": configured,
            "reason": reason,
            "tier": _TIER.get(name, "ready"),
        })
    # 补链源：Unpaywall 非独立检索源，但其配置状态同样对外可见。
    uw_configured, uw_reason = UnpaywallClient().configured()
    out.append({
        "source": "unpaywall",
        "role": "enrichment",
        "configured": uw_configured,
        "reason": uw_reason,
        "tier": "ready",
    })
    return out


async def search_source(
    name: str, query: str, *, limit: int, since: str | None = None,
) -> SourceOutcome:
    """检索单个源。未知源名返回 available=False。"""
    cls = _SOURCE_CLASSES.get(name)
    if cls is None:
        return SourceOutcome(name, available=False, unconfigured_reason=f"未知检索源: {name}")
    return await cls().search(query, limit=limit, since=since)
