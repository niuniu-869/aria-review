"""ExtractTool — 结构化抽取 / 元数据回填工具（BaseTool 子类，P0-1）。

把"用 LLM 从已 OCR 全文抽取研究要素"和"回填缺失题录"工具化，让 agent 在需要
结构化要素时自主调用。

actions:
  structured — 抽取 research_question/method/findings/dataset/contribution 五字段，
               幂等 upsert 到 paper_extraction（包 extract_paper_structured）。
  metadata   — 从 OCR 全文回填缺失的 abstract/creators/year/keywords，仅填空字段
               （包 backfill_paper_metadata）。

设计要点（对齐作战方案 §10.2）：
  1. extract_paper_structured / backfill_paper_metadata 是**单篇**函数（内部各自管
     commit/rollback）；本工具复刻 main.py 对应端点的「项目内查询 + limit + 跳过已
     处理」逻辑，批量处理项目内论文。
  2. structured 的「跳过已抽取」在 **SQL 层** 用 Paper.id.notin_(PaperExtraction)
     实现（reextract=False 时），让 limit 落在真正待抽取的篇上，不是应用层 continue。
  3. metadata 用 onlyMissing 过滤「缺 abstract 或 creators」（默认 True）。
  4. 单次开会话贯穿整批；每篇用 s.get(Paper, id) 取新鲜对象（单篇函数 rollback 后
     下篇不受 expire 影响），不每篇开新 session。
  5. LLM 客户端从 tool_context["override"] 构造（无 override / 无 key → 服务端回退或
     FakeStreamClient 离线），与 REST 端点 _llm 同源。
"""
from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import func as sa_func
from sqlalchemy import or_, select

from ..harness.tools import BaseTool, ToolResult
from ..llm import get_llm_client
from ..models import Attachment, Paper, PaperExtraction, ProjectPaper
from ..services.extraction import extract_paper_structured
from ..services.metadata_backfill import backfill_paper_metadata


class ExtractTool(BaseTool):
    """结构化抽取 / 元数据回填工具：对项目内 OCR-done 论文批量处理。"""

    tool_id = "extract"
    tool_name = "Extract Tool"
    description = (
        "结构化抽取与元数据回填：对项目内已 OCR 的文献，用 LLM 抽取研究问题/方法/"
        "发现/数据集/贡献（structured），或回填缺失的摘要/作者/年份/关键词（metadata）"
    )
    actions = ["structured", "metadata"]
    tags = ["read", "write"]  # 写 PaperExtraction / Paper → 进 write 集合（串行）

    action_schemas = {
        "structured": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer", "description": "项目 ID（必填）"},
                "limit": {"type": "integer", "description": "本批上限，默认 20"},
                "reextract": {
                    "type": "boolean",
                    "description": "true 强制重抽已抽取篇；false（默认）SQL 层跳过已抽取篇",
                },
            },
            "required": ["project_id"],
        },
        "metadata": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer", "description": "项目 ID（必填）"},
                "limit": {"type": "integer", "description": "本批上限，默认 20"},
                "only_missing": {
                    "type": "boolean",
                    "description": "true（默认）只处理缺 abstract 或 creators 的篇；false 处理全部 OCR-done 篇",
                },
            },
            "required": ["project_id"],
        },
    }

    def __init__(self, session_factory: Callable) -> None:
        """Args: session_factory — async_sessionmaker（兜底；execute 优先用 tool_context）。"""
        self._sf = session_factory

    # ------------------------------------------------------------------
    # 核心执行
    # ------------------------------------------------------------------

    async def _execute(
        self, action: str, params: dict[str, Any], context: Any = None,
    ) -> ToolResult:
        if action not in self.actions:
            return self._fail(action, f"不支持的 action: {action}")

        ctx = context if isinstance(context, dict) else {}
        session_factory = ctx.get("session_factory") or self._sf
        project_id = params.get("project_id") or ctx.get("project_id")
        override = ctx.get("override")

        if project_id is None:
            return self._fail(action, "缺少 project_id")
        if session_factory is None:
            return self._fail(action, "缺少 session_factory（无法访问数据库）")
        project_id = int(project_id)

        llm = self._build_llm(override)
        limit = int(params.get("limit") or 20)

        if action == "structured":
            return await self._structured(
                session_factory, llm, action, project_id, limit,
                reextract=bool(params.get("reextract")),
            )
        # metadata
        return await self._metadata(
            session_factory, llm, action, project_id, limit,
            only_missing=params.get("only_missing", True),
        )

    # ------------------------------------------------------------------
    # structured：复刻 extract_structured_endpoint
    # ------------------------------------------------------------------

    async def _structured(
        self, session_factory, llm, action, project_id, limit, *, reextract,
    ) -> ToolResult:
        att_sq = (
            select(Attachment.paper_id)
            .where(
                Attachment.mineru_status == "done",
                Attachment.markdown_path.isnot(None),
            )
            .distinct()
            .scalar_subquery()
        )
        base_where = [ProjectPaper.project_id == project_id, Paper.id.in_(att_sq)]
        if not reextract:
            base_where.append(
                Paper.id.notin_(select(PaperExtraction.paper_id).scalar_subquery())
            )

        async with session_factory() as s:
            paper_ids = await self._batch_ids(s, base_where, limit)
            processed = extracted = skipped = failed = 0
            for pid in paper_ids:
                paper = await s.get(Paper, pid)
                if paper is None:
                    skipped += 1
                    continue
                processed += 1
                r = await extract_paper_structured(s, llm, paper)
                st = r.get("status")
                if st == "extracted":
                    extracted += 1
                elif st == "skipped":
                    skipped += 1
                else:
                    failed += 1
            available = await self._count(s, base_where)

        row = {
            "processed": processed,
            "extracted": extracted,
            "skipped": skipped,
            "failed": failed,
            "available": available,
        }
        summary = (
            f"结构化抽取：处理 {processed} 篇，成功 {extracted}，跳过 {skipped}，"
            f"失败 {failed}；待抽取 {available} 篇。"
        )
        return self._ok(action, [row], source="db", summary=summary)

    # ------------------------------------------------------------------
    # metadata：复刻 backfill_metadata_endpoint
    # ------------------------------------------------------------------

    async def _metadata(
        self, session_factory, llm, action, project_id, limit, *, only_missing,
    ) -> ToolResult:
        att_sq = (
            select(Attachment.paper_id)
            .where(
                Attachment.mineru_status == "done",
                Attachment.markdown_path.isnot(None),
            )
            .distinct()
            .scalar_subquery()
        )
        base_where = [ProjectPaper.project_id == project_id, Paper.id.in_(att_sq)]
        if only_missing:
            missing_abstract = or_(Paper.abstract.is_(None), Paper.abstract == "")
            missing_creators = or_(
                Paper.creators.is_(None),
                sa_func.json_array_length(Paper.creators) == 0,
            )
            base_where.append(or_(missing_abstract, missing_creators))

        async with session_factory() as s:
            paper_ids = await self._batch_ids(s, base_where, limit)
            processed = updated = skipped = failed = 0
            for pid in paper_ids:
                paper = await s.get(Paper, pid)
                if paper is None:
                    skipped += 1
                    continue
                processed += 1
                r = await backfill_paper_metadata(s, llm, paper)
                st = r.get("status")
                if st == "updated":
                    updated += 1
                elif st == "skipped":
                    skipped += 1
                else:
                    failed += 1
            available = await self._count(s, base_where)

        row = {
            "processed": processed,
            "updated": updated,
            "skipped": skipped,
            "failed": failed,
            "available": available,
        }
        summary = (
            f"元数据回填：处理 {processed} 篇，回填 {updated}，跳过 {skipped}，"
            f"失败 {failed}；待回填 {available} 篇。"
        )
        return self._ok(action, [row], source="db", summary=summary)

    # ------------------------------------------------------------------
    # 共享查询辅助
    # ------------------------------------------------------------------

    @staticmethod
    async def _batch_ids(s, base_where: list, limit: int) -> list[int]:
        """取本批待处理 paper_id（只取 id，避免 rollback expire 整批对象）。"""
        id_q = (
            select(Paper.id)
            .join(ProjectPaper, ProjectPaper.paper_id == Paper.id)
            .where(*base_where)
            .limit(limit)
        )
        return list((await s.execute(id_q)).scalars().all())

    @staticmethod
    async def _count(s, base_where: list) -> int:
        """处理后重新 count 真实剩余 available。"""
        cnt_q = (
            select(sa_func.count())
            .select_from(Paper)
            .join(ProjectPaper, ProjectPaper.paper_id == Paper.id)
            .where(*base_where)
        )
        return (await s.execute(cnt_q)).scalar_one()

    @staticmethod
    def _build_llm(override):
        """从 tool_context override 构造 LLM 客户端（含 .complete(messages)->str）。

        与 REST 侧 _llm 同源：有 key → DeepSeekClient；无 key → FakeStreamClient（离线）。
        """
        if override is not None and getattr(override, "api_key", ""):
            return get_llm_client(
                override.api_key,
                base_url=getattr(override, "base_url", "") or None,
                model=getattr(override, "model", "") or None,
            )
        return get_llm_client()
