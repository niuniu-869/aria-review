"""结构化抽取仓储层：upsert / get PaperExtraction。

幂等 upsert：paper_id 已存在则更新字段，否则插入。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import PaperExtraction


async def upsert_extraction(
    s: AsyncSession,
    paper_id: int,
    fields: dict,
    model: str | None = None,
) -> PaperExtraction:
    """幂等 upsert PaperExtraction。

    若该 paper_id 已有记录则更新字段（覆盖写），否则新建。
    调用方负责 commit/rollback。

    Args:
        s:        AsyncSession。
        paper_id: 对应的 paper.id。
        fields:   包含 research_question/method/findings/dataset/contribution 的 dict。
        model:    生成该抽取结果的 LLM model 名（可 None）。

    Returns:
        upserted PaperExtraction ORM 对象。
    """
    q = select(PaperExtraction).where(PaperExtraction.paper_id == paper_id)
    existing: PaperExtraction | None = (await s.execute(q)).scalar_one_or_none()

    if existing is not None:
        existing.research_question = fields.get("research_question")
        existing.method = fields.get("method")
        existing.findings = fields.get("findings")
        existing.dataset = fields.get("dataset")
        existing.contribution = fields.get("contribution")
        existing.raw = fields.get("raw")
        existing.model = model
        s.add(existing)
        return existing
    else:
        new_ext = PaperExtraction(
            paper_id=paper_id,
            research_question=fields.get("research_question"),
            method=fields.get("method"),
            findings=fields.get("findings"),
            dataset=fields.get("dataset"),
            contribution=fields.get("contribution"),
            raw=fields.get("raw"),
            model=model,
        )
        s.add(new_ext)
        return new_ext


async def get_extraction(
    s: AsyncSession,
    paper_id: int,
) -> PaperExtraction | None:
    """取 paper_id 对应的 PaperExtraction，不存在返回 None。"""
    q = select(PaperExtraction).where(PaperExtraction.paper_id == paper_id)
    return (await s.execute(q)).scalar_one_or_none()
