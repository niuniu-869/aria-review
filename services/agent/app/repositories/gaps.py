"""gap_candidate 表读写仓储（A1 scratchpad 持久化 + A5 verify/verdict/HITL）。

映射 GapCandidate（领域 dataclass，契约 §2.2）↔ GapCandidateRecord（ORM）。
所有函数为无状态 session 入参式（与 ai_job.py 同风格）。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent.scratchpad import GapCandidate
from ..models import GapCandidateRecord


def _to_domain(row: GapCandidateRecord) -> GapCandidate:
    return GapCandidate(
        gap_id=row.gap_id,
        theme=row.theme or "",
        statement=row.statement or "",
        lens=row.lens or "concept",
        supporting_papers=list(row.supporting_papers or []),
        counter_evidence=list(row.counter_evidence or []),
        confidence=float(row.confidence or 0.0),
        status=row.status or "draft",
        value_verdict=row.value_verdict,
    )


def _apply(row: GapCandidateRecord, entry: GapCandidate) -> None:
    row.theme = entry.theme
    row.statement = entry.statement
    row.lens = entry.lens
    row.supporting_papers = entry.supporting_papers
    row.counter_evidence = entry.counter_evidence
    row.confidence = entry.confidence
    row.status = entry.status
    row.value_verdict = entry.value_verdict


async def upsert_gap(
    s: AsyncSession,
    run_id: str,
    entry: GapCandidate,
    *,
    project_id: int | None = None,
) -> GapCandidate:
    """按 gap_id 落库（存在则更新，否则插入）。gap_id 服务端唯一生成 → 并发 add 不撞键。"""
    row = (
        await s.execute(
            select(GapCandidateRecord).where(GapCandidateRecord.gap_id == entry.gap_id)
        )
    ).scalar_one_or_none()
    if row is None:
        row = GapCandidateRecord(gap_id=entry.gap_id, run_id=run_id, project_id=project_id)
        _apply(row, entry)
        s.add(row)
    else:
        _apply(row, entry)
    await s.commit()
    return entry


async def get_gap_in_run(s: AsyncSession, run_id: str, gap_id: str) -> GapCandidate | None:
    row = (
        await s.execute(
            select(GapCandidateRecord).where(
                GapCandidateRecord.gap_id == gap_id,
                GapCandidateRecord.run_id == run_id,
            )
        )
    ).scalar_one_or_none()
    return _to_domain(row) if row else None


async def list_gaps_by_run(s: AsyncSession, run_id: str) -> list[GapCandidate]:
    rows = (
        await s.execute(
            select(GapCandidateRecord)
            .where(GapCandidateRecord.run_id == run_id)
            .order_by(GapCandidateRecord.id.asc())
        )
    ).scalars().all()
    return [_to_domain(r) for r in rows]


# ---- A5 verify/verdict/HITL：按 gap_id 全局定位（跨 run）----

async def get_record(s: AsyncSession, gap_id: str) -> GapCandidateRecord | None:
    """按全局唯一 gap_id 取 ORM 行（供 verify/verdict/HITL 直接改字段）。"""
    return (
        await s.execute(
            select(GapCandidateRecord).where(GapCandidateRecord.gap_id == gap_id)
        )
    ).scalar_one_or_none()


async def get_gap(s: AsyncSession, gap_id: str) -> GapCandidate | None:
    row = await get_record(s, gap_id)
    return _to_domain(row) if row else None
