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
