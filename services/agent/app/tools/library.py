"""LibraryTool — 文献库管理工具（BaseTool 子类）。

actions: add / find / get / tag
依赖注入: session_factory (async_sessionmaker) 构造时传入，execute 内按请求建会话。
"""
from __future__ import annotations

from typing import Any, Callable

from ..harness.tools import BaseTool, ToolResult
from ..repositories import library as lib_repo


class LibraryTool(BaseTool):
    """文献题录库的增删查改工具。"""

    tool_id = "library"
    tool_name = "Library Tool"
    description = "管理本地文献题录库：新增、查找、获取、打标签"
    actions = ["add", "find", "get", "tag"]
    tags = ["read", "write"]

    action_schemas = {
        "add": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "论文标题（必填）"},
                "doi": {"type": "string", "description": "DOI（可选，支持 URL 前缀自动剥离）"},
                "authors": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "作者列表（可选）",
                },
                "year": {"type": "integer", "description": "发表年份（可选）"},
                "abstract": {"type": "string", "description": "摘要（可选）"},
                "keywords": {"type": "string", "description": "关键词，逗号分隔（可选）"},
                "source": {
                    "type": "string",
                    "description": "来源数据库：wos/openalex/arxiv/upload（可选）",
                },
            },
            "required": ["title"],
        },
        "find": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "关键词，在 title/doi/keywords 中模糊匹配",
                },
                "limit": {
                    "type": "integer",
                    "description": "最大返回数量，默认 20",
                    "default": 20,
                },
            },
            "required": ["query"],
        },
        "get": {
            "type": "object",
            "properties": {
                "paper_id": {"type": "integer", "description": "论文 ID"},
            },
            "required": ["paper_id"],
        },
        "tag": {
            "type": "object",
            "properties": {
                "paper_id": {"type": "integer", "description": "论文 ID"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "标签名称列表",
                },
            },
            "required": ["paper_id", "tags"],
        },
    }

    def __init__(self, session_factory: Callable) -> None:
        """
        Args:
            session_factory: 异步会话工厂，如 SessionLocal (async_sessionmaker)。
                             测试时可注入指向测试库的工厂。
        """
        self._sf = session_factory

    # ------------------------------------------------------------------
    # 核心执行
    # ------------------------------------------------------------------

    async def _execute(
        self, action: str, params: dict[str, Any], context: Any = None
    ) -> ToolResult:
        async with self._sf() as s:
            if action == "add":
                return await self._add(s, params)
            if action == "find":
                return await self._find(s, params)
            if action == "get":
                return await self._get(s, params)
            if action == "tag":
                return await self._tag(s, params)
        # 不应到达此处（BaseTool.execute 已过滤非法 action）
        return self._fail(action, f"action '{action}' not implemented")

    # ------------------------------------------------------------------
    # action 实现
    # ------------------------------------------------------------------

    async def _add(self, s, params: dict) -> ToolResult:
        title = (params.get("title") or "").strip()
        if not title:
            return self._fail("add", "title 是必填字段")

        # 构造 data dict，忽略 None 值
        data: dict[str, Any] = {"title": title}
        for field in ("doi", "year", "abstract", "keywords", "source"):
            if params.get(field) is not None:
                data[field] = params[field]

        # authors 列表 → CSL creators 格式存入 creators 字段
        authors = params.get("authors")
        if authors:
            data["creators"] = [
                {"literal": a} if isinstance(a, str) else a for a in authors
            ]

        # 获取插入前已存在的行（用于判断 created）
        dedup_key = lib_repo.compute_dedup_key(data)
        existing = await lib_repo.find_by_dedup(s, dedup_key)
        created = existing is None

        paper = await lib_repo.add_paper(s, data)
        row = {
            "paper_id": paper.id,
            "title": paper.title,
            "doi": paper.doi,
            "year": paper.year,
            "created": created,
        }
        summary = (
            f"已新增论文 id={paper.id}: {paper.title}"
            if created
            else f"论文已存在 id={paper.id}: {paper.title}"
        )
        return self._ok("add", [row], source="db", summary=summary)

    async def _find(self, s, params: dict) -> ToolResult:
        query = (params.get("query") or "").strip()
        if not query:
            return self._fail("find", "query 不能为空")
        limit = int(params.get("limit") or 20)
        papers = await lib_repo.find_by_query(s, query, limit=limit)
        if not papers:
            return self._empty("find", f"未找到匹配 '{query}' 的论文")
        data = [
            {"id": p.id, "title": p.title, "year": p.year, "doi": p.doi}
            for p in papers
        ]
        return self._ok("find", data, source="db", summary=f"命中 {len(data)} 篇论文")

    async def _get(self, s, params: dict) -> ToolResult:
        paper_id = params.get("paper_id")
        if paper_id is None:
            return self._fail("get", "paper_id 是必填字段")
        paper = await lib_repo.get_by_id(s, int(paper_id))
        if paper is None:
            return self._fail("get", f"论文 id={paper_id} 不存在")
        row = {
            "id": paper.id,
            "title": paper.title,
            "doi": paper.doi,
            "year": paper.year,
            "abstract": paper.abstract,
            "keywords": paper.keywords,
            "source": paper.source,
            "creators": paper.creators,
            "dedup_key": paper.dedup_key,
        }
        return self._ok("get", [row], source="db", summary=f"论文 id={paper.id}: {paper.title}")

    async def _tag(self, s, params: dict) -> ToolResult:
        paper_id = params.get("paper_id")
        tags = params.get("tags") or []
        if paper_id is None:
            return self._fail("tag", "paper_id 是必填字段")
        if not tags:
            return self._fail("tag", "tags 列表不能为空")

        paper = await lib_repo.get_by_id(s, int(paper_id))
        if paper is None:
            return self._fail("tag", f"论文 id={paper_id} 不存在")

        applied = await lib_repo.add_tags(s, int(paper_id), tags)
        row = {"paper_id": int(paper_id), "applied_tags": applied}
        return self._ok(
            "tag", [row], source="db",
            summary=f"论文 id={paper_id} 已打标签: {', '.join(applied)}"
        )
