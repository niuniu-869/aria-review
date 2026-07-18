"""A2 · ReadPaperTool — 按需导航单篇论文（outline/section/search_evidence）。

升级 review/read.py 的"整篇 dump"：GAP / 价值 subagent 用本工具按需翻页取证，
不被整篇全文撑爆 context（呼应 Anthropic context-engineering）。坐标对齐 EvidenceRef。

论文内容来源（按优先级）：
  1) context['papers'][paper_id]：上层编排预载（A5 用 load_project_corpus 一次性装好，
     省去逐次 DB 命中；测试也走此注入）。形如 {full_md, content_list, page_map?}。
  2) context['session_factory'] + project_id：按需从 DocumentStructure + markdown 文件加载
     （复用 main.py 的 markdown 路径安全护栏，防任意文件读取）。
两者皆无法得到该 paper → fail-loud（success=False）。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.harness.tools import BaseTool, ToolResult
from app.structure.reader import build_outline, read_section, search_evidence

logger = logging.getLogger("agent.tools.read_paper")

_SECTION_MAX_CHARS = 4000
_SEARCH_LIMIT = 5

# F-12：加载失败原因 → 面向用户的错误消息（区分"不在项目/无全文/全文读取失败"，
# 替代原先三合一的 merged message，让失败可定位）。
_REASON_MESSAGES = {
    "not_in_project": "文献不在本项目中",
    "no_attachment": "文献尚无可用全文（请先完成 OCR 解析）",
    "markdown_unreadable": "全文文件读取失败",
}


class ReadPaperTool(BaseTool):
    tool_id = "read_paper"
    tool_name = "论文导航阅读"
    description = (
        "按需导航单篇论文：outline 看章节与行号；section 按行号读逐字原文；"
        "search_evidence 按关键词命中并返回源坐标(页/块/bbox/章节)。逐字保留，带溯源坐标。"
    )
    actions = ["outline", "section", "search_evidence"]
    tags = ["read"]
    action_schemas = {
        "outline": {
            "type": "object",
            "properties": {"paper_id": {"type": "integer", "description": "论文 ID"}},
            "required": ["paper_id"],
        },
        "section": {
            "type": "object",
            "properties": {
                "paper_id": {"type": "integer"},
                "start_line": {"type": "integer", "description": "起始行(1-based, 闭区间)"},
                "end_line": {"type": "integer", "description": "结束行(1-based, 闭区间)"},
                "max_chars": {"type": "integer", "description": f"截断上限(默认 {_SECTION_MAX_CHARS})"},
            },
            "required": ["paper_id", "start_line", "end_line"],
        },
        "search_evidence": {
            "type": "object",
            "properties": {
                "paper_id": {"type": "integer"},
                "query": {"type": "string", "description": "检索关键词/短语"},
                "limit": {"type": "integer", "description": f"返回命中上限(默认 {_SEARCH_LIMIT})"},
            },
            "required": ["paper_id", "query"],
        },
    }

    async def _execute(self, action: str, params: dict[str, Any], context: Any = None) -> ToolResult:
        paper_id = params.get("paper_id")
        if paper_id is None:
            return self._fail(action, "缺少 paper_id")
        try:
            paper_id = int(paper_id)
        except (TypeError, ValueError):
            return self._fail(action, f"paper_id 非整数: {paper_id!r}")

        paper, reason = await self._load_paper(context, paper_id)
        if paper is None:
            # F-12：按失败原因给出具体消息（不再三合一 merged message）；
            # 未知/兜底情形（无预载且无 DB 通道、DB 异常）保留通用提示。
            detail = _REASON_MESSAGES.get(reason or "")
            if detail:
                return self._fail(action, f"无法加载 paper {paper_id}：{detail}")
            return self._fail(action, f"无法加载 paper {paper_id}")

        full_md = paper.get("full_md") or ""
        content_list = paper.get("content_list") or []
        page_map = paper.get("page_map") or {}

        if action == "outline":
            data = build_outline(full_md, page_map=page_map)
            return self._ok("outline", data, source="structure",
                            summary=f"paper {paper_id} 共 {len(data)} 个章节")
        if action == "section":
            sec = read_section(
                full_md,
                int(params.get("start_line", 1)),
                int(params.get("end_line", 1)),
                max_chars=int(params.get("max_chars", _SECTION_MAX_CHARS)),
                page_map=page_map,
            )
            return self._ok("section", [sec], source="structure",
                            summary=f"读 {sec['page_label']} 行 {sec['start_line']}-{sec['end_line']}"
                                    f"（{sec['total_chars']} 字{'，已截断' if sec['truncated'] else ''}）")
        if action == "search_evidence":
            hits = search_evidence(
                content_list,
                params.get("query", ""),
                limit=int(params.get("limit", _SEARCH_LIMIT)),
            )
            return self._ok("search_evidence", hits, source="structure",
                            summary=f"'{params.get('query','')}' 命中 {len(hits)} 处（带源坐标）")
        return self._fail(action, f"未知 action: {action}")

    # ------------------------------------------------------------------ 加载

    async def _load_paper(self, context: Any, paper_id: int) -> tuple[dict | None, str | None]:
        """取论文 ({full_md, content_list, page_map}, 失败原因)：优先 context 预载，否则 DB 按需加载。

        失败原因（F-12）：not_in_project / no_attachment / markdown_unreadable；
        None 表示无法归类（无预载且无 DB 通道、DB 异常等兜底）。
        """
        if isinstance(context, dict):
            preloaded = (context.get("papers") or {}).get(paper_id)
            if preloaded:
                return preloaded, None
            sf = context.get("session_factory")
            project_id = context.get("project_id")
            if sf is not None and project_id is not None:
                try:
                    return await self._load_from_db(sf, int(project_id), paper_id)
                except Exception as e:  # noqa: BLE001 — 加载失败按 None（fail-loud 在调用处）
                    logger.warning("[read_paper] DB 加载失败 paper=%s: %s", paper_id, e)
                    return None, None
        return None, None

    @staticmethod
    async def _load_from_db(session_factory: Any, project_id: int, paper_id: int) -> tuple[dict | None, str | None]:
        """从 DocumentStructure + markdown 文件加载（复用 main.py 路径安全护栏）。

        返回 (paper, None) 或 (None, reason)：paper 未关联项目 → not_in_project；
        有 markdown 附件但路径护栏/读盘失败且无任何可用内容 → markdown_unreadable；
        其余无结构无全文 → no_attachment。
        """
        from sqlalchemy import select

        from ..models import Attachment, DocumentStructure
        from ..repositories import project as project_repo

        async with session_factory() as s:
            pp = await project_repo.find_project_paper(s, project_id, paper_id)
            if pp is None:
                return None, "not_in_project"
            atts = (
                await s.execute(
                    select(Attachment).where(Attachment.paper_id == paper_id)
                    .order_by(Attachment.id.desc())
                )
            ).scalars().all()
            ds = None
            att = None
            for cand in atts:
                ds = (
                    await s.execute(
                        select(DocumentStructure)
                        .where(DocumentStructure.attachment_id == cand.id)
                    )
                ).scalar_one_or_none()
                if ds is not None:
                    att = cand
                    break

        content_list = (ds.content_list if ds else None) or []
        page_map = (ds.page_map if ds else None) or {}
        full_md = ""
        # 全文与结构必须**同源（同一附件）**：否则 outline/section 的页码/行号来自 A 文档，
        # 而 search_evidence 的 block 坐标来自 B 文档 → 坐标错配 = 静默伪溯源（codex A2 P2）。
        # 规则：
        #  - 有结构附件(att)：仅当 att 自身带可读 markdown 才读全文（content_list 与 full_md 同源）；
        #    att 无 markdown → 全文留空，outline/section 优雅降级（绝不借另一附件的全文）。
        #  - 无任何结构附件(content_list 本就为空)：才允许取最新带 markdown 的附件供 outline/section，
        #    此时空 content_list 与该全文不存在坐标冲突。
        if att is not None:
            md_att = att if (att.markdown_path and att.sha256) else None
        else:
            md_att = next((a for a in atts if a.markdown_path and a.sha256), None)
        # 路径安全护栏（对齐 main.py:1581-1598）：父目录 fulltext 或 sciverse/<paperId>，
        # 文件名恰为 <att.sha256>.md。任一不符即不读（防任意文件读取）。
        md_failed = False  # 有 markdown 附件但护栏拦截/读盘失败（F-12 markdown_unreadable）
        if md_att and md_att.markdown_path and md_att.sha256:
            try:
                md_file = Path(md_att.markdown_path).resolve()
                allowed_parent = (
                    md_file.parent.name == "fulltext"
                    or (md_file.parent.name == str(paper_id)
                        and md_file.parent.parent.name == "sciverse")
                )
                if md_file.is_file() and allowed_parent and md_file.name == f"{md_att.sha256}.md":
                    full_md = md_file.read_text(encoding="utf-8")
                else:
                    md_failed = True
            except Exception:  # noqa: BLE001 — 读盘失败按无全文
                logger.warning("[read_paper] markdown 读取失败 paper=%s path=%s", paper_id, md_att.markdown_path)
                md_failed = True
                full_md = ""

        if not content_list and not full_md:
            return None, "markdown_unreadable" if md_failed else "no_attachment"
        return {"full_md": full_md, "content_list": content_list, "page_map": page_map}, None
