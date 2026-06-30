"""元数据补全服务：用 LLM 从已 OCR 的 Markdown 全文首部回填缺失题录字段。

设计原则：
- 仅回填当前为空的字段（abstract/creators/year/keywords），不覆盖已有内容。
- 逐篇 try/except + rollback 隔离（Phase2 教训：DB 错误后 rollback 防级联）。
- LLM 返回非 JSON 或解析失败 → 记为 failed，不抛出异常。
- 无 markdown_path / OCR 未完成 → 记为 skipped。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Attachment, Paper
from ..prompts import prompt_extract_metadata

logger = logging.getLogger(__name__)

_HEAD_CHARS = 6000  # 读取 Markdown 首部字符数


def _parse_llm_json(raw: str) -> dict | None:
    """健壮解析 LLM 返回的 JSON 字符串。失败返回 None。

    解析策略（依次尝试）：
    1. 直接 json.loads 整串（最快路径，应对纯 JSON 输入）。
    2. 花括号配对计数：从每个 '{' 起逐字符计 depth（遇 '{' +1、遇 '}' -1），
       depth 降到 0 时截取，再 json.loads。可正确处理"JSON 后跟说明文字"、
       "前缀说明 + JSON"、"说明文字 {非JSON} ... {真JSON}" 等 LLM 常见格式。
       第一个块解析失败后继续扫描下一个块，直到成功或耗尽全部 '{' 为止。
    3. 两步均失败 → 返回 None（不抛，保持契约不变）。
    """
    if not raw:
        return None

    # 步骤 1：整串尝试（无任何额外处理，应对纯 JSON）
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass

    # 步骤 2：花括号配对计数，逐块扫描所有完整 JSON 对象直到成功
    search_from = 0
    while True:
        start = raw.find("{", search_from)
        if start == -1:
            return None

        depth = 0
        in_string = False
        escape_next = False
        end_pos = -1

        for i in range(start, len(raw)):
            ch = raw[i]
            if escape_next:
                escape_next = False
                continue
            if ch == "\\" and in_string:
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end_pos = i
                    break

        if end_pos == -1:
            # 未找到匹配的闭括号，整段无有效完整对象
            return None

        blob = raw[start : end_pos + 1]
        try:
            obj = json.loads(blob)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass

        # 此块解析失败，从下一个 '{' 继续搜索
        search_from = end_pos + 1


async def backfill_paper_metadata(
    s: AsyncSession,
    llm,
    paper: Paper,
) -> dict:
    """对单篇 paper 用 LLM 回填缺失的元数据字段。

    Args:
        s:     AsyncSession（调用方管理事务，本函数负责 commit 或 rollback）。
        llm:   LLM 客户端（实现 `complete(messages) -> str`）。
        paper: Paper ORM 对象。

    Returns:
        {"status": "updated"|"skipped"|"failed", "reason": str | None}
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
        from ..llm import LLMError
        raw = await llm.complete(prompt_extract_metadata(head))
    except Exception as exc:
        logger.warning("paper %d: LLM 调用失败: %s", paper.id, exc)
        try:
            await s.rollback()
        except Exception:
            pass
        return {"status": "failed", "reason": f"LLM 调用失败: {exc}"}

    # 4. 解析 JSON
    parsed = _parse_llm_json(raw)
    if parsed is None:
        logger.warning("paper %d: LLM 返回非 JSON: %r", paper.id, raw[:200])
        return {"status": "failed", "reason": "LLM 返回非 JSON"}

    # 5. 仅回填当前为空的字段
    try:
        updated_any = False

        # abstract: 空才填
        if not paper.abstract:
            new_abstract = parsed.get("abstract")
            if new_abstract and isinstance(new_abstract, str) and new_abstract.strip():
                paper.abstract = new_abstract.strip()
                updated_any = True

        # creators: 空才填（creators 为 None 或空列表）
        if not paper.creators:
            new_authors = parsed.get("authors")
            if new_authors and isinstance(new_authors, list):
                creators = [
                    {"literal": str(a).strip()}
                    for a in new_authors
                    if str(a).strip()
                ]
                if creators:
                    paper.creators = creators
                    updated_any = True

        # year: 空才填
        if paper.year is None:
            new_year = parsed.get("year")
            if isinstance(new_year, (int, float)) and not isinstance(new_year, bool):
                year_int = int(new_year)
                if 1500 <= year_int <= 2100:
                    paper.year = year_int
                    updated_any = True

        # keywords: 空才填
        if not paper.keywords:
            new_kws = parsed.get("keywords")
            if new_kws and isinstance(new_kws, list):
                kw_str = "; ".join(str(k).strip() for k in new_kws if str(k).strip())
                if kw_str:
                    paper.keywords = kw_str
                    updated_any = True

        if updated_any:
            s.add(paper)
            await s.commit()
            await s.refresh(paper)
            logger.info("paper %d: 元数据已回填（abstract=%s creators=%s year=%s kws=%s）",
                        paper.id,
                        bool(paper.abstract),
                        bool(paper.creators),
                        paper.year,
                        bool(paper.keywords))
            return {"status": "updated", "reason": None}
        else:
            return {"status": "skipped", "reason": "LLM 未返回可回填的新字段"}

    except Exception as exc:
        logger.error("paper %d: DB 更新失败: %s", paper.id, exc)
        try:
            await s.rollback()
        except Exception:
            pass
        return {"status": "failed", "reason": f"DB 更新失败: {exc}"}
