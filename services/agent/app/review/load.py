"""项目语料加载 — 把一个 project 的 included 论文加载为 (paper_markdowns, records)。

P3-2 提炼自 scripts/run_slr_e2e.py 第 370-413 行的加载逻辑，做成可复用函数，供
ReviewTool 调用（P3-3 e2e 也复用）。

关键点（P3-2 文档内容溯源）：
  - records 每条新增 content_sha256 字段 = 该论文全文文档的 sha256
    = Attachment.sha256 = markdown 文件名 stem。
  - records 的 idx 1-based，与综述里的 [n] 引用编号严格对齐。
  - markdown 全文从 Attachment.markdown_path 读盘（缺失/读失败则空串，不拖垮综述）。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Attachment, DocumentStructure
from ..repositories.library import get_by_id
from ..repositories.project import list_project_papers

logger = logging.getLogger("agent.review.load")


def _authors_str(creators: list | None) -> str:
    """把 CSL creators 数组拼成作者字符串（与 e2e 脚本同口径）。"""
    creators = creators or []
    if creators and isinstance(creators[0], dict):
        return "; ".join(
            c.get("literal") or f"{c.get('family', '')} {c.get('given', '')}".strip()
            for c in creators
        )
    return str(creators) if creators else ""


async def _attachment_for_paper(s: AsyncSession, paper_id: int) -> Attachment | None:
    """取某论文最新一条已解析（有 markdown_path）的 Attachment；无则取任意一条。"""
    q = (
        select(Attachment)
        .where(Attachment.paper_id == paper_id)
        .order_by(Attachment.id.desc())
    )
    rows = (await s.execute(q)).scalars().all()
    if not rows:
        return None
    # 优先有 markdown_path 的
    for a in rows:
        if a.markdown_path:
            return a
    return rows[0]


async def load_project_corpus(
    s: AsyncSession,
    project_id: int,
) -> tuple[list[dict], list[dict], list[dict]]:
    """加载 project 的 included 论文 → (paper_markdowns, records, skipped)。

    Args:
        s:          AsyncSession。
        project_id: 项目 id。

    Returns:
        (paper_markdowns, records, skipped)
          paper_markdowns: [{meta: {paper_id, title, authors, year}, markdown}, ...]
          records:         [{idx, paper_id, title, authors, year, doi, content_sha256}, ...]
                           idx 1-based，与综述 [n] 引用对齐；paper_id 为真实 DB id；
                           content_sha256 用于 EvidenceRef 文档内容溯源（命中时透传进
                           EvidenceRef）。
          skipped:         被跳过的 included 论文清单
                           [{paper_id, title, reason}, ...]，供调用方报告。

    Notes:
        - 仅纳入 inclusion_status == "included" 的论文。
        - codex P1-4：无可读 markdown（空串）或无 content_sha256 的 included 论文一律
          *跳过*（不进 paper_markdowns/records），记入 skipped。绝不喂空 markdown 进语料
          （静默垃圾输入 + 无溯源证据，破坏可信综述）。
        - markdown 从 Attachment.markdown_path 读盘；读失败 → 跳过（不再静默喂空串）。
        - content_sha256 = Attachment.sha256（即全文文档内容哈希）。
    """
    pairs = await list_project_papers(s, project_id)
    included = [(pp, paper) for (pp, paper) in pairs if pp.inclusion_status == "included"]

    paper_markdowns: list[dict] = []
    records: list[dict] = []
    skipped: list[dict] = []

    idx = 0
    for pp, paper in included:
        # paper 已由 join 取出；为兼容仅传 id 的旧路径也可重取，但此处直接用 join 结果
        if paper is None:
            paper = await get_by_id(s, pp.paper_id)
            if paper is None:
                skipped.append({
                    "paper_id": pp.paper_id, "title": "", "reason": "paper_not_found",
                })
                continue

        attachment = await _attachment_for_paper(s, paper.id)
        content_sha256 = attachment.sha256 if attachment else None

        # 读 markdown 全文
        markdown_text = ""
        if attachment and attachment.markdown_path:
            try:
                markdown_text = Path(attachment.markdown_path).read_text(encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                logger.warning("读 markdown 失败 paper_id=%s: %s", paper.id, exc)
                markdown_text = ""

        # codex P1-4：无可读 markdown 或无 content_sha256 → 跳过（不喂空 markdown 进语料）
        if not markdown_text.strip():
            logger.warning(
                "跳过无可读 markdown 的 included 论文 paper_id=%s（不喂空 markdown）", paper.id
            )
            skipped.append({
                "paper_id": paper.id, "title": paper.title or "", "reason": "empty_markdown",
            })
            continue
        if not content_sha256:
            logger.warning(
                "跳过无 content_sha256 的 included 论文 paper_id=%s（无溯源证据）", paper.id
            )
            skipped.append({
                "paper_id": paper.id, "title": paper.title or "", "reason": "missing_content_sha256",
            })
            continue

        authors_str = _authors_str(paper.creators)

        # B4a 溯源定位：取该附件的 DocumentStructure.content_list（无则 None，定位被跳过）。
        content_list = None
        if attachment is not None:
            ds = (
                await s.execute(
                    select(DocumentStructure).where(
                        DocumentStructure.attachment_id == attachment.id
                    )
                )
            ).scalar_one_or_none()
            content_list = ds.content_list if ds else None

        idx += 1
        paper_markdowns.append({
            "meta": {
                "paper_id": str(paper.id),
                "title": paper.title or "",
                "authors": authors_str,
                "year": paper.year,
            },
            "markdown": markdown_text,
            "content_list": content_list,
        })
        records.append({
            "idx": idx,
            # codex P1-3：真实 DB paper.id，供 EvidenceRef.paper_id 可靠回指库内论文
            # （idx 仅作 [n] 引用对齐用，非 DB 主键）。
            "paper_id": paper.id,
            # B4b/B4c：附件 id，供 provenance_map 条目携带（前端可凭此打开原文档/结构端点）。
            "attachment_id": attachment.id if attachment else None,
            "title": paper.title or "",
            "authors": authors_str,
            "year": str(paper.year or ""),
            "doi": paper.doi or "",
            # P3-2 文档内容溯源：该论文全文文档的 sha256
            "content_sha256": content_sha256,
        })

    return paper_markdowns, records, skipped


async def project_corpus_content_hashes(
    s: AsyncSession,
    project_id: int,
) -> set[str]:
    """取 project 的 included 论文源文档内容哈希集合（轻量，不读 markdown 全文）。

    与 load_project_corpus 同口径：每篇 included 论文用 _attachment_for_paper 取附件，
    收集 attachment.sha256（即源文档 content_sha256 = EvidenceRef.source_content_sha256
    的值域）。仅当附件存在、markdown_path 非空、sha256 非空时纳入——与 load 路径一致
    （只有可读 markdown 的论文才会进语料并产证据）。

    用途：供 grounding_metrics 的 provenance_hit_rate 判定 EvidenceRef 是否溯源命中。
    corpus_hashes 必须取此同源哈希，否则永远命不中（详见 load.py:82/103/154）。

    注（codex P2）：本 helper 不实际打开 markdown 文件校验可读性（load_project_corpus 会，
    并跳过读失败的论文），故返回集合是其超集——对真实证据无影响（真实 EvidenceRef 只可能
    来自已成功加载的可读论文，其 sha 必在本集合内），仅在极端情况下放宽命中判定；
    刻意不读盘以保持本端点轻量（避免每次 grounding 查询读全部 included 全文）。

    Args:
        s:          AsyncSession。
        project_id: 项目 id。

    Returns:
        sha256 字符串集合（source_content_sha256 值域）。included 论文均无附件时为空集。
    """
    pairs = await list_project_papers(s, project_id)
    included = [(pp, paper) for (pp, paper) in pairs if pp.inclusion_status == "included"]

    hashes: set[str] = set()
    for pp, paper in included:
        paper_id = paper.id if paper is not None else pp.paper_id
        attachment = await _attachment_for_paper(s, paper_id)
        # 仅当附件存在且 markdown_path 非空且 sha256 非空（与 load_project_corpus 同口径，
        # 不读 markdown 全文 → 轻量）
        if attachment and attachment.markdown_path and attachment.sha256:
            hashes.add(attachment.sha256)
    return hashes


async def has_readable_fulltext(s: AsyncSession, project_id: int) -> bool:
    """轻量预检：项目是否至少有 1 篇 included 论文有可读全文（与 load_project_corpus 同口径）。

    短路：找到第一篇 markdown_path 可读且 strip 非空 + 有 sha256 的 included 论文即返回 True，
    不读全部全文（避免 discover 端点同步阻塞）。供 discover 端点在建 job 前做快速失败预检——
    无可读全文则立即 400，不浪费一次异步 run + LLM 调用。

    刻意不用 project_corpus_content_hashes（那不读盘验证可读性，是 load 的超集/假阳性，
    codex 二审项）：本 helper 实际 read_text().strip() 校验首篇可读，与 load 跳过口径一致。
    """
    pairs = await list_project_papers(s, project_id)
    for pp, paper in pairs:
        if pp.inclusion_status != "included":
            continue
        paper_id = paper.id if paper is not None else pp.paper_id
        attachment = await _attachment_for_paper(s, paper_id)
        if not (attachment and attachment.markdown_path and attachment.sha256):
            continue
        try:
            if Path(attachment.markdown_path).read_text(encoding="utf-8").strip():
                return True
        except Exception:  # noqa: BLE001 — 读失败视为不可读，继续找下一篇
            continue
    return False
