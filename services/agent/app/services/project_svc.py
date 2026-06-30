"""Project service 层: 将 repo 调用封装为前端/agent 共用的 DTO。

REST 端点与 agent 工具均通过此层操作，保证业务逻辑单一入口。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import ApiError
from ..models import Corpus, Paper, PaperExtraction, ProjectPaper
from ..repositories import corpus as corpus_repo
from ..repositories import library as lib_repo
from ..repositories import project as proj_repo
from ..repositories.library import (
    RANK_TO_STATUS,
    attachment_status_subquery,
)

_VALID_STATUSES = frozenset({"candidate", "included", "excluded", "maybe"})


async def list_projects_dto(s: AsyncSession) -> list[dict]:
    """列出所有项目，返回精简 DTO [{id, name, createdAt}]。"""
    projects = await proj_repo.list_projects(s)
    return [
        {
            "id": p.id,
            "name": p.name,
            "createdAt": p.created_at.isoformat() if p.created_at else None,
        }
        for p in projects
    ]


async def create_project_dto(
    s: AsyncSession,
    name: str,
    research_question: str | None = None,
    description: str | None = None,
) -> dict:
    """创建项目，返回 DTO {id, name, createdAt}。"""
    data = {"name": name}
    if research_question is not None:
        data["research_question"] = research_question
    if description is not None:
        data["description"] = description
    try:
        proj = await proj_repo.create_project(s, data)
    except IntegrityError as exc:
        await s.rollback()
        raise ApiError(409, "PROJECT_NAME_EXISTS", f"项目名称已存在: {name}") from exc
    return {
        "id": proj.id,
        "name": proj.name,
        "createdAt": proj.created_at.isoformat() if proj.created_at else None,
    }


async def get_project_dto(s: AsyncSession, project_id: int) -> dict | None:
    """取单项目详情 DTO，含 paperCount/includedCount/activeCorpus。不存在返回 None。"""
    proj = await proj_repo.get_project(s, project_id)
    if proj is None:
        return None
    # 统计文献数
    pairs = await proj_repo.list_project_papers(s, project_id)
    paper_count = len(pairs)
    included_count = sum(1 for pp, _ in pairs if pp.inclusion_status == "included")

    # M2: 计算 active corpus 及 stale 状态
    active_corpus = await _get_active_corpus_dto(s, project_id)

    return {
        "id": proj.id,
        "name": proj.name,
        "researchQuestion": proj.research_question,
        "description": proj.description,
        "paperCount": paper_count,
        "includedCount": included_count,
        "activeCorpus": active_corpus,
    }


async def _get_active_corpus_dto(s: AsyncSession, project_id: int) -> dict | None:
    """计算项目的 active corpus（最新 ready corpus）及 stale 状态。

    active = 该项目最新 ready 的 corpus（按 id 降序取首条）。
    stale  = 当前 included 集合的 content_hash ≠ active corpus 的 content_hash。

    stale 判定直接复用 corpus_repo 的 _record_hash/_content_hash，不重复实现。
    """
    # 取最新 ready corpus
    q = (
        select(Corpus)
        .where(Corpus.project_id == project_id, Corpus.status == "ready")
        .order_by(Corpus.id.desc())
        .limit(1)
    )
    active: Corpus | None = (await s.execute(q)).scalar_one_or_none()
    if active is None:
        return None

    # 计算当前 included 集合的 content_hash（与 build_corpus_snapshot 相同逻辑）
    q_pp = (
        select(ProjectPaper, Paper)
        .join(Paper, Paper.id == ProjectPaper.paper_id)
        .where(
            ProjectPaper.project_id == project_id,
            ProjectPaper.inclusion_status == "included",
        )
    )
    rows = (await s.execute(q_pp)).all()
    papers: list[Paper] = [r[1] for r in rows]

    if papers:
        import hashlib
        record_hashes = [corpus_repo._record_hash(p) for p in papers]
        current_hash = corpus_repo._content_hash(record_hashes)
    else:
        import hashlib
        current_hash = hashlib.sha256(b"empty").hexdigest()

    stale = current_hash != active.content_hash

    return {
        "corpusId": active.id,
        "rCorpusId": active.r_corpus_id,
        "status": active.status,
        "documentCount": active.document_count,
        "contentHash": active.content_hash,
        "stale": stale,
    }


async def list_project_papers_dto(s: AsyncSession, project_id: int) -> list[dict]:
    """列出项目文献，返回带附件/OCR/元数据状态/结构化抽取状态字段的 DTO 列表（单查询，无 N+1）。

    通过 attachment_status_subquery() LEFT JOIN 一次性注入 hasPdf/ocrStatus/hasAbstract，
    再 LEFT JOIN paper_extraction 注入 hasExtraction，不逐篇再查。
    """
    att = attachment_status_subquery()
    q = (
        select(
            ProjectPaper,
            Paper,
            att.c.att_count,
            att.c.best_ocr_rank,
            PaperExtraction.id.label("ext_id"),
        )
        .join(Paper, Paper.id == ProjectPaper.paper_id)
        .join(att, att.c.paper_id == Paper.id, isouter=True)
        .join(PaperExtraction, PaperExtraction.paper_id == Paper.id, isouter=True)
        .where(ProjectPaper.project_id == project_id)
        .order_by(ProjectPaper.order.asc(), ProjectPaper.id.asc())
    )
    rows = (await s.execute(q)).all()
    return [
        {
            "paperId": paper.id,
            "title": paper.title,
            "year": paper.year,
            "inclusionStatus": pp.inclusion_status,
            "screeningScore": pp.screening_score,
            "hasAbstract": bool(paper.abstract),
            "hasPdf": (att_count or 0) > 0,
            "ocrStatus": RANK_TO_STATUS.get(best_ocr_rank or 0, "none"),
            "hasExtraction": ext_id is not None,
        }
        for pp, paper, att_count, best_ocr_rank, ext_id in rows
    ]


async def get_paper_detail_dto(
    s: AsyncSession,
    project_id: int,
    paper_id: int,
) -> dict:
    """取单篇文献详情（含 tags/notes/纳排状态/结构化抽取）。不存在则 ApiError 404。"""
    from ..repositories.extraction import get_extraction

    pp = await proj_repo.find_project_paper(s, project_id, paper_id)
    if pp is None:
        raise ApiError(404, "PROJECT_PAPER_NOT_FOUND",
                       f"文献 {paper_id} 未关联到项目 {project_id}")
    paper = await lib_repo.get_paper_with_relations(s, paper_id)
    if paper is None:
        raise ApiError(404, "PAPER_NOT_FOUND", f"文献 {paper_id} 不存在")

    # W5-b：填充结构化抽取结果
    ext = await get_extraction(s, paper_id)
    extraction_dto = None
    if ext is not None:
        extraction_dto = {
            "researchQuestion": ext.research_question,
            "method": ext.method,
            "findings": ext.findings,
            "dataset": ext.dataset,
            "contribution": ext.contribution,
        }

    return {
        "paperId": paper.id,
        "title": paper.title,
        "creators": paper.creators or [],
        "doi": paper.doi,
        "abstract": paper.abstract,
        "tags": [tag.name for tag in getattr(paper, "_tags", [])],
        "notes": [
            {"id": n.id, "body": n.body,
             "createdAt": n.created_at.isoformat() if n.created_at else None}
            for n in getattr(paper, "_notes", [])
        ],
        "inclusionStatus": pp.inclusion_status,
        "extraction": extraction_dto,
    }


async def update_inclusion_dto(
    s: AsyncSession,
    project_id: int,
    paper_id: int,
    status: str,
    reason: str | None = None,
    score: int | None = None,
) -> dict:
    """更新文献纳排状态，返回更新后的 ProjectPaperItem DTO。

    先验证状态合法，再 find_project_paper 查关联，找到后 set_inclusion。
    """
    if status not in _VALID_STATUSES:
        raise ApiError(
            400, "VALIDATION_ERROR",
            f"inclusion_status 非法: {status!r}，合法值: {sorted(_VALID_STATUSES)}"
        )
    pp = await proj_repo.find_project_paper(s, project_id, paper_id)
    if pp is None:
        raise ApiError(404, "PROJECT_PAPER_NOT_FOUND",
                       f"文献 {paper_id} 未关联到项目 {project_id}")
    updated_pp = await proj_repo.set_inclusion(s, pp.id, status, reason, score)
    # 取 paper 以便返回 title/year/hasAbstract
    paper = await lib_repo.get_by_id(s, paper_id)
    # 查附件状态（hasPdf/ocrStatus），复用 attachment_status_subquery 单篇查询
    att = attachment_status_subquery()
    from sqlalchemy import select as _select
    att_row = (
        await s.execute(
            _select(att.c.att_count, att.c.best_ocr_rank)
            .where(att.c.paper_id == paper_id)
        )
    ).first()
    att_count = att_row[0] if att_row else 0
    best_ocr_rank = att_row[1] if att_row else 0
    return {
        "paperId": paper_id,
        "title": paper.title if paper else None,
        "year": paper.year if paper else None,
        "inclusionStatus": updated_pp.inclusion_status,
        "screeningScore": updated_pp.screening_score,
        "hasAbstract": bool(paper.abstract) if paper else False,
        "hasPdf": (att_count or 0) > 0,
        "ocrStatus": RANK_TO_STATUS.get(best_ocr_rank or 0, "none"),
    }
