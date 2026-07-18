from __future__ import annotations


from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AiJob
from ..run_status import normalize_run_status


async def create_job(
    s: AsyncSession,
    *,
    project_id: int,
    kind: str,
    corpus_id: str | None,
    request_json: dict,
) -> AiJob:
    job = AiJob(
        project_id=project_id,
        corpus_id=corpus_id,
        kind=kind,
        status="queued",
        request_json=request_json,
        result_text="",
        events_json=[],
    )
    s.add(job)
    await s.commit()
    await s.refresh(job)
    return job


async def get_job(s: AsyncSession, project_id: int, job_id: int) -> AiJob | None:
    return (
        await s.execute(select(AiJob).where(AiJob.id == job_id, AiJob.project_id == project_id))
    ).scalar_one_or_none()


async def list_jobs(
    s: AsyncSession,
    *,
    project_id: int,
    kind: str | None = None,
    corpus_id: str | None = None,
    limit: int = 20,
) -> list[AiJob]:
    q = select(AiJob).where(AiJob.project_id == project_id)
    if kind:
        q = q.where(AiJob.kind == kind)
    if corpus_id:
        q = q.where(AiJob.corpus_id == corpus_id)
    q = q.order_by(AiJob.created_at.desc(), AiJob.id.desc()).limit(max(1, min(limit, 100)))
    return list((await s.execute(q)).scalars().all())


async def update_job(
    s: AsyncSession,
    job: AiJob,
    *,
    status: str | None = None,
    result_text: str | None = None,
    annotated_text: str | None = None,
    summary_json: dict | None = None,
    error: str | None = None,
    append_event: dict | None = None,
    complete: bool = False,
) -> AiJob:
    if status is not None:
        job.status = normalize_run_status(status)
    if result_text is not None:
        job.result_text = result_text
    if annotated_text is not None:
        job.annotated_text = annotated_text
    if summary_json is not None:
        job.summary_json = summary_json
    if error is not None:
        job.error = error
    if append_event is not None:
        events = list(job.events_json or [])
        events.append(append_event)
        job.events_json = events
    # 终态统一落 completed_at（成功走 complete=True；failed/cancelled 也是终态，
    # 不落会让失败样本在按完成时间统计时长/成功率时被静默丢失——codex 复核 P2）。
    # 与 created_at 的 server_default=func.now() 同源取 DB 时钟：此前 Python utcnow 与
    # DB 本地时区(+8)分裂，生产 6 条 review job 时长全为 -8h（0.6.2 S7）。
    if (complete or status in ("failed", "cancelled")) and job.completed_at is None:
        job.completed_at = func.now()
    await s.commit()
    await s.refresh(job)
    return job
