"""ProjectTool — 研究项目管理工具（BaseTool 子类）。

actions: create / add / import_search_results / set_inclusion / list
依赖注入: session_factory (async_sessionmaker) 构造时传入。
"""
from __future__ import annotations

from typing import Any, Callable

from ..harness.tools import BaseTool, ToolResult
from ..errors import ApiError
from ..ingest.sciverse_fulltext import (
    fetch_and_store_sciverse_content,
    select_sciverse_backfill_candidates,
)
from ..ingest.search_metadata import parse_cited_by_count, parse_year
from ..repositories import library as lib_repo
from ..repositories import project as proj_repo
from ..sciverse import SciverseClient, sciverse_config

_VALID_STATUSES = {"candidate", "included", "excluded", "maybe"}
# 不传 candidate_ids 的默认整批导入，仅在候选量 ≤ 此阈值时允许；超过则强制显式挑选
# （双级筛硬约束，防多源大候选集被盲目整批灌库）。
_IMPORT_SELECT_THRESHOLD = 50


class ProjectTool(BaseTool):
    """研究项目管理：创建项目、添加论文、设置纳入状态、列出项目/论文。"""

    tool_id = "project"
    tool_name = "Project Tool"
    description = "管理研究项目：创建、向项目添加论文、设置文献筛选状态、列出项目或论文"
    actions = ["create", "add", "import_search_results", "set_inclusion", "list"]
    tags = ["read", "write"]

    action_schemas = {
        "create": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "项目名称（必填）"},
                "research_question": {
                    "type": "string",
                    "description": "研究问题（可选）",
                },
            },
            "required": ["name"],
        },
        "add": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer", "description": "项目 ID（必填）"},
                "paper_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "论文 ID 列表（必填）",
                },
            },
            "required": ["project_id", "paper_ids"],
        },
        "import_search_results": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer", "description": "项目 ID（必填）"},
                "candidate_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选：仅导入指定候选 ID；不传则导入最近检索缓存中的候选",
                },
                "limit": {
                    "type": "integer",
                    "description": "最多导入候选数量，默认 100",
                    "default": 100,
                },
                "default_status": {
                    "type": "string",
                    "enum": ["candidate", "included"],
                    "description": "导入后项目筛选状态，默认 candidate；需要直接纳入时传 included",
                    "default": "candidate",
                },
            },
            "required": ["project_id"],
        },
        "set_inclusion": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer", "description": "项目 ID（必填）"},
                "paper_id": {"type": "integer", "description": "论文 ID（必填）"},
                "status": {
                    "type": "string",
                    "enum": ["candidate", "included", "excluded", "maybe"],
                    "description": "纳入状态",
                },
                "reason": {
                    "type": "string",
                    "description": "排除/纳入理由（可选）",
                },
                "score": {
                    "type": "integer",
                    "description": "筛选分数（可选）",
                },
            },
            "required": ["project_id", "paper_id", "status"],
        },
        "list": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "integer",
                    "description": "项目 ID（可选）：提供则列该项目的论文；不提供则列所有项目",
                },
            },
            "required": [],
        },
    }

    def __init__(self, session_factory: Callable) -> None:
        """
        Args:
            session_factory: 异步会话工厂，如 SessionLocal。
        """
        self._sf = session_factory

    # ------------------------------------------------------------------
    # 核心执行
    # ------------------------------------------------------------------

    async def _execute(
        self, action: str, params: dict[str, Any], context: Any = None
    ) -> ToolResult:
        async with self._sf() as s:
            if action == "create":
                return await self._create(s, params)
            if action == "add":
                return await self._add(s, params)
            if action == "import_search_results":
                return await self._import_search_results(s, params, context)
            if action == "set_inclusion":
                return await self._set_inclusion(s, params)
            if action == "list":
                return await self._list(s, params)
        return self._fail(action, f"action '{action}' not implemented")

    # ------------------------------------------------------------------
    # action 实现
    # ------------------------------------------------------------------

    async def _create(self, s, params: dict) -> ToolResult:
        name = (params.get("name") or "").strip()
        if not name:
            return self._fail("create", "name 是必填字段")
        data: dict[str, Any] = {"name": name}
        if params.get("research_question"):
            data["research_question"] = params["research_question"]

        proj = await proj_repo.create_project(s, data)
        row = {
            "project_id": proj.id,
            "name": proj.name,
            "research_question": proj.research_question,
        }
        return self._ok(
            "create", [row], source="db",
            summary=f"已创建项目 id={proj.id}: {proj.name}"
        )

    async def _add(self, s, params: dict) -> ToolResult:
        project_id = params.get("project_id")
        paper_ids = params.get("paper_ids") or []
        if project_id is None:
            return self._fail("add", "project_id 是必填字段")
        if not paper_ids:
            return self._fail("add", "paper_ids 列表不能为空")

        results = []
        for pid in paper_ids:
            pp = await proj_repo.add_paper_to_project(
                s, int(project_id), int(pid)
            )
            results.append({
                "project_paper_id": pp.id,
                "project_id": pp.project_id,
                "paper_id": pp.paper_id,
                "inclusion_status": pp.inclusion_status,
            })

        return self._ok(
            "add", results, source="db",
            summary=f"已向项目 id={project_id} 关联 {len(results)} 篇论文"
        )

    @staticmethod
    def _candidate_key(cand: dict) -> str:
        return str(
            cand.get("openalexId")
            or cand.get("sciverseDocId")
            or cand.get("sciverseUniqueId")
            or cand.get("doi")
            or cand.get("candidate_id")
            or f"{cand.get('title', '')}:{cand.get('year', '')}"
        )

    @staticmethod
    def _cut(value: Any, limit: int) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text[:limit] if text else None

    def _paper_data_from_candidate(self, cand: dict) -> dict[str, Any]:
        title = self._cut(cand.get("title"), 1000)
        data: dict[str, Any] = {"title": title}
        for src, dst, limit in (
            ("doi", "doi", 255),
            ("abstract", "abstract", 20000),
            ("containerTitle", "container_title", 1000),
            ("url", "url", 2000),
            ("source", "source", 40),
            ("keywords", "keywords", 2000),
        ):
            value = self._cut(cand.get(src), limit)
            if value:
                data[dst] = value

        # 归一 float/str 年份（Sciverse 曾返回 2025.0 → isinstance(int) 整列丢弃）
        year = parse_year(cand.get("year"), date_hint=cand.get("publicationDate"))
        if year is not None:
            data["year"] = year
        authors = cand.get("authors") or []
        if isinstance(authors, list) and authors:
            data["creators"] = [
                {"literal": str(author)[:300]}
                for author in authors[:100]
                if str(author).strip()
            ]
        refs = self._references_from_candidate(cand)
        csl_json: dict[str, Any] = {}
        if refs:
            csl_json["references"] = refs
        cited_by_count = parse_cited_by_count(cand.get("citedByCount"))
        if cited_by_count is not None:
            csl_json["citedByCount"] = cited_by_count
        if csl_json:
            data["csl_json"] = csl_json
        return data

    @staticmethod
    def _references_from_candidate(cand: dict) -> list[str]:
        raw = cand.get("raw") if isinstance(cand.get("raw"), dict) else {}
        values = (
            cand.get("references")
            or cand.get("referencedWorks")
            or cand.get("referenced_works")
            or raw.get("references")
            or raw.get("referencedWorks")
            or raw.get("referenced_works")
            or []
        )
        if isinstance(values, str):
            values = [v for v in values.split(";") if v.strip()]
        if not isinstance(values, list):
            return []
        refs: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value).strip()
            if not text or text in seen:
                continue
            refs.append(text[:1000])
            seen.add(text)
            if len(refs) >= 1000:
                break
        return refs

    def _external_ids_from_candidate(self, cand: dict) -> list[dict]:
        external_ids: list[dict] = []
        url = self._cut(cand.get("url"), 2000)
        if cand.get("openalexId"):
            external_ids.append({
                "provider": "openalex",
                "id_type": "work_id",
                "external_id": str(cand["openalexId"])[:255],
                "url": url,
            })
        if cand.get("sciverseDocId"):
            external_ids.append({
                "provider": "sciverse",
                "id_type": "doc_id",
                "external_id": str(cand["sciverseDocId"])[:255],
                "url": url,
                "raw": cand.get("raw"),
            })
        if cand.get("sciverseUniqueId"):
            external_ids.append({
                "provider": "sciverse",
                "id_type": "unique_id",
                "external_id": str(cand["sciverseUniqueId"])[:255],
                "url": url,
                "raw": cand.get("raw"),
            })
        extra = cand.get("externalIds")
        if isinstance(extra, list):
            external_ids.extend(item for item in extra[:20] if isinstance(item, dict))
        return external_ids

    async def _import_search_results(self, s, params: dict, context: Any) -> ToolResult:
        project_id = params.get("project_id")
        if project_id is None:
            return self._fail("import_search_results", "project_id 是必填字段")

        status = params.get("default_status") or "candidate"
        if status not in {"candidate", "included"}:
            return self._fail("import_search_results", "default_status 必须是 candidate 或 included")

        ctx = context if isinstance(context, dict) else {}
        cached = [c for c in (ctx.get("search_candidates") or []) if isinstance(c, dict)]
        if not cached:
            return self._fail(
                "import_search_results",
                "本次运行尚无检索候选缓存，请先调用 search__topic",
            )

        wanted = {str(v) for v in (params.get("candidate_ids") or []) if str(v).strip()}
        raw_limit = params.get("limit")
        try:
            limit = None if raw_limit in (None, "", "all", "ALL", 0) else max(1, int(raw_limit))
        except (TypeError, ValueError):
            limit = None

        # 双级筛硬约束（QA 实测：多源候选量大时 Agent 常省略 candidate_ids 盲目整批导入，
        # 把不相关文献灌进库，且无批量清理手段）。候选量超阈值却不传 candidate_ids 时拒绝，
        # 强制 Agent 先按相关性挑 ID——prompt 已要求但 LLM 不总遵守，故在工具层兜底。
        distinct_cached = len({self._candidate_key(c) for c in cached if (c.get("title") or "").strip()})
        if not wanted and distinct_cached > _IMPORT_SELECT_THRESHOLD:
            return self._fail(
                "import_search_results",
                f"当前检索缓存有 {distinct_cached} 篇候选，数量较多，不能不加筛选整批导入。"
                f"请逐条判断相关性，仅用 candidate_ids 传入你**高度确信切题**的候选 ID 再导入，"
                f"避免把不相关文献一并灌入文献库（阈值 {_IMPORT_SELECT_THRESHOLD} 篇）。",
            )

        selected: list[dict] = []
        seen: set[str] = set()
        for cand in cached:
            key = self._candidate_key(cand)
            if wanted and key not in wanted and str(cand.get("candidate_id") or "") not in wanted:
                continue
            title = self._cut(cand.get("title"), 1000)
            if not title or key in seen:
                continue
            selected.append(cand)
            seen.add(key)
            if limit is not None and len(selected) >= limit:
                break

        if not selected:
            return self._empty("import_search_results", "未找到可导入的检索候选")

        imported = 0
        skipped = 0
        failed = 0
        paper_ids: list[int] = []
        sciverse_paper_ids: list[int] = []
        sciverse_seen: set[int] = set()

        for cand in selected:
            try:
                paper_data = self._paper_data_from_candidate(cand)
                paper = await lib_repo.add_paper(s, paper_data)

                external_ids = self._external_ids_from_candidate(cand)
                if external_ids:
                    await lib_repo.upsert_external_ids(s, paper.id, external_ids)

                existing_pp = await proj_repo.find_project_paper(s, int(project_id), paper.id)
                if existing_pp is not None:
                    if status == "included":
                        await proj_repo.set_inclusion(s, existing_pp.id, "included")
                    skipped += 1
                    paper_ids.append(paper.id)
                    if cand.get("sciverseDocId") and paper.id not in sciverse_seen:
                        sciverse_paper_ids.append(paper.id)
                        sciverse_seen.add(paper.id)
                    continue

                pp = await proj_repo.add_paper_to_project(
                    s,
                    project_id=int(project_id),
                    paper_id=paper.id,
                    added_by="search",
                )
                if status == "included":
                    await proj_repo.set_inclusion(s, pp.id, "included")

                imported += 1
                paper_ids.append(paper.id)
                if cand.get("sciverseDocId") and paper.id not in sciverse_seen:
                    sciverse_paper_ids.append(paper.id)
                    sciverse_seen.add(paper.id)
            except Exception:
                try:
                    await s.rollback()
                except Exception:
                    pass
                failed += 1

        fulltext = await self._auto_fetch_sciverse_fulltexts(
            s,
            project_id=int(project_id),
            paper_ids=sciverse_paper_ids,
            context=ctx,
        )
        metadata_only = max(0, len(paper_ids) - len(sciverse_paper_ids))
        data = [{
            "project_id": int(project_id),
            "imported": imported,
            "skipped": skipped,
            "failed": failed,
            "paper_ids": paper_ids,
            "default_status": status,
            "sciverse_fulltext": fulltext,
        }]
        fulltext_summary = ""
        if sciverse_paper_ids:
            if fulltext.get("not_configured"):
                fulltext_summary = (
                    f"；其中 {len(sciverse_paper_ids)} 篇 Sciverse 有全文，"
                    f"但 Sciverse 未配置，已跳过自动拉取；其余 {metadata_only} 篇仅题录"
                )
            else:
                fulltext_summary = (
                    f"；其中 {len(sciverse_paper_ids)} 篇 Sciverse 有全文，"
                    f"已自动拉取 {fulltext['fetched']} 篇成功 {fulltext['failed']} 篇失败；"
                    f"其余 {metadata_only} 篇仅题录"
                )
        elif paper_ids:
            fulltext_summary = f"；本次 {len(paper_ids)} 篇均仅题录"
        return self._ok(
            "import_search_results",
            data,
            source="db",
            summary=(
                f"已从最近检索候选导入 {imported} 篇到项目 id={project_id}"
                f"（跳过已关联 {skipped}，失败 {failed}，状态 {status}）"
                f"{fulltext_summary}"
            ),
        )

    async def _auto_fetch_sciverse_fulltexts(
        self,
        s,
        *,
        project_id: int,
        paper_ids: list[int],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """为本次导入的 Sciverse doc_id 论文自动补全文；失败只记账不阻断导入。"""
        unique_ids = list(dict.fromkeys(int(pid) for pid in paper_ids))
        if not unique_ids:
            return {"eligible": 0, "attempted": 0, "fetched": 0, "failed": 0, "failures": []}

        override = context.get("sciverse") if isinstance(context.get("sciverse"), dict) else {}
        try:
            client = SciverseClient(sciverse_config(
                override.get("base_url"),
                override.get("api_token"),
            ))
        except ApiError as exc:
            return {
                "eligible": len(unique_ids),
                "attempted": 0,
                "fetched": 0,
                "failed": 0,
                "failures": [],
                "not_configured": True,
                "reason": exc.message,
            }

        candidates = (await select_sciverse_backfill_candidates(
            s,
            project_id=project_id,
            paper_ids=unique_ids,
        ))[:25]
        fetched = 0
        failures: list[dict[str, Any]] = []
        for candidate in candidates:
            try:
                await fetch_and_store_sciverse_content(
                    s,
                    project_id=project_id,
                    paper_id=candidate.paper_id,
                    client=client,
                    doc_id=candidate.doc_id,
                )
                fetched += 1
            except Exception as exc:  # noqa: BLE001
                try:
                    await s.rollback()
                except Exception:
                    pass
                failures.append({
                    "paperId": candidate.paper_id,
                    "reason": exc.message if isinstance(exc, ApiError) else str(exc),
                })
        return {
            "eligible": len(unique_ids),
            "attempted": len(candidates),
            "fetched": fetched,
            "failed": len(failures),
            "failures": failures,
        }

    async def _set_inclusion(self, s, params: dict) -> ToolResult:
        project_id = params.get("project_id")
        paper_id = params.get("paper_id")
        status = params.get("status")
        reason = params.get("reason")
        score = params.get("score")

        if project_id is None or paper_id is None:
            return self._fail("set_inclusion", "project_id 和 paper_id 均为必填字段")
        if status not in _VALID_STATUSES:
            return self._fail(
                "set_inclusion",
                f"status 必须为 {sorted(_VALID_STATUSES)} 之一，收到: {status!r}"
            )

        # 先查 ProjectPaper 行
        pp = await proj_repo.find_project_paper(s, int(project_id), int(paper_id))
        if pp is None:
            return self._fail(
                "set_inclusion",
                f"论文 id={paper_id} 尚未关联到项目 id={project_id}，请先调用 project__add"
            )

        updated = await proj_repo.set_inclusion(
            s, pp.id, status, reason=reason, score=score
        )
        row = {
            "project_paper_id": updated.id,
            "project_id": updated.project_id,
            "paper_id": updated.paper_id,
            "inclusion_status": updated.inclusion_status,
            "exclusion_reason": updated.exclusion_reason,
            "screening_score": updated.screening_score,
        }
        return self._ok(
            "set_inclusion", [row], source="db",
            summary=f"论文 id={paper_id} 在项目 id={project_id} 中状态已更新为 {status}"
        )

    async def _list(self, s, params: dict) -> ToolResult:
        project_id = params.get("project_id")

        if project_id is None:
            # 列出所有项目
            projects = await proj_repo.list_projects(s)
            if not projects:
                return self._empty("list", "暂无项目")
            data = [
                {
                    "project_id": p.id,
                    "name": p.name,
                    "research_question": p.research_question,
                    "created_at": str(p.created_at),
                }
                for p in projects
            ]
            return self._ok(
                "list", data, source="db",
                summary=f"共 {len(data)} 个项目"
            )
        else:
            # 列出该项目的论文
            rows = await proj_repo.list_project_papers(s, int(project_id))
            if not rows:
                return self._empty("list", f"项目 id={project_id} 暂无关联论文")
            data = [
                {
                    "project_paper_id": pp.id,
                    "paper_id": paper.id,
                    "title": paper.title,
                    "doi": paper.doi,
                    "year": paper.year,
                    "inclusion_status": pp.inclusion_status,
                    "exclusion_reason": pp.exclusion_reason,
                    "screening_score": pp.screening_score,
                }
                for pp, paper in rows
            ]
            return self._ok(
                "list", data, source="db",
                summary=f"项目 id={project_id} 共 {len(data)} 篇论文"
            )
