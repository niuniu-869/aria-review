"""P0-1 端到端：FakeLLM 驱动 agent 自主发起 ingest__parse → extract__structured。

这是作战方案 §3.1 / §4 P0-1 的核心验证：一次 agent run 的 RunLog 里出现 agent
**自主发起**的 ingest__parse / extract__structured tool_call，且 run 跑到 done。

做法（全离线）：
- 建真实 Project + 1 篇带 PDF 路径、尚未 OCR-done 的 paper。
- patch app.ingest.fulltext.parse_pdfs → 返回 done markdown（IngestTool 写盘 + 关联项目）。
- patch app.tools.extract.get_llm_client → FakeLLM 返回结构化 JSON（ExtractTool 抽取）。
- patch app.harness.engine.call_llm_with_fallback → 三轮：tool_call(ingest) → tool_call(extract) → 最终答复。
- auto_confirm=True：写工具直接执行（不挂起人工确认），run 端到端跑完。

断言：
- RunLog（agent_event）里出现 name=ingest__parse 与 extract__structured 的 tool_call。
- run.status == "done"。
- DB：paper 已 OCR-done + 关联项目 + 有 paper_extraction 行（工具真正落了副作用）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agent.context import AgentContext
from app.agent.prompts import AGENT_SYSTEM, WRAP_UP
from app.agent.registry_factory import build_registry
from app.agent.run_controller import RunController
from app.harness.config import EngineConfig, set_config
from app.harness.events import SubscribableEventPublisher
from app.harness.llm import LLMRouter
from app.models import Attachment, Paper, PaperExtraction, ProjectPaper
from app.repositories import agent_run as repo
from app.repositories.library import add_paper
from app.repositories.project import add_paper_to_project, create_project, find_project_paper


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def session_factory_local(session):
    return async_sessionmaker(session.bind, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _isolated_corpora(tmp_path, monkeypatch):
    """独立 corpora_dir，避免 sha256 markdown 缓存跨测试泄漏。"""
    monkeypatch.setattr("app.config.settings.corpora_dir", str(tmp_path / "corpora"))


@pytest.fixture(autouse=True)
def _engine_config():
    set_config(EngineConfig(
        context_limit=128_000,
        context_reserve=20_000,
        tool_concurrency=8,
        tool_timeout=30,
        tool_result_max_chars=4000,
        loop_base_timeout=120,
        loop_per_round_timeout=90,
        memo_interval=8,
    ))
    yield
    set_config(None)


_SAMPLE_MD = "# Autorun Paper\n\nAuthors: Carol Auto\n\n## Abstract\n\nEnd-to-end agent test.\n"

_STRUCTURED_JSON = json.dumps({
    "research_question": "Can an agent autonomously parse and extract?",
    "method": "End-to-end FakeLLM-driven run.",
    "findings": "Yes — ingest then extract fire as autonomous tool_calls.",
    "dataset": "Single synthetic PDF.",
    "contribution": "Demonstrates document-processing tool-use closure.",
})


class _FakeExtractLLM:
    model = "fake-extract"

    async def complete(self, messages, **kwargs) -> str:
        return _STRUCTURED_JSON

    async def stream(self, messages, **kwargs):
        yield _STRUCTURED_JSON


async def _fake_parse_pdfs(paths, language="en", max_files=200, *, _client=None):
    out = []
    for p in paths:
        p = Path(p)
        out.append({
            "name": p.name, "path": str(p), "status": "done",
            "markdown": _SAMPLE_MD, "err": None,
        })
    return out


def _make_router() -> LLMRouter:
    router = LLMRouter()
    router.add_provider(name="stub", api_key="stub-key",
                        base_url="http://stub.local/v1", models=["stub-model"])
    return router


def _tc(call_id: str, name: str, args: dict) -> dict:
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def _resp(message: dict) -> tuple[dict, str]:
    return ({"choices": [{"message": message, "finish_reason": "stop"}]}, "stub-model")


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_autonomously_ingests_then_extracts(session_factory_local, fake_r, tmp_path, monkeypatch):
    factory = session_factory_local

    # 1) 建项目 + 1 篇带 PDF 路径、尚未 OCR-done 的 paper
    pdf = tmp_path / "autorun.pdf"
    pdf.write_bytes(b"%PDF-1.4 autorun")
    async with factory() as s:
        proj = await create_project(s, {"name": "Autorun Project"})
        pid = proj.id
        paper = await add_paper(s, {"title": "Autorun Source", "source": "upload"})
        s.add(Attachment(paper_id=paper.id, path=str(pdf), mineru_status="pending"))
        await add_paper_to_project(s, pid, paper.id)
        await s.commit()

    # 2) patch MinerU 解析 + ExtractTool 的 LLM（离线确定）
    monkeypatch.setattr("app.ingest.fulltext.parse_pdfs", _fake_parse_pdfs)
    monkeypatch.setattr("app.tools.extract.get_llm_client", lambda *a, **k: _FakeExtractLLM())

    # 3) build_ctx：真实 registry（含 IngestTool/ExtractTool），绑测试 session_factory
    async def build_ctx(project_id: int) -> AgentContext:
        registry = build_registry(factory, fake_r)
        return AgentContext(
            registry=registry,
            llm_router=_make_router(),
            model_names=["stub-model"],
            system_prompt=AGENT_SYSTEM,
            tool_ids=None,
            max_rounds=6,
            wrap_up_prompt=WRAP_UP,
        )

    publisher = SubscribableEventPublisher()
    controller = RunController(
        session_factory=factory, publisher=publisher, build_ctx=build_ctx,
    )
    # auto_confirm=True → 写工具直接执行，run 端到端跑完（不挂起人工确认）
    run_id = await controller.create(
        project_id=pid, user_prompt="请先解析项目内全文，再做结构化抽取。", auto_confirm=True,
    )

    # 4) FakeLLM 决策序列：ingest → extract → 最终答复
    round_no = 0

    async def fake_llm(router, model_names, messages, tools=None, **kwargs):
        nonlocal round_no
        round_no += 1
        if round_no == 1:
            return _resp({
                "role": "assistant", "content": "先解析全文",
                "tool_calls": [_tc("c-ingest", "ingest__parse", {"project_id": pid})],
            })
        if round_no == 2:
            return _resp({
                "role": "assistant", "content": "再做结构化抽取",
                "tool_calls": [_tc("c-extract", "extract__structured", {"project_id": pid})],
            })
        return _resp({"role": "assistant", "content": "已完成解析与抽取。"})

    with patch("app.harness.engine.call_llm_with_fallback", new=fake_llm):
        await controller._drive(run_id)

    # 5) 断言 run 终态 + RunLog 出现两个 agent 自主发起的 tool_call
    async with factory() as s:
        run = await repo.get_run(s, run_id)
        events = await repo.list_events(s, run_id)

    assert run.status == "done", f"run 应跑到 done，实际 {run.status}"

    # 从事件 payload 里收集所有 tool_call 名字（tools_start / round_complete 都带 tool_calls）
    seen_names: set[str] = set()
    for e in events:
        payload = e.payload if isinstance(e.payload, dict) else {}
        for tc in payload.get("tool_calls", []) or []:
            name = tc.get("name") or tc.get("function", {}).get("name", "")
            if name:
                seen_names.add(name)

    assert "ingest__parse" in seen_names, f"RunLog 应含 agent 自主发起的 ingest__parse，实际 {seen_names}"
    assert "extract__structured" in seen_names, f"RunLog 应含 extract__structured，实际 {seen_names}"

    # 6) 副作用真实落库：OCR-done + 关联项目 + paper_extraction 行
    async with factory() as s:
        done_cnt = (await s.execute(
            select(func.count()).select_from(Attachment).where(Attachment.mineru_status == "done")
        )).scalar_one()
        assert done_cnt >= 1, "ingest 应把附件标 OCR-done"

        ext_cnt = (await s.execute(
            select(func.count()).select_from(PaperExtraction)
        )).scalar_one()
        assert ext_cnt >= 1, "extract 应写入 paper_extraction 行"

        # 强断言（codex P0-1-fix #3）：解析出的每个 OCR-done 附件的 paper_id 都已关联项目，
        # 不再用 pp_cnt>=1 这种「种子 paper 天然成立」的弱断言。
        done_paper_ids = (await s.execute(
            select(Attachment.paper_id).where(Attachment.mineru_status == "done").distinct()
        )).scalars().all()
        assert done_paper_ids, "ingest 后应有 OCR-done 附件对应的 paper"
        for did in done_paper_ids:
            pp = await find_project_paper(s, pid, did)
            assert pp is not None, f"OCR-done 附件的 paper {did} 必须已关联到项目"

        # 收敛断言：项目内不再有「带 path 且 paper 无 done 附件」的 pending 附件
        # （原始 pending 附件已回写 done），即同 path 不会被无 paths 调用再次选中。
        done_paper_sq = (
            select(Attachment.paper_id)
            .where(Attachment.mineru_status == "done", Attachment.markdown_path.isnot(None))
            .distinct()
            .scalar_subquery()
        )
        leftover = (await s.execute(
            select(func.count())
            .select_from(Attachment)
            .join(ProjectPaper, ProjectPaper.paper_id == Attachment.paper_id)
            .where(
                ProjectPaper.project_id == pid,
                Attachment.path.isnot(None),
                Attachment.path != "",
                Attachment.paper_id.notin_(done_paper_sq),
            )
        )).scalar_one()
        assert leftover == 0, f"项目内不应残留未解析（无 done 附件）的带 path 附件，实际 {leftover}"
