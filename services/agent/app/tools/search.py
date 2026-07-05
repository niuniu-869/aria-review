"""SearchTool — 文献检索工具（只读，调 /search/openalex）。

只读工具；不进 mark_write_tools，无写 DB 操作，无确认 gate。

execute 流程:
  1. 校验 query 非空。
  2. 调 r_client.search_openalex(query, limit, since)。
  3. R 失败/空 → 友好 _fail/_empty。
  4. 成功 → 每条候选补 candidate_id:
       - 优先 openalexId（原值，不含 URL 前缀）
       - 无则 "doi:" + sha256(doi)[:16]
       - 再无则 sha256(title)[:16]
  5. await emit({"type":"search_results","candidates":[...],"query":query}) if emit。
  6. ToolResult 成功，data 含候选列表 + 计数摘要，供 LLM 继续推理。

块事件:
  search_results — 候选卡列表（含 candidate_id + 所有规范化字段），供前端渲染候选卡。
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from ..sciverse import SciverseClient, normalize_meta_result, sciverse_config
from ..sources import available_sources, multi_source_search
from ..config import settings
from ..errors import ApiError
from ..harness.tools import BaseTool, ToolResult
from ..search_limits import SEARCH_LIMIT_ERROR_MESSAGE, SEARCH_LIMIT_MAX

logger = logging.getLogger("agent.tools.search")

_DEFAULT_LIMIT = 50
_DEFAULT_SINCE = "2016-01-01"


def _sciverse_configured() -> bool:
    """Sciverse 是否已配置 base_url + token（决定自动路由能否切到 sciverse）。"""
    from ..config import settings
    return bool((settings.sciverse_base_url or "").strip() and (settings.sciverse_api_token or "").strip())


def _auto_provider(query: str) -> str:
    """provider 未显式指定时的默认数据源：Sciverse 优先。

    Sciverse 已配置 → sciverse（默认数据源，中英文覆盖与语义检索均更好，且直连不经 R）；
    Sciverse 未配置 → openalex（配置感知 fallback，避免因缺 token 直接失败，codex P1）。
    """
    if _sciverse_configured():
        return "sciverse"
    return "openalex"

# 候选枚举：summary 需逐条暴露 candidate_id + 标题，LLM 才能逐条判相关性并按 ID 自筛导入
# （旧版只预览前 5 条标题且无 ID，导致 prompts.py 要求的「只传相关 candidate_ids」无法落地）。
_SUMMARY_TITLE_CAP = 90    # 单条标题截断长度（足够判主题相关性，又约束字符预算）
_SUMMARY_CARD_CAP = 150    # summary 最多枚举条数；检索上限独立由 SEARCH_LIMIT_MAX 约束


def _r_unavailable_message(exc: Exception) -> str:
    reason = str(exc).strip() or exc.__class__.__name__
    return (
        f"R 分析服务不可达（地址: {settings.r_analysis_url}；原因: {reason}）。"
        "OpenAlex 检索依赖 R 分析服务，请先运行 `docker compose --profile analysis up -d` "
        "启动 R 服务，并检查端口和网络连通性。"
    )


def _format_candidate_cards(candidates: list[dict]) -> tuple[str, int]:
    """把候选枚举成 `[candidate_id] 标题（年份）` 多行清单，供 LLM 逐条判相关性并按 ID 自筛导入。

    Returns:
        (cards_text, hidden_count)：cards_text 为多行清单；hidden_count 为超出 _SUMMARY_CARD_CAP
        未列出的条数（>0 时提示收紧检索式）。
    """
    lines: list[str] = []
    for c in candidates[:_SUMMARY_CARD_CAP]:
        cid = str(c.get("candidate_id") or "").strip() or "?"
        title = str(c.get("title") or "").strip() or "(无标题)"
        if len(title) > _SUMMARY_TITLE_CAP:
            title = title[:_SUMMARY_TITLE_CAP] + "…"
        year = c.get("year") or "n.d."
        lines.append(f"- [{cid}] {title}（{year}）")
    hidden = max(0, len(candidates) - _SUMMARY_CARD_CAP)
    return "\n".join(lines), hidden


def _build_search_summary(
    provider_label: str,
    total: int,
    query: str,
    candidates: list[dict],
    *,
    partial: bool = False,
    partial_reason: str | None = None,
) -> str:
    """检索成功摘要：先一句概览（供 UI 200 字预览），再逐条候选清单（供 LLM 自筛）。"""
    cards, hidden = _format_candidate_cards(candidates)
    partial_note = ""
    if partial:
        reason = f"：{partial_reason}" if partial_reason else ""
        partial_note = f"（注意：本次检索只返回部分结果{reason}）"
    more_note = (
        f"\n（还有 {hidden} 篇未列出，若噪声较多请收紧检索式后重检）" if hidden > 0 else ""
    )
    return (
        f"{provider_label}检索到 {total} 篇候选文献（关键词：{query}）。{partial_note}已生成候选卡。"
        f"下面逐条列出 candidate_id 与标题，请逐条判断是否真正切题，"
        f"仅用相关候选的 candidate_id 调用 project__import_search_results 导入：\n"
        f"{cards}{more_note}"
    )


def _candidate_id(cand: dict) -> str:
    """按优先级生成 candidate_id: openalexId → doi hash → title hash。"""
    oa_id = (cand.get("openalexId") or "").strip()
    if oa_id:
        # 防御 URL 形式（如 https://openalex.org/W123），剥取末段
        if oa_id.startswith("http"):
            oa_id = oa_id.rsplit("/", 1)[-1]
        return oa_id

    doi = (cand.get("doi") or "").strip()
    if doi:
        return "doi:" + hashlib.sha256(doi.encode()).hexdigest()[:16]

    title = (cand.get("title") or "").strip()
    return hashlib.sha256(title.encode()).hexdigest()[:16]


def _cache_key(item: dict) -> str:
    """候选缓存去重键 (与既有 topic/sciverse 分支一致)。"""
    return (
        item.get("openalexId")
        or item.get("sciverseDocId")
        or item.get("sciverseUniqueId")
        or item.get("doi")
        or item.get("candidate_id")
        or f"{item.get('title', '')}:{item.get('year', '')}"
    )


def _cache_candidates(ctx: dict | None, candidates: list[dict]) -> None:
    """把候选并入 ctx['search_candidates'] (去重)，供 project__import_search_results 导入。"""
    if not isinstance(ctx, dict):
        return
    cached = ctx.setdefault("search_candidates", [])
    seen = {_cache_key(item) for item in cached if isinstance(item, dict)}
    for item in candidates:
        key = _cache_key(item)
        if key not in seen:
            cached.append(item)
            seen.add(key)


def _parse_limit(value: Any) -> tuple[int | None, str | None]:
    """解析检索上限；超出契约时显式失败，不再静默截断。"""
    if value in (None, ""):
        return _DEFAULT_LIMIT, None
    if isinstance(value, bool):
        return None, f"limit 必须是 1-{SEARCH_LIMIT_MAX} 的整数"
    try:
        if isinstance(value, float) and not value.is_integer():
            return None, f"limit 必须是 1-{SEARCH_LIMIT_MAX} 的整数"
        limit = int(value)
    except (TypeError, ValueError):
        return None, f"limit 必须是 1-{SEARCH_LIMIT_MAX} 的整数"
    if limit < 1:
        return None, f"limit 必须是 1-{SEARCH_LIMIT_MAX} 的整数"
    if limit > SEARCH_LIMIT_MAX:
        return None, f"{SEARCH_LIMIT_ERROR_MESSAGE}，请调小 limit 后重试"
    return limit, None


class SearchTool(BaseTool):
    """按主题/关键词检索文献（调 R /search/openalex），只返回候选，不建库。"""

    tool_id = "search"
    tool_name = "Search Tool"
    description = (
        "按主题/关键词检索文献，返回候选卡列表（不建库）。"
        "topic: 单源检索（OpenAlex/Sciverse，默认 sciverse）；"
        "multi: 多源并发检索（OpenAlex/CORE/EuropePMC/Crossref/Semantic Scholar/HAL），"
        "跨源合并去重 + 确定性预过滤，广度更强、更易命中开放获取 PDF，主题综述建库首选；"
        "sources: 查询各数据源是否已配置可用。"
        "检索得到候选后，告知用户可在候选卡中选择加入文献库或纳入"
    )
    actions = ["topic", "multi", "sources"]
    tags = ["read"]  # 只读工具，不加 "write"

    action_schemas = {
        "topic": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "检索关键词/主题（必填）",
                },
                "limit": {
                    "type": "integer",
                    "description": f"返回候选数量上限，默认 50；最大 {SEARCH_LIMIT_MAX}，超出将显式报错",
                    "default": _DEFAULT_LIMIT,
                    "minimum": 1,
                    "maximum": SEARCH_LIMIT_MAX,
                },
                "since": {
                    "type": "string",
                    "description": (
                        "发表年份下限（YYYY-MM-DD），默认 2016-01-01；"
                        "检索近年文献时可调大（如 2020-01-01）"
                    ),
                },
                "provider": {
                    "type": "string",
                    "enum": ["openalex", "sciverse"],
                    "description": (
                        "检索数据源，默认 'sciverse'（中英文覆盖与语义检索均更好）；"
                        "省略则自动使用 sciverse（未配置时回退 'openalex'）。"
                        "如需强制走 OpenAlex（如需要 OpenAlex 特有的英文相关性排序），显式传 'openalex'。"
                    ),
                },
            },
            "required": ["query"],
        },
        "multi": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索关键词/主题（必填）"},
                "sources": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["openalex", "core", "europepmc", "crossref", "semantic", "hal"],
                    },
                    "description": (
                        "要并发检索的数据源；省略或传空则自动使用全部已配置源。"
                        "先用 sources action 查看哪些源可用。"
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "每个源返回候选上限，默认 50",
                    "minimum": 1,
                    "maximum": SEARCH_LIMIT_MAX,
                },
                "since": {
                    "type": "string",
                    "description": "发表年份下限（YYYY-MM-DD），默认 2016-01-01",
                },
            },
            "required": ["query"],
        },
        "sources": {
            "type": "object",
            "properties": {},
            "description": "查询各数据源配置/可用状态，无参数",
        },
    }

    def __init__(self, r_client: Any) -> None:
        """
        Args:
            r_client: RClient 实例（或 FakeR mock）。只调用 search_openalex 方法。
        """
        self._r = r_client

    # ------------------------------------------------------------------
    # 核心执行
    # ------------------------------------------------------------------

    async def _execute(
        self, action: str, params: dict[str, Any], context: Any = None,
    ) -> ToolResult:
        if action not in ("topic", "multi", "sources"):
            return self._fail(action, f"不支持的 action: {action}")

        ctx = context if isinstance(context, dict) else {}
        emit = ctx.get("emit")

        if action == "sources":
            return self._execute_sources()
        if action == "multi":
            return await self._execute_multi(params, emit, ctx)

        query = (params.get("query") or "").strip()
        if not query:
            return self._fail(action, "query 是必填字段，请提供检索关键词")

        limit, limit_error = _parse_limit(params.get("limit"))
        if limit_error:
            return self._fail(action, limit_error)
        assert limit is not None
        since = (params.get("since") or _DEFAULT_SINCE).strip() or _DEFAULT_SINCE
        # provider 路由：显式指定则尊重；省略则默认 sciverse（未配置 sciverse 时回退 openalex）。
        provider = (params.get("provider") or "").strip().lower() or _auto_provider(query)

        if provider == "sciverse":
            sciverse = ctx.get("sciverse") or {}
            return await self._execute_sciverse(action, query, limit, emit, sciverse, ctx)
        if provider != "openalex":
            return self._fail(action, f"不支持的 provider: {provider}")

        # 调 R 服务
        try:
            status_code, body = await self._r.search_openalex(query, limit, since)
        except Exception as exc:
            logger.exception("[SearchTool] search_openalex 调用异常")
            return self._fail(action, _r_unavailable_message(exc))

        # R 失败
        if status_code >= 400:
            b = body or {}
            # R 失败体可能是 {error, detail}（R 服务风格）或 {code, message}（agent 风格）
            code = b.get("code") or b.get("error") or "SEARCH_FAILED"
            msg = b.get("message") or b.get("error") or f"R 服务返回 {status_code}"
            # 兼容 R 的 detail 字段，截断防止泄露堆栈细节（max 200 chars）
            detail = b.get("detail")
            if detail:
                detail_str = str(detail)[:200]
                err_str = f"检索失败 [{code}]: {msg}（{detail_str}）"
            else:
                err_str = f"检索失败 [{code}]: {msg}"
            logger.warning("[SearchTool] R 返回 %d: %s", status_code, err_str)
            return self._fail(action, err_str)

        r_body = body or {}
        partial = bool(r_body.get("partial"))
        partial_reason = str(r_body.get("partialReason") or "").strip() or None
        raw_results: list[dict] = r_body.get("results", []) or []

        # 空结果 → 友好提示
        if not raw_results:
            if partial:
                if emit is not None:
                    try:
                        await emit({
                            "type": "search_results",
                            "candidates": [],
                            "query": query,
                            "partial": True,
                            "partialReason": partial_reason,
                        })
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("[SearchTool] emit partial search_results 失败（不影响结果）: %s", exc)
                summary = (
                    f"检索只返回部分结果，但暂未拿到候选文献（关键词：{query}）。"
                    f"原因：{partial_reason or '上游限流或超时'}。请稍后重试或收紧检索式。"
                )
                return self._ok(
                    action,
                    data=[{
                        "candidates": [],
                        "total": 0,
                        "query": query,
                        "partial": True,
                        "partialReason": partial_reason,
                    }],
                    source="api",
                    summary=summary,
                )
            return self._empty(
                action,
                f"未找到与\"{query}\"相关的文献（共 0 篇），建议调整关键词或放宽年份范围",
            )

        # 补 candidate_id
        candidates: list[dict] = []
        for cand in raw_results:
            c = dict(cand)
            c["candidate_id"] = _candidate_id(cand)
            candidates.append(c)

        if isinstance(ctx, dict):
            cached = ctx.setdefault("search_candidates", [])
            seen = {
                (
                    item.get("openalexId")
                    or item.get("sciverseDocId")
                    or item.get("sciverseUniqueId")
                    or item.get("doi")
                    or item.get("candidate_id")
                    or f"{item.get('title', '')}:{item.get('year', '')}"
                )
                for item in cached
                if isinstance(item, dict)
            }
            for item in candidates:
                key = (
                    item.get("openalexId")
                    or item.get("sciverseDocId")
                    or item.get("sciverseUniqueId")
                    or item.get("doi")
                    or item.get("candidate_id")
                    or f"{item.get('title', '')}:{item.get('year', '')}"
                )
                if key not in seen:
                    cached.append(item)
                    seen.add(key)

        # 块事件
        if emit is not None:
            try:
                await emit({
                    "type": "search_results",
                    "candidates": candidates,
                    "query": query,
                    "partial": partial,
                    "partialReason": partial_reason,
                })
            except Exception as exc:  # noqa: BLE001
                logger.warning("[SearchTool] emit search_results 失败（不影响结果）: %s", exc)

        total = len(candidates)
        summary = _build_search_summary(
            "",
            total,
            query,
            candidates,
            partial=partial,
            partial_reason=partial_reason,
        )

        return self._ok(
            action,
            data=[{
                "candidates": candidates,
                "total": total,
                "query": query,
                "partial": partial,
                "partialReason": partial_reason,
            }],
            source="api",
            summary=summary,
        )

    async def _execute_sciverse(
        self,
        action: str,
        query: str,
        limit: int,
        emit: Any,
        override: dict | None = None,
        ctx: dict | None = None,
    ) -> ToolResult:
        try:
            override = override or {}
            client = SciverseClient(sciverse_config(
                override.get("base_url"),
                override.get("api_token"),
            ))
            body = await client.meta_search(
                query=query,
                page_size=limit,
                fields=[
                    "title",
                    "doi",
                    "abstract",
                    "author",
                    "keywords",
                    "publication_published_year",
                    "publication_published_date",
                    "publication_venue_name_unified",
                    "citation_count",
                    "reference_count",
                    "doc_id",
                    "unique_id",
                ],
            )
        except ApiError as exc:
            logger.warning("[SearchTool] sciverse meta-search 失败 [%s]: %s", exc.code, exc.message)
            return self._fail(action, exc.message)
        except Exception as exc:
            logger.exception("[SearchTool] sciverse meta-search 调用异常")
            return self._fail(action, f"Sciverse 检索服务异常: {exc}")

        sciverse_body = body or {}
        partial = bool(sciverse_body.get("partial"))
        partial_reason = str(sciverse_body.get("partialReason") or "").strip() or None
        raw_results: list[dict] = sciverse_body.get("results", []) or []
        candidates = [
            normalize_meta_result(row)
            for row in raw_results
            if isinstance(row, dict) and (row.get("title") or "").strip()
        ]
        if not candidates:
            return self._empty(action, f"未找到与\"{query}\"相关的 Sciverse 文献候选（共 0 篇）")

        if isinstance(ctx, dict):
            cached = ctx.setdefault("search_candidates", [])
            seen = {
                (
                    item.get("openalexId")
                    or item.get("sciverseDocId")
                    or item.get("sciverseUniqueId")
                    or item.get("doi")
                    or item.get("candidate_id")
                    or f"{item.get('title', '')}:{item.get('year', '')}"
                )
                for item in cached
                if isinstance(item, dict)
            }
            for item in candidates:
                key = (
                    item.get("openalexId")
                    or item.get("sciverseDocId")
                    or item.get("sciverseUniqueId")
                    or item.get("doi")
                    or item.get("candidate_id")
                    or f"{item.get('title', '')}:{item.get('year', '')}"
                )
                if key not in seen:
                    cached.append(item)
                    seen.add(key)

        if emit is not None:
            try:
                await emit({
                    "type": "search_results",
                    "candidates": candidates,
                    "query": query,
                    "provider": "sciverse",
                    "partial": partial,
                    "partialReason": partial_reason,
                })
            except Exception as exc:  # noqa: BLE001
                logger.warning("[SearchTool] emit sciverse search_results 失败: %s", exc)

        total = len(candidates)
        summary = _build_search_summary(
            "Sciverse ",
            total,
            query,
            candidates,
            partial=partial,
            partial_reason=partial_reason,
        )
        return self._ok(
            action,
            data=[{
                "candidates": candidates,
                "total": total,
                "query": query,
                "provider": "sciverse",
                "partial": partial,
                "partialReason": partial_reason,
            }],
            source="api",
            summary=summary,
        )

    # ------------------------------------------------------------------
    # 多源检索（M3）—— 复用现有候选缓存/event/导入路径
    # ------------------------------------------------------------------

    def _execute_sources(self) -> ToolResult:
        """返回各数据源配置/可用状态，供 Agent 选源前查看（缺 key 显式提示，非静默）。"""
        rows = available_sources()
        lines: list[str] = []
        for r in rows:
            role = "补链" if r.get("role") == "enrichment" else "检索"
            status = "可用" if r["configured"] else f"未配置（{r.get('reason') or ''}）"
            lines.append(f"- {r['source']}（{role}·{r.get('tier', '')}）：{status}")
        summary = "多源数据源状态（未配置的源不会静默跳过，会如实标注）：\n" + "\n".join(lines)
        return self._ok("sources", data=[{"sources": rows}], source="api", summary=summary)

    @staticmethod
    def _format_source_stats(per_source: list[dict]) -> str:
        parts: list[str] = []
        for p in per_source:
            if p.get("available"):
                note = f"（{p['error']}）" if p.get("error") else ""
                parts.append(f"{p['source']} {p.get('count', 0)} 篇{note}")
            else:
                reason = p.get("reason") or p.get("error") or "不可用"
                parts.append(f"{p['source']} 未用（{reason}）")
        return "；".join(parts)

    async def _execute_multi(self, params: dict[str, Any], emit: Any, ctx: dict) -> ToolResult:
        """多源并发检索 → 跨源合并 + 确定性预过滤 → 复用候选卡/导入路径（双级筛第一级）。"""
        query = (params.get("query") or "").strip()
        if not query:
            return self._fail("multi", "query 是必填字段，请提供检索关键词")
        limit, limit_error = _parse_limit(params.get("limit"))
        if limit_error:
            return self._fail("multi", limit_error)
        since = (params.get("since") or _DEFAULT_SINCE).strip() or _DEFAULT_SINCE
        sources = params.get("sources") or "auto"

        try:
            result = await multi_source_search(sources, query, limit=limit, since=since)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[SearchTool] multi-source 检索异常")
            return self._fail("multi", f"多源检索服务异常: {exc}")

        src_summary = self._format_source_stats(result.per_source)
        candidates = result.candidates
        if not candidates:
            if emit is not None:
                try:
                    await emit({
                        "type": "search_results", "candidates": [], "query": query,
                        "provider": "multi", "perSource": result.per_source,
                    })
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[SearchTool] emit multi 空结果失败: %s", exc)
            return self._empty(
                "multi",
                f"多源检索未得到候选（关键词：{query}）。各源：{src_summary}。"
                "可换检索式或用 search__sources 查看是否有源未配置。",
            )

        _cache_candidates(ctx, candidates)

        if emit is not None:
            try:
                await emit({
                    "type": "search_results", "candidates": candidates, "query": query,
                    "provider": "multi", "perSource": result.per_source,
                })
            except Exception as exc:  # noqa: BLE001
                logger.warning("[SearchTool] emit multi search_results 失败: %s", exc)

        cards, hidden = _format_candidate_cards(candidates)
        merge_note = (
            f"合并前 {result.total_before_merge} → 跨源去重合并后 {result.total_after_merge}"
            + (f"，超额截断 {result.truncated}" if result.truncated else "")
        )
        more_note = f"\n（还有 {hidden} 篇未列出，若噪声较多请收紧检索式）" if hidden > 0 else ""
        summary = (
            f"多源检索到 {result.count} 篇候选（{merge_note}；关键词：{query}）。"
            f"各源：{src_summary}。已生成候选卡，下面逐条列出 candidate_id 与标题，"
            f"请逐条判断是否真正切题，仅用相关候选的 candidate_id 调用 "
            f"project__import_search_results 导入：\n{cards}{more_note}"
        )
        return self._ok(
            "multi",
            data=[{
                "candidates": candidates,
                "total": result.count,
                "query": query,
                "provider": "multi",
                "perSource": result.per_source,
                "totalBeforeMerge": result.total_before_merge,
                "totalAfterMerge": result.total_after_merge,
                "truncated": result.truncated,
            }],
            source="api",
            summary=summary,
        )
