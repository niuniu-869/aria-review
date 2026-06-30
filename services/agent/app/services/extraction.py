"""结构化抽取服务：用 LLM 从已 OCR 的 Markdown 全文抽取研究要素。

设计原则：
- 抽取 research_question/method/findings/dataset/contribution 五字段。
- 幂等 upsert：重复调用同一篇只更新不重复建行。
- 逐篇 try/except + rollback 隔离（Phase2 教训：DB 错误后 rollback 防级联）。
- LLM 返回非 JSON 或解析失败 → 记为 failed，不抛出异常。
- 无 markdown_path / OCR 未完成 → 记为 skipped。
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Attachment, Paper
from ..prompts import prompt_extract_structured
from ..repositories.extraction import upsert_extraction
from ..services.metadata_backfill import _parse_llm_json  # 复用同款健壮 JSON 解析

logger = logging.getLogger(__name__)

_HEAD_CHARS = 10000  # 读取 Markdown 首部字符数（W5-b 比 W5-a 多读，提升抽取覆盖率）

_EXTRACT_FIELDS = ("research_question", "method", "findings", "dataset", "contribution")


async def extract_paper_structured(
    s: AsyncSession,
    llm,
    paper: Paper,
) -> dict:
    """对单篇 paper 用 LLM 结构化抽取研究要素，upsert 到 paper_extraction。

    Args:
        s:     AsyncSession（调用方管理事务，本函数负责 commit 或 rollback）。
        llm:   LLM 客户端（实现 `complete(messages) -> str`）。
        paper: Paper ORM 对象。

    Returns:
        {"status": "extracted"|"skipped"|"failed", "reason": str | None}
    """
    # 1. 取 Attachment.markdown_path（mineru_status=done）
    att_q = (
        select(Attachment)
        .where(
            Attachment.paper_id == paper.id,
            Attachment.mineru_status == "done",
            Attachment.markdown_path.isnot(None),
        )
        .limit(1)
    )
    try:
        att = (await s.execute(att_q)).scalar_one_or_none()
    except Exception as exc:
        logger.warning("paper %d: 查询 attachment 失败: %s", paper.id, exc)
        try:
            await s.rollback()
        except Exception:
            pass
        return {"status": "failed", "reason": f"查询 attachment 失败: {exc}"}

    if att is None or not att.markdown_path:
        return {"status": "skipped", "reason": "无 OCR-done 的 markdown"}

    # 2. 读文件首部
    md_path = Path(att.markdown_path)
    if not md_path.exists():
        return {"status": "skipped", "reason": f"markdown 文件不存在: {md_path}"}

    try:
        head = md_path.read_text(encoding="utf-8")[:_HEAD_CHARS]
    except Exception as exc:
        return {"status": "skipped", "reason": f"读取 markdown 失败: {exc}"}

    if not head.strip():
        return {"status": "skipped", "reason": "markdown 内容为空"}

    # 3. 调用 LLM
    try:
        raw = await llm.complete(prompt_extract_structured(head))
    except Exception as exc:
        logger.warning("paper %d: LLM 调用失败: %s", paper.id, exc)
        try:
            await s.rollback()
        except Exception:
            pass
        return {"status": "failed", "reason": f"LLM 调用失败: {exc}"}

    # 4. 解析 JSON（复用 metadata_backfill 的健壮解析）
    parsed = _parse_llm_json(raw)
    if parsed is None:
        logger.warning("paper %d: LLM 返回非 JSON: %r", paper.id, raw[:200])
        return {"status": "failed", "reason": "LLM 返回非 JSON"}

    # 5. 提取五字段（缺则 None，不编造）
    fields: dict = {
        f: (parsed.get(f) if isinstance(parsed.get(f), str) else None)
        for f in _EXTRACT_FIELDS
    }
    fields["raw"] = parsed  # 备份原始 LLM 返回

    # 6. upsert
    try:
        model_name = getattr(llm, "model", None) or getattr(llm, "_model", None)
        await upsert_extraction(s, paper.id, fields, model=model_name)
        await s.commit()
        logger.info(
            "paper %d: 结构化抽取完成（rq=%s method=%s findings=%s dataset=%s contribution=%s）",
            paper.id,
            bool(fields.get("research_question")),
            bool(fields.get("method")),
            bool(fields.get("findings")),
            bool(fields.get("dataset")),
            bool(fields.get("contribution")),
        )
        return {"status": "extracted", "reason": None}
    except Exception as exc:
        logger.error("paper %d: DB upsert 失败: %s", paper.id, exc)
        try:
            await s.rollback()
        except Exception:
            pass
        return {"status": "failed", "reason": f"DB upsert 失败: {exc}"}
