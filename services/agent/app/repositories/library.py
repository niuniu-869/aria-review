"""Library 仓储: paper 增改查 + dedup + 附件 + 统计聚合。

去重策略 (幂等):
  1. 有 DOI  → dedup_key = "doi:<normalized_doi>"
  2. 无 DOI  → dedup_key = "title:<sha256(normalized_title)[:32]>"
  命中已有行直接返回，不新建。
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

from sqlalchemy import case, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Attachment, Note, Paper, PaperExternalId, PaperTag, ProjectPaper, Tag

# Paper 表列名集合，用于过滤 data 里的非列字段
_PAPER_COLS: frozenset[str] = frozenset(Paper.__table__.columns.keys())

# DOI URL 前缀正则（支持常见变体）
_DOI_URL_PREFIX_RE = re.compile(
    r"^https?://(?:dx\.)?doi\.org/",
    re.IGNORECASE,
)


def _is_missing_value(value) -> bool:
    """判断 Paper 现有字段是否可被元数据回填。"""
    return value is None or value == "" or value == [] or value == {}


def _needs_creator_enrichment(current, incoming) -> bool:
    """旧版本曾把 authors 直接存成字符串数组；新格式用 CSL literal 对象。
    仅在 incoming 作者数 >= current 时才升级，避免把完整作者覆盖成更少/不完整的
    列表(codex Batch2 P1:dedup 命中时 incoming 可能比已有作者少)。"""
    if not isinstance(current, list) or not isinstance(incoming, list) or not incoming:
        return False
    if len(incoming) < len(current):
        return False
    return all(isinstance(item, str) for item in current) and any(
        isinstance(item, dict) for item in incoming
    )


def _merge_csl_json(current, incoming) -> dict | None:
    """合并 CSL-JSON 增量字段，重点保留后续分析依赖的 references。"""
    if not isinstance(incoming, dict) or not incoming:
        return None
    base = dict(current) if isinstance(current, dict) else {}
    changed = False

    for key, value in incoming.items():
        if _is_missing_value(value):
            continue
        existing = base.get(key)
        if key in {"references", "reference"} and isinstance(value, list):
            merged = []
            seen = set()
            for item in (existing if isinstance(existing, list) else []) + value:
                text = str(item).strip() if item is not None else ""
                if not text or text in seen:
                    continue
                seen.add(text)
                merged.append(item)
            if merged and merged != existing:
                base[key] = merged
                changed = True
        elif _is_missing_value(existing):
            base[key] = value
            changed = True

    return base if changed else None


def _split_keywords(value) -> list[str]:
    if _is_missing_value(value):
        return []
    if isinstance(value, (list, tuple, set)):
        raw = "; ".join(str(item) for item in value if not _is_missing_value(item))
    else:
        raw = str(value)
    parts = re.split(r"[;,，；]+", raw)
    return [part.strip() for part in parts if part.strip()]


def _merge_keywords(current, incoming) -> str | None:
    incoming_parts = _split_keywords(incoming)
    if not incoming_parts:
        return None
    current_parts = _split_keywords(current)
    merged: list[str] = []
    seen: set[str] = set()
    for item in current_parts + incoming_parts:
        norm = item.casefold()
        if norm in seen:
            continue
        seen.add(norm)
        merged.append(item)
    result = "; ".join(merged)
    return result if result and result != (current or "") else None


def _normalize_doi(raw: str) -> str:
    """剥离 DOI URL 前缀，统一小写+strip。"""
    stripped = _DOI_URL_PREFIX_RE.sub("", raw.strip())
    return stripped.strip().lower()


def compute_dedup_key(data: dict) -> str:
    """根据 doi 或标题 hash 计算去重键。

    - DOI 分支：剥除 https://doi.org/ 等前缀，统一小写。
    - 标题分支：NFC 归一后去非词字符再 sha256。
    """
    doi = (data.get("doi") or "").strip()
    if doi:
        return f"doi:{_normalize_doi(doi)}"
    raw_title = data.get("title") or ""
    nfc_title = unicodedata.normalize("NFC", raw_title)
    norm = re.sub(r"\W+", "", nfc_title.lower())
    return "title:" + hashlib.sha256(norm.encode()).hexdigest()[:32]


async def find_by_dedup(
    s: AsyncSession,
    key: str,
    owner_id: int | None = None,
) -> Paper | None:
    """按 dedup_key (+owner_id) 查找 Paper；不存在返回 None。"""
    q = select(Paper).where(
        Paper.dedup_key == key,
        Paper.owner_id == owner_id,
    )
    return (await s.execute(q)).scalar_one_or_none()


async def add_paper(
    s: AsyncSession,
    data: dict,
    owner_id: int | None = None,
) -> Paper:
    """幂等写入 Paper：命中 dedup 直接返回已有行；否则 INSERT 并返回新行。

    使用 INSERT ... ON CONFLICT DO NOTHING 后 SELECT，保证并发安全、DB 层真去重。
    - owner_id 为 None 时：冲突目标为部分唯一索引 uq_paper_dedup_null_owner（dedup_key WHERE owner_id IS NULL）。
    - owner_id 非 None 时：冲突目标为复合唯一约束 uq_paper_dedup（dedup_key, owner_id）。

    data 支持 Paper 所有列字段（多余字段忽略）。
    """
    key = compute_dedup_key(data)

    # 只保留 ORM 列字段，去掉 id/dedup_key/owner_id（单独传）
    safe_fields = {
        k: v for k, v in data.items()
        if k in _PAPER_COLS and k not in ("id", "dedup_key", "owner_id",
                                           "created_at", "updated_at")
    }

    if owner_id is None:
        stmt = (
            pg_insert(Paper)
            .values(dedup_key=key, owner_id=None, **safe_fields)
            .on_conflict_do_nothing(
                index_elements=["dedup_key"],
                index_where=text("owner_id IS NULL"),
            )
        )
    else:
        stmt = (
            pg_insert(Paper)
            .values(dedup_key=key, owner_id=owner_id, **safe_fields)
            .on_conflict_do_nothing(constraint="uq_paper_dedup")
        )

    await s.execute(stmt)
    await s.commit()

    # 无论是否新插入，都 SELECT 返回（幂等）。若命中已有记录，只补全空字段，不覆盖已有值。
    paper = await find_by_dedup(s, key, owner_id)
    if paper is None:
        return paper

    changed = False
    for field, value in safe_fields.items():
        if field in {"title", "dedup_key", "owner_id"} or _is_missing_value(value):
            continue
        if hasattr(paper, field):
            current = getattr(paper, field)
            if field == "csl_json":
                merged = _merge_csl_json(current, value)
                if merged is not None:
                    setattr(paper, field, merged)
                    changed = True
            elif field == "keywords":
                merged = _merge_keywords(current, value)
                if merged is not None:
                    setattr(paper, field, merged)
                    changed = True
            elif _is_missing_value(current) or (
                field == "creators" and _needs_creator_enrichment(current, value)
            ):
                setattr(paper, field, value)
                changed = True
    if changed:
        await s.commit()
        await s.refresh(paper)
    return paper


async def get_by_id(
    s: AsyncSession,
    paper_id: int,
) -> Paper | None:
    """按主键取 Paper；不存在返回 None。"""
    q = select(Paper).where(Paper.id == paper_id)
    return (await s.execute(q)).scalar_one_or_none()


async def get_paper_with_relations(
    s: AsyncSession,
    paper_id: int,
) -> Paper | None:
    """按主键取 Paper 并附带其 tags 和 notes（通过独立查询加载）。

    返回的 Paper 对象上额外挂两个属性:
      - _tags: list[Tag]
      - _notes: list[Note]
    不存在返回 None。
    """
    paper = await get_by_id(s, paper_id)
    if paper is None:
        return None

    # 查 tags
    tag_q = (
        select(Tag)
        .join(PaperTag, PaperTag.tag_id == Tag.id)
        .where(PaperTag.paper_id == paper_id)
    )
    tags = list((await s.execute(tag_q)).scalars().all())
    paper._tags = tags

    # 查 notes（paper 关联的笔记）
    note_q = select(Note).where(Note.paper_id == paper_id).order_by(Note.created_at.asc())
    notes = list((await s.execute(note_q)).scalars().all())
    paper._notes = notes

    return paper


async def find_by_query(
    s: AsyncSession,
    query: str,
    limit: int = 20,
) -> list[Paper]:
    """按关键词在 title / doi / keywords 中做 ILIKE 模糊查询。

    三列之间取 OR，结果最多返回 limit 条（按 id 倒序，优先最新）。
    """
    pattern = f"%{query}%"
    q = (
        select(Paper)
        .where(
            or_(
                Paper.title.ilike(pattern),
                Paper.doi.ilike(pattern),
                Paper.keywords.ilike(pattern),
            )
        )
        .order_by(Paper.id.desc())
        .limit(limit)
    )
    result = await s.execute(q)
    return list(result.scalars().all())


async def add_tags(
    s: AsyncSession,
    paper_id: int,
    tags: list[str],
) -> list[str]:
    """幂等给 Paper 打标签：Tag 不存在则创建，PaperTag 已存在则跳过。

    返回实际关联后的标签名列表（按输入顺序去重）。
    """
    seen: set[str] = set()
    applied: list[str] = []
    for name in tags:
        name = name.strip()
        if not name or name in seen:
            continue
        seen.add(name)

        # 查或建 Tag
        tag_q = select(Tag).where(Tag.name == name)
        tag = (await s.execute(tag_q)).scalar_one_or_none()
        if tag is None:
            tag = Tag(name=name)
            s.add(tag)
            await s.flush()  # 获取 tag.id，不提交事务

        # 幂等关联 PaperTag
        pt_q = select(PaperTag).where(
            PaperTag.paper_id == paper_id,
            PaperTag.tag_id == tag.id,
        )
        existing = (await s.execute(pt_q)).scalar_one_or_none()
        if existing is None:
            s.add(PaperTag(paper_id=paper_id, tag_id=tag.id))

        applied.append(name)

    await s.commit()
    return applied


# ---------------------------------------------------------------------------
# 附件辅助
# ---------------------------------------------------------------------------

async def add_attachment(
    s: AsyncSession,
    paper_id: int,
    *,
    sha256: str | None = None,
    mineru_status: str | None = None,
    path: str | None = None,
    url: str | None = None,
) -> Attachment:
    """最小 Attachment 写入（供测试 fixture 和内部使用）。"""
    att = Attachment(
        paper_id=paper_id,
        sha256=sha256,
        mineru_status=mineru_status,
        path=path,
        url=url,
    )
    s.add(att)
    await s.flush()
    return att


async def upsert_external_ids(
    s: AsyncSession,
    paper_id: int,
    external_ids: list[dict],
) -> list[PaperExternalId]:
    """幂等写入外部 provider 标识，保留跨服务追溯能力。"""
    rows: list[PaperExternalId] = []
    seen: set[tuple[str, str, str]] = set()
    for item in external_ids:
        provider = str(item.get("provider") or "").strip().lower()
        id_type = str(item.get("id_type") or item.get("type") or "").strip().lower()
        external_id = str(item.get("external_id") or item.get("value") or "").strip()
        if not provider or not id_type or not external_id:
            continue
        key = (provider, id_type, external_id)
        if key in seen:
            continue
        seen.add(key)

        stmt = (
            pg_insert(PaperExternalId)
            .values(
                paper_id=paper_id,
                provider=provider,
                id_type=id_type,
                external_id=external_id,
                url=item.get("url"),
                raw=item.get("raw"),
            )
            .on_conflict_do_nothing(constraint="uq_paper_external_id_paper")
        )
        await s.execute(stmt)

    await s.commit()

    if not seen:
        return rows
    q = select(PaperExternalId).where(
        PaperExternalId.provider.in_({p for p, _, _ in seen}),
        PaperExternalId.paper_id == paper_id,
    )
    result = await s.execute(q)
    return list(result.scalars().all())


async def list_external_ids(
    s: AsyncSession,
    paper_id: int,
    provider: str | None = None,
    id_type: str | None = None,
) -> list[PaperExternalId]:
    """读取某篇文献的外部标识。"""
    q = select(PaperExternalId).where(PaperExternalId.paper_id == paper_id)
    if provider:
        q = q.where(PaperExternalId.provider == provider.lower())
    if id_type:
        q = q.where(PaperExternalId.id_type == id_type.lower())
    result = await s.execute(q.order_by(PaperExternalId.id.asc()))
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# 统计聚合（附件先按 paper_id 预聚合，防一对多放大 + 防 N+1）
# ---------------------------------------------------------------------------

# OCR 完成度可比序：done>processing>pending>failed>none
_OCR_RANK = case(
    (Attachment.mineru_status == "done", 4),
    (Attachment.mineru_status == "processing", 3),
    (Attachment.mineru_status == "pending", 2),
    (Attachment.mineru_status == "failed", 1),
    else_=0,
)

# 供外部（main.py list 端点）复用
RANK_TO_STATUS = {4: "done", 3: "processing", 2: "pending", 1: "failed", 0: "none"}


def attachment_status_subquery():
    """每篇 paper 一行：att_count + best_ocr_rank。预聚合杜绝一对多放大。"""
    return (
        select(
            Attachment.paper_id.label("paper_id"),
            func.count(Attachment.id).label("att_count"),
            func.max(_OCR_RANK).label("best_ocr_rank"),
        )
        .group_by(Attachment.paper_id)
        .subquery()
    )


async def compute_library_stats(
    s: AsyncSession,
    project_id: int | None = None,
) -> dict:
    """计算文献库统计摘要。

    - project_id=None：全库统计，返回 LibraryStats 形状。
    - project_id!=None：项目作用域，返回 ProjectLibraryStats 形状（含 inclusion breakdown）。

    附件先按 paper_id 预聚合成子查询再 LEFT JOIN，保证一对多不放大论文数。
    withMetadata 口径：abstract 非空 OR csl_json 非空（title 必填，不算指标）。
    """
    att = attachment_status_subquery()
    has_meta = (
        ((Paper.abstract.isnot(None)) & (func.length(Paper.abstract) > 0))
        | (Paper.csl_json.isnot(None))
    )

    q = select(
        func.count(func.distinct(Paper.id)),
        func.count(func.distinct(case((has_meta, Paper.id)))),
        func.count(func.distinct(case((att.c.att_count > 0, Paper.id)))),
        func.count(func.distinct(case((att.c.best_ocr_rank == 4, Paper.id)))),
        func.count(func.distinct(case((att.c.best_ocr_rank == 3, Paper.id)))),
        func.count(func.distinct(case((att.c.best_ocr_rank == 2, Paper.id)))),
        func.count(func.distinct(case((att.c.best_ocr_rank == 1, Paper.id)))),
    ).select_from(Paper).join(att, att.c.paper_id == Paper.id, isouter=True)

    if project_id is not None:
        q = q.join(ProjectPaper, ProjectPaper.paper_id == Paper.id).where(
            ProjectPaper.project_id == project_id
        )

    total, meta, pdf, d, pr_, pe, fa = (await s.execute(q)).one()
    out = {
        "totalPapers": total,
        "withMetadata": meta,
        "withPdf": pdf,
        "ocr": {
            "done": d,
            "processing": pr_,
            "pending": pe,
            "failed": fa,
            "none": total - (d + pr_ + pe + fa),
        },
    }

    if project_id is not None:
        inc_q = (
            select(ProjectPaper.inclusion_status, func.count())
            .where(ProjectPaper.project_id == project_id)
            .group_by(ProjectPaper.inclusion_status)
        )
        rows = {r[0]: r[1] for r in (await s.execute(inc_q)).all()}
        out = {
            "projectPapers": total,
            "inclusion": {
                k: rows.get(k, 0)
                for k in ("included", "candidate", "excluded", "maybe")
            },
            "withMetadata": meta,
            "withPdf": pdf,
            "ocr": out["ocr"],
        }

    return out
