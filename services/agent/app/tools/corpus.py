"""CorpusTool — 从 Project included 论文构建 R 分析语料（阶段3a）。

actions: build / status
依赖注入:
  - session_factory: 异步会话工厂（用于 corpus 仓储操作）。
  - r_client: RClient 实例（调用 /parse-from-records）。
"""
from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import select

from ..harness.tools import BaseTool, ToolResult
from ..models import Corpus
from ..repositories import corpus as corpus_repo


class CorpusTool(BaseTool):
    """从 Project included 论文构建 bibliometrix 语料，并查询语料状态。"""

    tool_id = "corpus"
    tool_name = "Corpus Tool"
    description = "从 Project included 论文构建 R 分析语料（保真路径，不绕 OpenAlex）；查询语料状态"
    actions = ["build", "status"]
    tags = ["read", "write"]

    action_schemas = {
        "build": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "integer",
                    "description": "项目 ID（必填）：将其 included 论文打包为语料",
                },
            },
            "required": ["project_id"],
        },
        "status": {
            "type": "object",
            "properties": {
                "corpus_id": {
                    "type": "integer",
                    "description": "语料 Postgres ID（必填）",
                },
            },
            "required": ["corpus_id"],
        },
    }

    def __init__(self, session_factory: Callable, r_client: Any) -> None:
        """
        Args:
            session_factory: 异步会话工厂（如 async_sessionmaker）。
            r_client: RClient 实例（支持 parse_from_records 方法）。
        """
        self._sf = session_factory
        self._r = r_client

    # ------------------------------------------------------------------
    # 核心执行
    # ------------------------------------------------------------------

    async def _execute(
        self, action: str, params: dict[str, Any], context: Any = None
    ) -> ToolResult:
        if action == "build":
            return await self._build(params)
        if action == "status":
            return await self._status(params)
        return self._fail(action, f"action '{action}' not implemented")

    # ------------------------------------------------------------------
    # build
    # ------------------------------------------------------------------

    async def _build(self, params: dict) -> ToolResult:
        project_id = params.get("project_id")
        if project_id is None:
            return self._fail("build", "project_id 是必填字段")
        project_id = int(project_id)

        async with self._sf() as s:
            # 1. 构建/复用快照（幂等）
            corpus = await corpus_repo.build_corpus_snapshot(s, project_id)

        corpus_id = corpus.id

        # 2. 幂等检查：已 ready 则直接返回（不重复调 R）
        if corpus.status == "ready" and corpus.r_corpus_id:
            row = self._corpus_row(corpus)
            return self._ok(
                "build", [row], source="db",
                summary=f"corpus id={corpus_id} 已就绪（r_corpus_id={corpus.r_corpus_id}），直接复用"
            )

        # 3. 取 included 题录
        async with self._sf() as s:
            records = await corpus_repo.get_corpus_records(s, corpus_id)

        if not records:
            # 空 included 集合：状态标 failed 并返回有意义错误
            async with self._sf() as s:
                await corpus_repo.mark_failed(s, corpus_id)
            return self._fail(
                "build",
                f"project id={project_id} 没有 included 论文，无法构建语料"
            )

        # 4. 调 R /parse-from-records
        try:
            status_code, body = await self._r.parse_from_records(records)
        except Exception as exc:
            async with self._sf() as s:
                await corpus_repo.mark_failed(s, corpus_id)
            return self._fail("build", f"R 服务不可达: {exc}")

        if status_code >= 400 or (body or {}).get("status") == "failed":
            code = (body or {}).get("code", "R_FAILED")
            msg = (body or {}).get("error") or (body or {}).get("message", f"R 返回 {status_code}")
            async with self._sf() as s:
                await corpus_repo.mark_failed(s, corpus_id)
            return self._fail("build", f"R 建库失败 [{code}]: {msg}")

        # 5. 写回 r_corpus_id + status=ready
        r_corpus_id = (body or {}).get("corpusId", "")
        doc_count = int((body or {}).get("documentCount") or len(records))

        async with self._sf() as s:
            corpus = await corpus_repo.mark_ready(s, corpus_id, r_corpus_id, doc_count)

        row = self._corpus_row(corpus)
        return self._ok(
            "build", [row], source="api",
            summary=f"corpus id={corpus_id} 构建成功（{doc_count} 篇，r_corpus_id={r_corpus_id}）"
        )

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------

    async def _status(self, params: dict) -> ToolResult:
        corpus_id = params.get("corpus_id")
        if corpus_id is None:
            return self._fail("status", "corpus_id 是必填字段")

        async with self._sf() as s:
            q = select(Corpus).where(Corpus.id == int(corpus_id))
            corpus = (await s.execute(q)).scalar_one_or_none()

        if corpus is None:
            return self._fail("status", f"corpus id={corpus_id} 不存在")

        row = self._corpus_row(corpus)
        return self._ok(
            "status", [row], source="db",
            summary=f"corpus id={corpus_id}: status={corpus.status}, docs={corpus.document_count}"
        )

    # ------------------------------------------------------------------
    # 私有辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _corpus_row(corpus: Corpus) -> dict:
        return {
            "corpus_id": corpus.id,
            "project_id": corpus.project_id,
            "status": corpus.status,
            "document_count": corpus.document_count,
            "r_corpus_id": corpus.r_corpus_id,
            "dbsource": corpus.dbsource,
            "content_hash": corpus.content_hash,
        }
