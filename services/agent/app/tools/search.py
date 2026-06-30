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
from ..harness.tools import BaseTool, ToolResult

logger = logging.getLogger("agent.tools.search")

_DEFAULT_LIMIT = 50
_DEFAULT_SINCE = "2016-01-01"


def _sciverse_configured() -> bool:
    """Sciverse 是否已配置 base_url + token（决定自动路由能否切到 sciverse）。"""
    from ..config import settings
    return bool((settings.sciverse_base_url or "").strip() and (settings.sciverse_api_token or "").strip())


def _auto_provider(query: str) -> str:
    """provider 未显式指定时按 query 语言路由（benchmark 结论：中文/混合走 Sciverse 优、纯英文走 OpenAlex 优）。

    含中文(CJK)字符 **且 Sciverse 已配置** → sciverse（中文覆盖与语义检索更好，且直连不经 R）；
    否则 → openalex（英文相关性排序更稳）。配置感知：Sciverse 未配置时中文也回退 openalex，
    避免原本可用的中文检索因缺 token 直接失败（codex P1）。
    """
    has_cjk = any("一" <= ch <= "鿿" for ch in query)  # CJK 统一表意文字 U+4E00–U+9FFF
    if has_cjk and _sciverse_configured():
        return "sciverse"
    return "openalex"

# 候选枚举：summary 需逐条暴露 candidate_id + 标题，LLM 才能逐条判相关性并按 ID 自筛导入
# （旧版只预览前 5 条标题且无 ID，导致 prompts.py 要求的「只传相关 candidate_ids」无法落地）。
_SUMMARY_TITLE_CAP = 90    # 单条标题截断长度（足够判主题相关性，又约束字符预算）
_SUMMARY_CARD_CAP = 150    # 最多枚举条数（配合 tool_result_max_chars=12000，覆盖 limit≤100/120 全列）


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


def _build_search_summary(provider_label: str, total: int, query: str, candidates: list[dict]) -> str:
    """检索成功摘要：先一句概览（供 UI 200 字预览），再逐条候选清单（供 LLM 自筛）。"""
    cards, hidden = _format_candidate_cards(candidates)
    more_note = (
        f"\n（还有 {hidden} 篇未列出，若噪声较多请收紧检索式后重检）" if hidden > 0 else ""
    )
    return (
        f"{provider_label}检索到 {total} 篇候选文献（关键词：{query}）。已生成候选卡。"
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


class SearchTool(BaseTool):
    """按主题/关键词检索文献（调 R /search/openalex），只返回候选，不建库。"""

    tool_id = "search"
    tool_name = "Search Tool"
    description = (
        "按主题/关键词检索文献，返回候选卡列表（不建库）；支持 OpenAlex 与 Sciverse 两个数据源"
        "（provider 参数，中文/混合主题走 sciverse、纯英文走 openalex，省略则自动路由）；"
        "检索得到候选后，告知用户可在候选卡中选择加入文献库或纳入"
    )
    actions = ["topic"]
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
                    "description": "返回候选数量上限，默认 50；系统综述/赛题验证建议 50-100",
                    "default": _DEFAULT_LIMIT,
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
                        "检索数据源。中文/中英混合/自然语言问句/较长描述 → 'sciverse'（中文覆盖与语义更好）；"
                        "纯英文术语/缩写（GAN/CNN/ESG 等）→ 'openalex'（英文相关性排序更稳）。"
                        "省略则按主题是否含中文自动路由。"
                    ),
                },
            },
            "required": ["query"],
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
        if action != "topic":
            return self._fail(action, f"不支持的 action: {action}")

        ctx = context if isinstance(context, dict) else {}
        emit = ctx.get("emit")

        query = (params.get("query") or "").strip()
        if not query:
            return self._fail(action, "query 是必填字段，请提供检索关键词")

        limit = int(params.get("limit") or _DEFAULT_LIMIT)
        # clamp ≤500（系统综述/赛题需大批量检索；from-search 入库端 candidates 同步设
        # max_length=500 保持一致，杜绝"检索 N 但入库 422"）
        limit = min(500, max(1, limit))
        since = (params.get("since") or _DEFAULT_SINCE).strip() or _DEFAULT_SINCE
        # provider 路由：显式指定则尊重；省略则按 query 语言自动路由（含中文→sciverse, 否则→openalex）。
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
            return self._fail(action, f"检索服务不可达: {exc}")

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

        raw_results: list[dict] = (body or {}).get("results", []) or []

        # 空结果 → 友好提示
        if not raw_results:
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
                })
            except Exception as exc:  # noqa: BLE001
                logger.warning("[SearchTool] emit search_results 失败（不影响结果）: %s", exc)

        total = len(candidates)
        summary = _build_search_summary("", total, query, candidates)

        return self._ok(
            action,
            data=[{"candidates": candidates, "total": total, "query": query}],
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
        except Exception as exc:
            logger.exception("[SearchTool] sciverse meta-search 调用异常")
            return self._fail(action, f"Sciverse 检索服务不可达或未配置: {exc}")

        raw_results: list[dict] = (body or {}).get("results", []) or []
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
                })
            except Exception as exc:  # noqa: BLE001
                logger.warning("[SearchTool] emit sciverse search_results 失败: %s", exc)

        total = len(candidates)
        summary = _build_search_summary("Sciverse ", total, query, candidates)
        return self._ok(
            action,
            data=[{"candidates": candidates, "total": total, "query": query, "provider": "sciverse"}],
            source="api",
            summary=summary,
        )
