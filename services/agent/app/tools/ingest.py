"""IngestTool — 全文摄取工具（BaseTool 子类，P0-1）。

把 MinerU 全文解析能力工具化，让 agent 自主决定"先解析全文再抽取再综述"。

action:
  parse — 解析一批 PDF（按路径列表，或项目内尚未 OCR-done 的论文）为全文
          Markdown，建 Paper + Attachment（dedup），并 add_paper_to_project 关联
          到项目（否则 ReviewTool 看不到 included 语料）。

设计要点（对齐作战方案 §10.2 工程坑清单）：
  1. 核心包 `ingest_pdfs()`（app/ingest/fulltext.py），它已带缓存命中 / DB 写入 /
     逐批失败隔离 / 无 OCR token 本地降级；不直接调 mineru.parse_pdfs。
  2. 工具内用 `async with session_factory() as s` 单次开会话；不复用一个跨整批的
     长事务（ingest_pdfs 内部每篇一 commit，事务边界由其自管）。
  3. `ingest_pdfs()` 只建 Paper/Attachment，**不**自动 add 到 project → 本工具拿到
     成功结果后，对每篇做 find_project_paper（幂等统计）+ add_paper_to_project
     （ON CONFLICT DO NOTHING，幂等），added_by="ingest"。
  4. `cached`（sha256.md 命中缓存复用）也算成功 → summary 显式给"解析 N 篇
     (含缓存命中 M 篇)"，否则演示数字会偏低。
  5. 单篇/整批失败不抛，逐项 status=failed 记入 failed 计数。
  6. 不调大 tool_timeout；现场真实 OCR poll 默认 15 分钟会超时，demo 必用缓存命中 /
     预置 markdown（本工具不改超时，由 engine.tool_timeout 控制）。
  7. action_schema 接受路径列表（不沿用 REST 端点的 PDF/ZIP 限制），也支持
     paths 省略时解析项目内尚未 OCR-done 的论文。
  8. 项目模式（省略 paths）收敛性：选中原始 pending 附件后，解析成功就把**原始
     附件**回写为 mineru_status=done + sha256 + markdown_path，使其退出未解析队列。
     否则原 paper 永不进 done 集合、同 path 反复被选中并堆叠重复 done 附件，永不
     收敛（codex P0-1-fix；同项目无 paths 连调两次，第二次必为空批次）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select

from ..harness.tools import BaseTool, ToolResult
from ..ingest.fulltext import ingest_pdfs
from ..models import Attachment, ProjectPaper
from ..repositories.project import add_paper_to_project, find_project_paper


class IngestTool(BaseTool):
    """全文摄取工具：把 PDF 解析为全文 Markdown 并建库、关联到项目。"""

    tool_id = "ingest"
    tool_name = "Ingest Tool"
    description = (
        "全文摄取：用 MinerU 把指定路径的 PDF（或项目内尚未解析的论文）解析为全文 "
        "Markdown，建文献题录并关联到当前项目；缓存命中直接复用，不重复 OCR"
    )
    actions = ["parse"]
    tags = ["read", "write"]  # 写 Paper/Attachment/ProjectPaper → 进 write 集合（串行）

    action_schemas = {
        "parse": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "integer",
                    "description": "项目 ID（必填；解析结果会关联到该项目）",
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "要解析的 PDF 文件路径列表（绝对路径）。"
                        "省略或为空时，解析项目内尚未 OCR-done 的论文（其附件 path）。"
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "省略 paths 时，单批解析项目内未解析论文的上限，默认 20",
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
        if action != "parse":
            return self._fail(action, f"不支持的 action: {action}")

        ctx = context if isinstance(context, dict) else {}
        session_factory = ctx.get("session_factory") or self._sf
        project_id = params.get("project_id") or ctx.get("project_id")

        if project_id is None:
            return self._fail(action, "缺少 project_id")
        if session_factory is None:
            return self._fail(action, "缺少 session_factory（无法访问数据库）")
        project_id = int(project_id)

        explicit_paths = params.get("paths") or []
        limit = int(params.get("limit") or 20)

        # 单次开会话贯穿：解析（ingest_pdfs 内部每篇自 commit）+ 关联到项目。
        # 注意：ingest_pdfs 的 session 是 keyword-only。
        async with session_factory() as s:
            # 1) 确定待解析路径
            # 项目模式下额外拿到「原始 pending 附件」映射 path -> [attachment_id]，
            # 解析成功后回写这些原始附件为 done，确保它们退出未解析队列（收敛）。
            pending_by_path: dict[str, list[int]] = {}
            if explicit_paths:
                paths: list[str] = [str(p) for p in explicit_paths]
                source = "explicit"
            else:
                pending_by_path = await self._project_pending_attachments(
                    s, project_id, limit,
                )
                paths = list(pending_by_path.keys())
                source = "project"

            if not paths:
                if source == "project":
                    msg = (
                        f"项目 {project_id} 内没有尚未解析（OCR-done）且带 PDF 路径的论文，"
                        "无需解析"
                    )
                else:
                    msg = "未提供任何待解析路径"
                return self._empty(action, msg)

            # 2) 解析（缓存命中复用 / 逐篇失败隔离，由 ingest_pdfs 内部保证）
            results = await ingest_pdfs(paths=paths, language="en", session=s)

            # 3) 统计 + 关联到项目（成功项才 add_paper_to_project，幂等）
            done = cached = failed = 0
            linked = already_linked = 0
            backfilled = 0  # 项目模式下回写为 done 的原始 pending 附件数
            rows: list[dict] = []

            for r in results:
                status = r.get("status", "failed")
                paper_id = r.get("paper_id")
                pdf_path = r.get("pdf_path", "")
                pdf_name = Path(pdf_path).name or "unknown.pdf"

                if status == "failed" or paper_id is None:
                    failed += 1
                    rows.append({
                        "pdf": pdf_name,
                        "status": "failed",
                        "paper_id": None,
                        "err": r.get("err") or "ingest 失败",
                    })
                    continue

                # cached 与 done 都算成功（缓存命中复用同样建好了 Paper/Attachment）
                if status == "cached":
                    cached += 1
                else:
                    done += 1

                # 项目模式：把原始 pending 附件回写为 done（带 sha256 + markdown_path），
                # 让原 paper 退出未解析队列 → 同项目无 paths 连调可收敛。
                # 不删除/迁移原附件（保留来源轨迹），只标记解析完成。
                if pending_by_path:
                    backfilled += await self._backfill_pending_attachments(
                        s,
                        pending_by_path.get(pdf_path, []),
                        sha256=r.get("sha256"),
                        markdown_path=r.get("markdown_path"),
                    )

                # 关联到项目（幂等）：先查再关，statistics 区分新关联 / 已存在
                existing = await find_project_paper(s, project_id, paper_id)
                if existing is not None:
                    already_linked += 1
                else:
                    await add_paper_to_project(
                        s, project_id=project_id, paper_id=paper_id, added_by="ingest",
                    )
                    linked += 1

                rows.append({
                    "pdf": pdf_name,
                    "status": status,
                    "paper_id": paper_id,
                    "markdown_len": r.get("markdown_len", 0),
                })

        succeeded = done + cached
        summary = (
            f"解析 {succeeded} 篇（含缓存命中 {cached} 篇），失败 {failed} 篇；"
            f"新关联到项目 {linked} 篇，已在项目 {already_linked} 篇。"
        )
        # data_source: 有缓存命中时标 cache，否则 api（MinerU/本地降级）
        data_source = "cache" if cached and not done else "api"
        return self._ok(action, rows, source=data_source, summary=summary)

    # ------------------------------------------------------------------
    # 辅助：项目内尚未 OCR-done 的论文附件（path + 原始 attachment_id）
    # ------------------------------------------------------------------

    async def _project_pending_attachments(
        self, s, project_id: int, limit: int,
    ) -> dict[str, list[int]]:
        """取项目内「有 PDF 路径但尚未 OCR-done」的原始附件，返回 path -> [attachment_id]。

        判定：Attachment.path 非空，且该 paper 没有任何 mineru_status=done 的附件。
        返回原始 attachment_id（而非只返回 path），以便解析成功后把这些 pending
        附件回写为 done —— 否则原 paper 永不进 done 集合，同 path 会被反复选中、
        缓存命中后继续堆叠重复 done 附件，永不收敛（codex P0-1-fix）。

        limit 按「论文数」截断（取最早 limit 个 paper），同一 paper 多个附件共享一行。
        ingest_pdfs 走 sha256 缓存幂等，已解析过的 PDF 命中缓存直接复用（cached）。
        """
        # 项目内已有 OCR-done markdown 的 paper（应跳过）
        done_paper_sq = (
            select(Attachment.paper_id)
            .where(
                Attachment.mineru_status == "done",
                Attachment.markdown_path.isnot(None),
            )
            .distinct()
            .scalar_subquery()
        )

        # 取项目内尚未 OCR-done 且 paper_id 在「最早 limit 个未解析 paper」内的附件。
        # 子查询先按 paper 截断 limit，保证 limit 语义是「论文数」而非「附件行数」。
        paper_q = (
            select(Attachment.paper_id)
            .join(ProjectPaper, ProjectPaper.paper_id == Attachment.paper_id)
            .where(
                ProjectPaper.project_id == project_id,
                Attachment.path.isnot(None),
                Attachment.path != "",
                Attachment.paper_id.notin_(done_paper_sq),
            )
            .distinct()
            .order_by(Attachment.paper_id.asc())
            .limit(limit)
        )
        target_paper_ids = (await s.execute(paper_q)).scalars().all()
        if not target_paper_ids:
            return {}

        att_q = (
            select(Attachment.id, Attachment.path)
            .where(
                Attachment.paper_id.in_(target_paper_ids),
                Attachment.path.isnot(None),
                Attachment.path != "",
                Attachment.paper_id.notin_(done_paper_sq),
            )
            .order_by(Attachment.id.asc())
        )
        out: dict[str, list[int]] = {}
        for att_id, path in (await s.execute(att_q)).all():
            if not path:
                continue
            out.setdefault(path, []).append(att_id)
        return out

    async def _backfill_pending_attachments(
        self,
        s,
        attachment_ids: list[int],
        *,
        sha256: str | None,
        markdown_path: str | None,
    ) -> int:
        """把原始 pending 附件回写为 done（带 sha256 + markdown_path），返回更新条数。

        只回写仍非 done 的行（幂等：缓存命中再调时已 done 的不重复改）。本工具单次
        会话内自 commit（与 ingest_pdfs 每篇一 commit 的事务风格一致）。
        """
        if not attachment_ids or not markdown_path:
            return 0
        updated = 0
        for att_id in attachment_ids:
            att = await s.get(Attachment, att_id)
            if att is None or att.mineru_status == "done":
                continue
            att.mineru_status = "done"
            att.markdown_path = markdown_path
            if sha256:
                att.sha256 = sha256
            updated += 1
        if updated:
            await s.commit()
        return updated
