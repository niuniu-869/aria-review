"""Task P1-1: AgentRun 扩展 + AgentEvent / ToolInvocation 表 TDD 测试。

测试用 `session` fixture（create_all 已建全部表），每测试完全隔离。
因为后端是 PostgreSQL，FK 约束严格，必须先建父行再插子行。
"""
import pytest
from app.repositories.project import create_project


@pytest.mark.asyncio
async def test_agent_run_awaiting_confirmation_status_fits(session):
    """status = 'awaiting_confirmation'（21 字符）应能存入 String(24) 列，
    新增字段 cursor / auto_confirm / final_output / evidence_refs / pending_round 可正常写读。
    """
    from app.models import AgentRun

    # 先建父行 Project（FK 要求存在）
    proj = await create_project(session, {"name": "test-project-p11"})

    r = AgentRun(
        project_id=proj.id,
        status="awaiting_confirmation",
        cursor=0,
        auto_confirm=False,
        final_output="some output",
        evidence_refs={"key": "val"},
        pending_round={"step": 1},
    )
    session.add(r)
    await session.commit()
    await session.refresh(r)

    assert r.status == "awaiting_confirmation"
    assert r.cursor == 0
    assert r.auto_confirm is False
    assert r.final_output == "some output"
    assert r.evidence_refs == {"key": "val"}
    assert r.pending_round == {"step": 1}


@pytest.mark.asyncio
async def test_agent_run_defaults(session):
    """cursor / auto_confirm 的默认值正确。"""
    from app.models import AgentRun

    proj = await create_project(session, {"name": "test-project-defaults"})

    r = AgentRun(project_id=proj.id)
    session.add(r)
    await session.commit()
    await session.refresh(r)

    assert r.cursor == 0
    assert r.auto_confirm is False
    assert r.status == "running"
    assert r.final_output is None
    assert r.evidence_refs is None
    assert r.pending_round is None


@pytest.mark.asyncio
async def test_agent_event_and_tool_invocation(session):
    """AgentEvent / ToolInvocation 能正常写读，唯一约束字段正常持久化。"""
    from app.models import AgentEvent, AgentRun, ToolInvocation

    # 建 Project → AgentRun 父行
    proj = await create_project(session, {"name": "test-project-events"})
    run = AgentRun(project_id=proj.id, status="running", cursor=0)
    session.add(run)
    await session.commit()
    await session.refresh(run)

    # AgentEvent
    e = AgentEvent(
        run_id=run.id,
        seq=1,
        type="run_start",
        payload={"a": 1},
    )
    # ToolInvocation
    ti = ToolInvocation(
        run_id=run.id,
        idempotency_key="k",
        tool_id="project",
        action="set_inclusion",
        result={"ok": True},
    )
    session.add(e)
    session.add(ti)
    await session.commit()
    await session.refresh(e)
    await session.refresh(ti)

    assert e.id is not None
    assert ti.id is not None
    assert e.seq == 1
    assert e.type == "run_start"
    assert e.payload == {"a": 1}
    assert ti.idempotency_key == "k"
    assert ti.tool_id == "project"
    assert ti.action == "set_inclusion"
    assert ti.result == {"ok": True}


@pytest.mark.asyncio
async def test_agent_event_unique_seq(session):
    """同一 run 下 seq 重复应违反唯一约束 uq_agent_event_seq。"""
    from sqlalchemy.exc import IntegrityError
    from app.models import AgentEvent, AgentRun

    proj = await create_project(session, {"name": "test-project-unique-seq"})
    run = AgentRun(project_id=proj.id)
    session.add(run)
    await session.commit()
    await session.refresh(run)

    e1 = AgentEvent(run_id=run.id, seq=1, type="run_start")
    e2 = AgentEvent(run_id=run.id, seq=1, type="tool_call")  # 同 seq，应冲突
    session.add(e1)
    session.add(e2)
    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.asyncio
async def test_tool_invocation_unique_key(session):
    """同一 run 下 idempotency_key 重复应违反唯一约束 uq_tool_invocation_key。"""
    from sqlalchemy.exc import IntegrityError
    from app.models import AgentRun, ToolInvocation

    proj = await create_project(session, {"name": "test-project-unique-key"})
    run = AgentRun(project_id=proj.id)
    session.add(run)
    await session.commit()
    await session.refresh(run)

    ti1 = ToolInvocation(run_id=run.id, idempotency_key="same-key", tool_id="t", action="a")
    ti2 = ToolInvocation(run_id=run.id, idempotency_key="same-key", tool_id="t", action="b")
    session.add(ti1)
    session.add(ti2)
    with pytest.raises(IntegrityError):
        await session.commit()
