"""B5: 轻量语料质检报告（确定性，非 LLM）。

对项目内论文做一次确定性 SQL 扫描，标记可由前端展示的数据质量问题：
  - missing_metadata : 缺 abstract / creators(空) / year
  - duplicate        : 归一 (title, year, doi) 三元组在项目内撞车（同组每篇都标记）
  - not_parsed       : 无 mineru_status=="done" 的 Attachment（未 OCR 解析）
  - extraction_missing: 已解析（有 done 附件）但无 PaperExtraction

不调用任何 LLM；批量查询避免 N+1。
"""
from __future__ import annotations

import re
from collections import defaultdict

from sqlalchemy import select

from ..models import Attachment, PaperExtraction
from ..repositories.project import list_project_papers

_ISSUE_TYPES = (
    "missing_metadata",
    "duplicate",
    "not_parsed",
    "extraction_missing",
)

# DOI 归一：剥常见前缀(https://doi.org/ 等) + 去空白 + 小写，使同一 DOI 的变体能归并(codex P2)。
_DOI_PREFIX_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


def _norm_doi(doi) -> str:
    return _DOI_PREFIX_RE.sub("", str(doi or "").strip()).strip().lower()


def _norm_title(title) -> str:
    return _WS_RE.sub(" ", str(title or "").strip().lower())


async def build_quality_report(session, project_id: int) -> dict:
    """扫描项目内论文，产出确定性质检报告（非 LLM）。

    Returns: {"total": int, "issues": [{"paper_id", "type", "detail"}],
              "by_type": {type: count}}
    """
    pairs = await list_project_papers(session, project_id)
    papers = [paper for (_pp, paper) in pairs]
    total = len(papers)

    issues: list[dict] = []
    by_type: dict[str, int] = {t: 0 for t in _ISSUE_TYPES}

    if not papers:
        return {"total": total, "issues": issues, "by_type": by_type}

    paper_ids = [p.id for p in papers]

    # 批量加载：已解析(OCR done) paper_id 集合
    parsed_ids = set(
        (
            await session.execute(
                select(Attachment.paper_id).where(
                    Attachment.paper_id.in_(paper_ids),
                    Attachment.mineru_status == "done",
                )
            )
        ).scalars().all()
    )
    # 批量加载：已有结构化抽取的 paper_id 集合
    extracted_ids = set(
        (
            await session.execute(
                select(PaperExtraction.paper_id).where(
                    PaperExtraction.paper_id.in_(paper_ids)
                )
            )
        ).scalars().all()
    )

    # 重复检测：归一 (title, year, doi) 分组（仅 title 非空者参与，避免空标题平凡撞车）
    groups: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for p in papers:
        title = _norm_title(p.title)
        if not title:
            continue
        key = (title, str(p.year or ""), _norm_doi(p.doi))
        groups[key].append(p.id)
    dup_ids: dict[int, int] = {}  # paper_id -> 组大小
    for members in groups.values():
        if len(members) >= 2:
            for pid in members:
                dup_ids[pid] = len(members)

    for p in papers:
        # missing_metadata
        missing: list[str] = []
        if not str(p.abstract or "").strip():  # 含纯空白 abstract 也判缺(codex P3)
            missing.append("abstract")
        if not p.creators:
            missing.append("creators")
        if p.year is None:
            missing.append("year")
        if missing:
            issues.append({
                "paper_id": p.id,
                "type": "missing_metadata",
                "detail": "缺少: " + ", ".join(missing),
            })

        # duplicate
        if p.id in dup_ids:
            issues.append({
                "paper_id": p.id,
                "type": "duplicate",
                "detail": f"与项目内 {dup_ids[p.id]} 篇题录重复(同 title+year+doi)",
            })

        # not_parsed
        if p.id not in parsed_ids:
            issues.append({
                "paper_id": p.id,
                "type": "not_parsed",
                "detail": "无已解析(OCR done)附件",
            })
        # extraction_missing（已解析但无抽取）
        elif p.id not in extracted_ids:
            issues.append({
                "paper_id": p.id,
                "type": "extraction_missing",
                "detail": "已解析但无结构化抽取",
            })

    for i in issues:
        by_type[i["type"]] += 1

    issues.sort(key=lambda i: (i["paper_id"], i["type"]))

    return {"total": total, "issues": issues, "by_type": by_type}
