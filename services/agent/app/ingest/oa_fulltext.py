"""OA 全文溯源贯通 (M4)：把多源候选的开放获取 PDF 接进既有信任闸门。

链路：候选 pdfUrl（或经 Unpaywall 用 DOI 懒加载补链）→ resolve_pdf 安全下载 →
MinerU 结构化解析 → 挂到**既有 Paper**（Attachment + DocumentStructure 页/块锚点）。
之后 cite_check/溯源与上传/Sciverse 全文一视同仁。信任闸门原地不动，本模块只做接线。

只对**入选**候选运行（导入后 Agent/人点选），不对全部候选跑——防 MinerU 配额爆。
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Attachment, Paper
from ..sources.unpaywall import UnpaywallClient
from .fulltext import _save_markdown, _upsert_document_structure
from .mineru import parse_pdfs
from .pdf_download import resolve_pdf

logger = logging.getLogger("agent.ingest.oa_fulltext")


async def resolve_oa_pdf_url(
    pdf_url: str | None,
    doi: str | None,
    *,
    _http_client=None,
) -> tuple[str | None, str | None]:
    """确定要下载的 OA PDF 链接。返回 (url, source)。

    优先候选自带 pdfUrl；否则若有 DOI，走 Unpaywall `/v2/{doi}` 懒加载补链 (§4.6)。
    命中率非满 (OA 现实)，拿不到返回 (None, reason)。
    """
    if pdf_url and pdf_url.strip():
        return pdf_url.strip(), "candidate"
    doi = (doi or "").strip()
    if not doi:
        return None, "无 pdfUrl 且无 DOI"
    hit = await UnpaywallClient(client=_http_client).lookup(doi)
    if hit and hit.pdf_url:
        return hit.pdf_url, "unpaywall"
    return None, "Unpaywall 未命中 OA PDF"


async def fetch_and_store_oa_fulltext(
    paper_id: int,
    *,
    pdf_url: str | None = None,
    doi: str | None = None,
    language: str = "en",
    session: AsyncSession,
    _mineru_client=None,
    _http_client=None,
) -> dict:
    """把既有 paper 的 OA PDF 安全下载 + 解析 + 挂结构 (溯源贯通)。

    返回 {status, ...}。status ∈ done/skipped/rejected/failed。任一步失败返回原因，不抛
    （远程下载/OA 命中失败是常态）。已挂过同 sha 附件则复用不重解析。
    """
    paper = (await session.execute(select(Paper).where(Paper.id == paper_id))).scalar_one_or_none()
    if paper is None:
        return {"status": "skipped", "paper_id": paper_id, "reason": "paper 不存在"}

    resolved_url, url_source = await resolve_oa_pdf_url(pdf_url, doi, _http_client=_http_client)
    if not resolved_url:
        return {"status": "skipped", "paper_id": paper_id, "reason": url_source}

    # 安全下载 (SSRF/重定向/大小/魔数闸门在 resolve_pdf 内)。
    asset = await resolve_pdf(resolved_url, client=_http_client)
    if not asset.ok:
        return {"status": "rejected", "paper_id": paper_id, "reason": asset.reject_reason,
                "pdf_url": resolved_url}

    # 已有同 sha256 的**完整**全文附件 (mineru_status=done + markdown_path) → 复用，
    # 不重复解析 (幂等省 MinerU 配额)。pending/failed/缺 markdown 的旧附件不算，避免误报
    # done 而实际无可读全文/页块锚点 (codex P2)。
    existing = (await session.execute(
        select(Attachment).where(
            Attachment.paper_id == paper_id,
            Attachment.sha256 == asset.sha256,
            Attachment.mineru_status == "done",
            Attachment.markdown_path.isnot(None),
            Attachment.markdown_path != "",  # 空串也不算有效全文 (对齐仓库其他路径口径)
        )
    )).scalar_one_or_none()
    if existing is not None:
        return {"status": "done", "paper_id": paper_id, "attachment_id": existing.id,
                "pdf_url": resolved_url, "url_source": url_source, "reused": True}

    # MinerU 结构化解析。批级异常 (submit/HTTP/poll 失败) 转 failed，不抛——否则
    # IngestTool 整批循环会中断、后续 paper 不处理 (codex P1，对齐 ingest_pdfs)。
    try:
        results = await parse_pdfs([Path(asset.path)], language=language, max_files=1,
                                  _client=_mineru_client)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[oa_fulltext] MinerU 解析异常（转 failed）: %r", exc)
        return {"status": "failed", "paper_id": paper_id,
                "reason": f"MinerU 解析异常: {exc}", "pdf_url": resolved_url}
    result = results[0] if results else {}
    if result.get("status") != "done" or not result.get("markdown"):
        return {"status": "failed", "paper_id": paper_id,
                "reason": result.get("err", "MinerU 返回 status != done"), "pdf_url": resolved_url}

    markdown = result["markdown"]
    md_path = _save_markdown(asset.sha256, markdown)
    attachment = Attachment(
        paper_id=paper_id,
        path=asset.path,
        content_type="application/pdf",
        sha256=asset.sha256,
        mineru_status="done",
        markdown_path=str(md_path),
    )
    session.add(attachment)
    await session.commit()
    await session.refresh(attachment)
    attachment_id = attachment.id

    # DocumentStructure 页/块锚点落库（失败不阻断——附件已存）。
    content_list = result.get("content_list")
    if content_list:
        try:
            await _upsert_document_structure(session, attachment_id, asset.sha256, markdown, content_list)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[oa_fulltext] DocumentStructure 落库失败（不阻断）: %r", exc)
            await session.rollback()

    return {
        "status": "done",
        "paper_id": paper_id,
        "attachment_id": attachment_id,
        "pdf_url": resolved_url,
        "url_source": url_source,
        "markdown_len": len(markdown),
        "sha256": asset.sha256,
    }
