"""AnalysisTool — 文献计量分析工具（BaseTool 子类，只读）。

actions: overview / sources / authors / documents
依赖注入:
  - session_factory: 用于查 Corpus 行（取 r_corpus_id）。
  - r_client: RClient 实例，用于调用 R 分析服务。
"""
from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import select

from ..harness.tools import BaseTool, ToolResult
from ..models import Corpus


class AnalysisTool(BaseTool):
    """调用 R 分析服务进行文献计量分析（只读）。"""

    tool_id = "analysis"
    tool_name = "Analysis Tool"
    description = "文献计量分析：overview/sources/authors/documents（只读，通过 R 服务）"
    actions = ["overview", "sources", "authors", "documents"]
    tags = ["read"]

    action_schemas = {
        "overview": {
            "type": "object",
            "properties": {
                "corpus_id": {
                    "type": "integer",
                    "description": "Postgres corpus.id（必填）",
                },
            },
            "required": ["corpus_id"],
        },
        "sources": {
            "type": "object",
            "properties": {
                "corpus_id": {"type": "integer", "description": "Postgres corpus.id（必填）"},
            },
            "required": ["corpus_id"],
        },
        "authors": {
            "type": "object",
            "properties": {
                "corpus_id": {"type": "integer", "description": "Postgres corpus.id（必填）"},
            },
            "required": ["corpus_id"],
        },
        "documents": {
            "type": "object",
            "properties": {
                "corpus_id": {"type": "integer", "description": "Postgres corpus.id（必填）"},
            },
            "required": ["corpus_id"],
        },
    }

    def __init__(self, session_factory: Callable, r_client: Any) -> None:
        """
        Args:
            session_factory: 异步会话工厂（用于查 Corpus 表）。
            r_client: RClient 实例（可为 FakeR mock 方便测试）。
        """
        self._sf = session_factory
        self._r = r_client

    # ------------------------------------------------------------------
    # 核心执行
    # ------------------------------------------------------------------

    async def _execute(
        self, action: str, params: dict[str, Any], context: Any = None
    ) -> ToolResult:
        corpus_id = params.get("corpus_id")
        if corpus_id is None:
            return self._fail(action, "corpus_id 是必填字段")

        # 查 Corpus 行，取 r_corpus_id
        r_corpus_id = await self._resolve_r_corpus_id(int(corpus_id))
        if r_corpus_id is None:
            # 分两种情况给出明确错误
            corpus = await self._get_corpus(int(corpus_id))
            if corpus is None:
                return self._fail(action, f"corpus id={corpus_id} 不存在")
            return self._fail(
                action,
                f"corpus id={corpus_id} 尚未完成 build（r_corpus_id 为空），"
                "请先调用 build_corpus_snapshot 并等待 R 服务解析完成"
            )

        # 调 R 客户端
        if action == "overview":
            status_code, body = await self._r.get_overview(r_corpus_id)
        elif action == "sources":
            status_code, body = await self._r.get_sources(r_corpus_id)
        elif action == "authors":
            status_code, body = await self._r.get_authors(r_corpus_id)
        elif action == "documents":
            status_code, body = await self._r.get_documents(r_corpus_id)
        else:
            return self._fail(action, f"action '{action}' not implemented")

        if status_code >= 400:
            code = (body or {}).get("code", "UNKNOWN")
            msg = (body or {}).get("message", f"R 服务返回 {status_code}")
            return self._fail(action, f"R 服务错误 [{code}]: {msg}")

        # 将 R 返回的 JSON body 打包为 data 列表
        data = [body] if body else []
        summary = f"corpus id={corpus_id} ({action}) — r_corpus_id={r_corpus_id}"
        return self._ok(action, data, source="api", summary=summary)

    # ------------------------------------------------------------------
    # 私有辅助
    # ------------------------------------------------------------------

    async def _get_corpus(self, corpus_id: int) -> Corpus | None:
        async with self._sf() as s:
            q = select(Corpus).where(Corpus.id == corpus_id)
            return (await s.execute(q)).scalar_one_or_none()

    async def _resolve_r_corpus_id(self, corpus_id: int) -> str | None:
        """查 Postgres，返回 r_corpus_id；corpus 不存在或 r_corpus_id 为空 → None。"""
        corpus = await self._get_corpus(corpus_id)
        if corpus is None:
            return None
        if not corpus.r_corpus_id:
            return None
        return corpus.r_corpus_id
