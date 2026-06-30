"""Corpus 仓储: 从 Project included 论文构建冻结快照。

build_corpus_snapshot 逻辑:
  1. 查 project_paper WHERE inclusion_status='included' (按 order ASC)
  2. 对每篇 paper 计算 record_hash = sha256(csl_json|title|doi)
  3. 聚合所有 record_hash → content_hash（去重+幂等键）
  4. 在单一事务内：
     a. INSERT corpus ON CONFLICT (project_id, content_hash) DO NOTHING
     b. SELECT corpus 行（无论新建还是已有）
     c. 批量 INSERT corpus_paper ON CONFLICT (corpus_id, paper_id) DO NOTHING
  5. 返回 Corpus 行

事务约定：build_corpus_snapshot 自管整个事务（begin/commit），
调用方应在事务外（或自管 savepoint）调用本函数。
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Corpus, CorpusPaper, Paper, ProjectPaper

# DOI URL 前缀正则（与 library.py 保持一致）
_DOI_URL_PREFIX_RE = re.compile(
    r"^https?://(?:dx\.)?doi\.org/",
    re.IGNORECASE,
)


def _normalize_doi(raw: str) -> str:
    """剥离 DOI URL 前缀，统一小写+strip。"""
    stripped = _DOI_URL_PREFIX_RE.sub("", raw.strip())
    return stripped.strip().lower()


def _record_hash(paper: Paper) -> str:
    """计算单篇论文题录的稳定 hash（用于 CorpusPaper.record_hash）。

    - DOI 剥 URL 前缀并小写，保证 https://doi.org/10.1/x 与 10.1/x 计算结果一致。
    - title NFC 归一，消除 Unicode 等价形式差异。
    """
    canonical = json.dumps(
        {
            "doi": _normalize_doi(paper.doi or ""),
            "title": unicodedata.normalize("NFC", (paper.title or "").strip()),
            "creators": paper.creators,
            "year": paper.year,
            "abstract": paper.abstract,
            "keywords": paper.keywords,
            "container_title": paper.container_title,
            "csl_json": paper.csl_json,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _content_hash(record_hashes: list[str]) -> str:
    """聚合所有 included 论文的 record_hash → corpus content_hash（顺序无关）。"""
    combined = ",".join(sorted(record_hashes))
    return hashlib.sha256(combined.encode()).hexdigest()


async def build_corpus_snapshot(
    s: AsyncSession,
    project_id: int,
    dbsource: str | None = None,
) -> Corpus:
    """幂等构建 Corpus + CorpusPaper 冻结快照。

    - 只纳入 inclusion_status='included' 的 ProjectPaper。
    - 相同 included 集合重复调用命中 (project_id, content_hash) 唯一约束，返回已有 Corpus。
    - 新建 Corpus 时批量 INSERT corpus_paper 快照行（含 record_hash/csl_json_snapshot）。
    - corpus + corpus_paper 写入在单一事务内完成，保证原子性。

    事务约定：本函数自管 commit，调用方应在事务外调用本函数。
    """
    # 1. 取 included project_paper（含 paper 关联）
    q_pp = (
        select(ProjectPaper, Paper)
        .join(Paper, Paper.id == ProjectPaper.paper_id)
        .where(
            ProjectPaper.project_id == project_id,
            ProjectPaper.inclusion_status == "included",
        )
        .order_by(ProjectPaper.order.asc(), ProjectPaper.id.asc())
    )
    rows = (await s.execute(q_pp)).all()

    pps: list[ProjectPaper] = [r[0] for r in rows]
    papers: list[Paper] = [r[1] for r in rows]

    # 2. 计算各 record_hash 及聚合 content_hash
    record_hashes = [_record_hash(p) for p in papers]
    c_hash = _content_hash(record_hashes) if record_hashes else hashlib.sha256(b"empty").hexdigest()

    # 3. 单一事务写入 corpus + corpus_paper（原子）
    # 3a. 幂等 INSERT corpus
    insert_corpus = (
        pg_insert(Corpus)
        .values(
            project_id=project_id,
            status="parsing",
            document_count=len(papers),
            dbsource=dbsource,
            content_hash=c_hash,
        )
        .on_conflict_do_nothing(constraint="uq_corpus_hash")
    )
    await s.execute(insert_corpus)

    # 3b. SELECT corpus（无论新建还是已有）
    corpus_q = select(Corpus).where(
        Corpus.project_id == project_id,
        Corpus.content_hash == c_hash,
    )
    corpus: Corpus = (await s.execute(corpus_q)).scalar_one()

    # 3c. 批量 INSERT corpus_paper（ON CONFLICT DO NOTHING，幂等重入安全）
    for order_idx, (pp, paper, rec_hash) in enumerate(zip(pps, papers, record_hashes)):
        cp_stmt = (
            pg_insert(CorpusPaper)
            .values(
                corpus_id=corpus.id,
                paper_id=paper.id,
                order=order_idx,
                inclusion_status_snapshot=pp.inclusion_status,
                record_hash=rec_hash,
                csl_json_snapshot=paper.csl_json,
            )
            .on_conflict_do_nothing(constraint="uq_corpus_paper")
        )
        await s.execute(cp_stmt)

    await s.commit()
    return corpus


async def mark_ready(
    s: AsyncSession,
    corpus_id: int,
    r_corpus_id: str,
    document_count: int,
) -> Corpus:
    """将 Corpus 行标记为 ready，写入 r_corpus_id 和 document_count。

    幂等：若已是 ready 且 r_corpus_id 相同，静默覆盖（状态不回退）。
    """
    await s.execute(
        update(Corpus)
        .where(Corpus.id == corpus_id)
        .values(status="ready", r_corpus_id=r_corpus_id, document_count=document_count)
    )
    await s.commit()
    corpus_q = select(Corpus).where(Corpus.id == corpus_id)
    return (await s.execute(corpus_q)).scalar_one()


async def mark_failed(s: AsyncSession, corpus_id: int) -> Corpus:
    """将 Corpus 行标记为 failed（R 端解析失败时调用）。"""
    await s.execute(
        update(Corpus).where(Corpus.id == corpus_id).values(status="failed")
    )
    await s.commit()
    corpus_q = select(Corpus).where(Corpus.id == corpus_id)
    return (await s.execute(corpus_q)).scalar_one()


async def get_corpus_records(s: AsyncSession, corpus_id: int) -> list[dict]:
    """从 corpus_paper 快照取题录 dict 列表，供 R parse_from_records 使用。

    字段取自 Paper 表（通过 csl_json_snapshot 或直接列）。
    返回的 dict 字段与 paper 表列对齐：
      title, creators, year, doi, abstract, keywords,
      container_title, volume, issue, pages, language, csl_json
    """
    q = (
        select(CorpusPaper, Paper)
        .join(Paper, Paper.id == CorpusPaper.paper_id)
        .where(CorpusPaper.corpus_id == corpus_id)
        .order_by(CorpusPaper.order.asc(), CorpusPaper.id.asc())
    )
    rows = (await s.execute(q)).all()
    result: list[dict] = []
    for cp, paper in rows:
        # 优先使用快照时冻结的 csl_json（保真），其次 paper 表直接列
        csl = cp.csl_json_snapshot or paper.csl_json or {}
        # creators: 从 csl_json 的 author 或 paper.creators
        creators = (
            paper.creators
            or (csl.get("author") if isinstance(csl, dict) else None)
            or []
        )
        record: dict = {
            "title": paper.title,
            "creators": creators,
            "year": paper.year,
            "doi": paper.doi,
            "abstract": paper.abstract,
            "keywords": paper.keywords,
            "container_title": paper.container_title,
            "volume": paper.volume,
            "issue": paper.issue,
            "pages": (csl.get("page") if isinstance(csl, dict) else None) or paper.pages,
            "language": paper.language,
            "csl_json": csl if csl else None,
        }
        result.append(record)
    return result
