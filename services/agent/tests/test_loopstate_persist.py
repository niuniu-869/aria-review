import pytest
from app.harness.engine import LoopState
from app.repositories.agent_run import create_run, save_state, get_state
from app.repositories.project import create_project


@pytest.mark.asyncio
async def test_save_and_restore_full_loopstate(session_factory):
    async with session_factory() as s:
        p = await create_project(s, {"name": "P"}); rid = (await create_run(s, project_id=p.id)).id
    st = LoopState(messages=[{"role": "user", "content": "x"}], round_idx=2, tool_rounds=1,
                   last_memo_idx=3, all_tool_results=[{"tool_id": "a"}], rounds_log=[{"round": 1}],
                   model_used="deepseek-chat", status="awaiting_confirmation",
                   pending_round={"queue": [1]}, final_output=None,
                   evidence_refs=[{"paper_id": 1}], validation_summary={"fabricated_citations": 0})
    async with session_factory() as s:
        await save_state(s, rid, st)
    async with session_factory() as s:
        st2 = await get_state(s, rid)
    assert st2.round_idx == 2 and st2.tool_rounds == 1 and st2.last_memo_idx == 3
    assert st2.model_used == "deepseek-chat" and st2.pending_round == {"queue": [1]}
    assert st2.evidence_refs == [{"paper_id": 1}] and st2.validation_summary == {"fabricated_citations": 0}


@pytest.mark.asyncio
async def test_get_state_backward_compat_with_plain_messages_list(session_factory):
    # simulate old row: messages_snapshot is a plain list
    async with session_factory() as s:
        p = await create_project(s, {"name": "Q"}); run = await create_run(s, project_id=p.id)
        run.messages_snapshot = [{"role": "user", "content": "old"}]; await s.commit()
        rid = run.id
    async with session_factory() as s:
        st = await get_state(s, rid)
    assert st is not None and st.messages == [{"role": "user", "content": "old"}]
