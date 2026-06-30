"""Project 仓储: project CRUD + paper 关联管理。

幂等规则:
  - add_paper_to_project: 利用 uq_project_paper 唯一约束，INSERT ON CONFLICT DO NOTHING，
    再 SELECT 返回已有行（不报 IntegrityError）。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Paper, Project, ProjectPaper

_PROJECT_COLS: frozenset[str] = frozenset(Project.__table__.columns.keys())
_PP_COLS: frozenset[str] = frozenset(ProjectPaper.__table__.columns.keys())


async def create_project(
    s: AsyncSession,
    data: dict,
    owner_id: int | None = None,
) -> Project:
    """创建 Project，返回已持久化对象。"""
    safe = {
        k: v for k, v in data.items()
        if k in _PROJECT_COLS and k not in ("id", "owner_id", "created_at")
    }
    proj = Project(owner_id=owner_id, **safe)
    s.add(proj)
    await s.commit()
    await s.refresh(proj)
    return proj


async def add_paper_to_project(
    s: AsyncSession,
    project_id: int,
    paper_id: int,
    added_by: str = "user",
    order: int = 0,
) -> ProjectPaper:
    """幂等关联 Paper 到 Project（INSERT ... ON CONFLICT DO NOTHING）。

    无论是否已存在都返回 ProjectPaper 行。
    """
    stmt = (
        pg_insert(ProjectPaper)
        .values(
            project_id=project_id,
            paper_id=paper_id,
            inclusion_status="candidate",
            added_by=added_by,
            order=order,
        )
        .on_conflict_do_nothing(constraint="uq_project_paper")
    )
    await s.execute(stmt)
    await s.commit()

    # 无论插入还是已存在，都 SELECT 返回
    q = select(ProjectPaper).where(
        ProjectPaper.project_id == project_id,
        ProjectPaper.paper_id == paper_id,
    )
    pp = (await s.execute(q)).scalar_one()
    return pp


async def set_inclusion(
    s: AsyncSession,
    project_paper_id: int,
    status: str,
    reason: str | None = None,
    score: int | None = None,
) -> ProjectPaper:
    """更新 ProjectPaper 的 inclusion_status（及可选的 exclusion_reason / screening_score）。"""
    q = select(ProjectPaper).where(ProjectPaper.id == project_paper_id)
    pp = (await s.execute(q)).scalar_one()
    pp.inclusion_status = status
    if reason is not None:
        pp.exclusion_reason = reason
    if score is not None:
        pp.screening_score = score
    await s.commit()
    await s.refresh(pp)
    return pp


async def get_project(
    s: AsyncSession,
    project_id: int,
) -> Project | None:
    """按主键取 Project；不存在返回 None。"""
    q = select(Project).where(Project.id == project_id)
    return (await s.execute(q)).scalar_one_or_none()


async def list_projects(
    s: AsyncSession,
    owner_id: int | None = None,
) -> list[Project]:
    """列出所有 Project（按创建时间倒序）。owner_id 为 None 时列出全部。"""
    q = select(Project).order_by(Project.created_at.desc())
    if owner_id is not None:
        q = q.where(Project.owner_id == owner_id)
    result = await s.execute(q)
    return list(result.scalars().all())


async def list_project_papers(
    s: AsyncSession,
    project_id: int,
) -> list[tuple[ProjectPaper, Paper]]:
    """列出指定 project 的所有 ProjectPaper 及其 Paper（按 order ASC）。"""
    q = (
        select(ProjectPaper, Paper)
        .join(Paper, Paper.id == ProjectPaper.paper_id)
        .where(ProjectPaper.project_id == project_id)
        .order_by(ProjectPaper.order.asc(), ProjectPaper.id.asc())
    )
    rows = (await s.execute(q)).all()
    return [(r[0], r[1]) for r in rows]


async def find_project_paper(
    s: AsyncSession,
    project_id: int,
    paper_id: int,
) -> ProjectPaper | None:
    """按 (project_id, paper_id) 查 ProjectPaper；不存在返回 None。"""
    q = select(ProjectPaper).where(
        ProjectPaper.project_id == project_id,
        ProjectPaper.paper_id == paper_id,
    )
    return (await s.execute(q)).scalar_one_or_none()
