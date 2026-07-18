import pytest

from app.models import Project
from app.repositories import ai_job as ai_job_repo


@pytest.mark.asyncio
async def test_ai_job_repo_persists_result_and_events(session):
    project = Project(name="ai-job-repo")
    session.add(project)
    await session.commit()
    await session.refresh(project)

    job = await ai_job_repo.create_job(
        session,
        project_id=project.id,
        kind="summary",
        corpus_id=None,
        request_json={"kind": "summary", "llm": {"hasApiKey": False}},
    )
    job = await ai_job_repo.update_job(
        session,
        job,
        status="done",
        result_text="persistent output",
        append_event={"event": "done", "data": {}},
        complete=True,
    )

    saved = await ai_job_repo.get_job(session, project.id, job.id)
    assert saved is not None
    assert saved.status == "done"
    assert saved.result_text == "persistent output"
    assert saved.events_json[-1]["event"] == "done"
    assert saved.request_json["llm"]["hasApiKey"] is False
    # 0.6.2 S7: completed_at 与 created_at 同源 DB 时钟——时长不得为负
    # （回归防护: 此前 Python utcnow 与 server_default func.now() 时区分裂, 生产时长 -8h）。
    assert saved.completed_at is not None
    assert saved.completed_at >= saved.created_at


@pytest.mark.asyncio
async def test_ai_job_repo_filters_latest_by_kind_and_corpus(session):
    project = Project(name="ai-job-filter")
    session.add(project)
    await session.commit()
    await session.refresh(project)

    await ai_job_repo.create_job(
        session,
        project_id=project.id,
        kind="chat",
        corpus_id="c1",
        request_json={"kind": "chat"},
    )
    await ai_job_repo.create_job(
        session,
        project_id=project.id,
        kind="review",
        corpus_id="c1",
        request_json={"kind": "review"},
    )

    rows = await ai_job_repo.list_jobs(session, project_id=project.id, kind="review", corpus_id="c1")
    assert len(rows) == 1
    assert rows[0].kind == "review"


@pytest.mark.asyncio
async def test_ai_job_kind_enums_split(session):
    """P3 收口回归：gap_verify 行可被 AiJobItem 序列化；通用创建入口拒绝 gap_discover。

    gap_discover/gap_verify 由 research 专用端点创建；曾因共享单一 AiJobKind 枚举，
    存在 gap_verify 行时 jobs 列表整个 500。
    """
    import pydantic
    from app.schemas import AiJobCreateRequest, AiJobItem

    project = Project(name="ai-job-kind-split")
    session.add(project)
    await session.commit()
    await session.refresh(project)

    # 持久化 gap_verify 行（模拟 research verify 端点行为）→ 响应模型可序列化
    job = await ai_job_repo.create_job(
        session,
        project_id=project.id,
        kind="gap_verify",
        corpus_id=None,
        request_json={"gapId": "g1"},
    )
    item = AiJobItem(
        id=job.id, projectId=project.id, kind=job.kind, status=job.status,
    )
    assert item.kind == "gap_verify"

    # 通用创建入口拒绝 research 专用 kind
    for bad_kind in ("gap_discover", "gap_verify", "banana"):
        with pytest.raises(pydantic.ValidationError):
            AiJobCreateRequest(kind=bad_kind)


@pytest.mark.asyncio
async def test_ai_job_failed_terminal_gets_completed_at(session):
    """failed/cancelled 终态也须落 completed_at（codex 复核 P2：失败样本不得在时长统计中静默丢失）。"""
    project = Project(name="ai-job-failed-ts")
    session.add(project)
    await session.commit()
    await session.refresh(project)

    job = await ai_job_repo.create_job(
        session, project_id=project.id, kind="summary", corpus_id=None, request_json={},
    )
    job = await ai_job_repo.update_job(session, job, status="failed", error="boom")

    assert job.status == "failed"
    assert job.completed_at is not None
    assert job.completed_at >= job.created_at
