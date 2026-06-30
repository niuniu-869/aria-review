"""P3-3 Step 2+4 — 完整综述 e2e 离线冒烟（多格式 + 产出可验证 RunLog）。

证明「用户一句话 → agent run 自主调 review 工具 → 产出可信综述 + 可验证 RunLog」整条
链路跑通，全程离线（FakeLLM + 缓存 markdown，无 MinerU/真实 LLM）：

  1. 建 project + ≥2 篇 included 论文，含 ≥1 篇非 PDF 样例（.docx / .html）以体现
     「多格式经同一管线」（§0.6）——不真跑 MinerU，造好盘上 markdown + Attachment 元
     数据（sha256 + markdown_path）即可，重点是 e2e 链路 + RunLog 可验证性。
  2. 用 RunController 驱动一个 agent run：stub LLM 第 1 轮调 `review__generate`、
     第 2 轮纯文本 final answer（无 tool_calls）→ run 到 done（auto_confirm=True）。
  3. 断言：run done；build_runlog 非空（event_count>0、evidence_refs 每条带
     source_content_sha256、validation_summary 有 fabricated_count）；verify_runlog
     （给各篇 content_sha256 + 足够大 max_fabricated）ok=True（哈希链/seq/manifest/
     溯源/伪造门限全过）；runlog 能体现 ≥1 docx/html 样例。

模式沿用 tests/test_run_resume.py（patch app.harness.engine.call_llm_with_fallback +
RunController.create/start + 轮询终态 + 真实 ReviewTool）。conftest 强制 review 链离线。
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import uuid as _uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.agent.context import AgentContext
from app.agent.run_controller import RunController
from app.agent.runlog import build_runlog
from app.agent.runlog_verify import verify_runlog
from app.harness.config import EngineConfig, set_config
from app.harness.events import SubscribableEventPublisher
from app.harness.llm import LLMRouter
from app.harness.tools import ToolRegistry
from app.models import Attachment
from app.repositories import agent_run as repo
from app.repositories.library import add_paper
from app.repositories.project import (
    add_paper_to_project,
    create_project,
    set_inclusion,
)
from app.tools.review_tool import ReviewTool


# ---------------------------------------------------------------------------
# 测试夹具/辅助（沿用 test_run_resume.py 口径）
# ---------------------------------------------------------------------------

def _make_config() -> EngineConfig:
    return EngineConfig(
        context_limit=128_000,
        context_reserve=20_000,
        tool_concurrency=8,
        tool_timeout=30,
        tool_result_max_chars=4000,
        loop_base_timeout=120,
        loop_per_round_timeout=90,
        memo_interval=8,
    )


@pytest.fixture(autouse=True)
def patch_config():
    set_config(_make_config())
    yield
    set_config(None)


def _make_router() -> LLMRouter:
    router = LLMRouter()
    router.add_provider(
        name="stub", api_key="stub-key",
        base_url="http://stub.local/v1", models=["stub-model"],
    )
    return router


def _make_build_ctx(registry: ToolRegistry, max_rounds: int = 6):
    async def build_ctx(project_id: int) -> AgentContext:
        return AgentContext(
            registry=registry,
            llm_router=_make_router(),
            model_names=["stub-model"],
            system_prompt="你是文献综述助手",
            tool_ids=None,
            max_rounds=max_rounds,
            wrap_up_prompt="收尾",
        )
    return build_ctx


def _assistant(content: str, tool_calls: list[dict] | None = None) -> dict:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _tool_call(call_id: str, name: str, args: str = "{}") -> dict:
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": args}}


def _resp(message: dict) -> tuple[dict, str]:
    return ({"choices": [{"message": message, "finish_reason": "stop"}]}, "stub-model")


async def _wait(predicate, timeout: float = 20.0, interval: float = 0.05) -> bool:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        res = predicate()
        if asyncio.iscoroutine(res):
            res = await res
        if res:
            return True
        await asyncio.sleep(interval)
    return False


# 一篇足够长、含可被引用主题词的 markdown 全文（离线 fake review 引用 [1]/[2]/[3]）。
def _markdown_for(title: str, body: str) -> str:
    return (
        f"# {title}\n\n"
        f"## Abstract\n\n{body}\n\n"
        f"## 1. Introduction\n\n{body} {body}\n\n"
        f"## 2. Method\n\n这是一篇用于 e2e 综述测试的全文文档。{body}\n"
    )


async def _seed_paper_with_markdown(
    session_factory,
    *,
    title: str,
    content_type: str,
    src_filename: str,
    markdown_body: str,
    tmp_dir: Path,
) -> tuple[int, str]:
    """造一篇 included 候选论文：Paper + 盘上 markdown + Attachment（sha256+markdown_path）。

    content_type/src_filename 体现「源文件格式」（.pdf / .docx / .html），用以证明多格式
    经同一管线产出可读 markdown。返回 (paper_id, content_sha256)。
    """
    markdown = _markdown_for(title, markdown_body)
    # content_sha256 = 该论文「全文文档内容」哈希；与盘上 markdown 文件名 stem 一致
    # （load.py 约定 content_sha256 == Attachment.sha256 == markdown 文件名 stem）。
    content_sha256 = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    md_path = tmp_dir / f"{content_sha256}.md"
    md_path.write_text(markdown, encoding="utf-8")

    async with session_factory() as s:
        paper = await add_paper(s, {
            "title": title,
            "creators": [{"literal": "Test Author"}],
            "year": 2023,
            "source": "upload",
            "item_type": "journalArticle",
        })
        paper_id = paper.id
        att = Attachment(
            paper_id=paper_id,
            path=str(tmp_dir / src_filename),  # 源文件名体现格式（.docx/.html/.pdf）
            content_type=content_type,
            sha256=content_sha256,
            mineru_status="done",
            markdown_path=str(md_path),
        )
        s.add(att)
        await s.commit()
    return paper_id, content_sha256


# ---------------------------------------------------------------------------
# e2e 冒烟
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_e2e_review_produces_verifiable_runlog(session_factory, tmp_path):
    """一句话 → agent 调 review 工具 → run done → 产出可验证 RunLog（多格式样例）。"""
    # ---- 1. 建 project + 3 篇 included 论文（含 1 篇 .docx + 1 篇 .html 多格式样例） ----
    async with session_factory() as s:
        project = await create_project(s, {
            "name": f"E2E-Review-{_uuid.uuid4().hex[:8]}",
            "research_question": "多格式文献综述 e2e 可信链路",
        })
        project_id = project.id

    seeds = [
        # (title, content_type, src_filename) —— 覆盖 pdf / docx / html 三种源格式
        ("分析师盈余预测的影响因素研究", "application/pdf", "Zhang_2023_forecast.pdf"),
        ("机构投资者与分析师跟踪", (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ), "Li_2022_institutional.docx"),
        ("中国资本市场信息环境综述", "text/html", "Wang_2021_infoenv.html"),
    ]
    paper_ids: list[int] = []
    content_hashes: set[str] = set()
    # 按源格式分别记录各篇 sha，用于「非 PDF 样例确实进入综述语料」的真断言（P2-d）。
    sha_by_ext: dict[str, str] = {}
    for idx, (title, ctype, fname) in enumerate(seeds):
        pid, sha = await _seed_paper_with_markdown(
            session_factory,
            title=title,
            content_type=ctype,
            src_filename=fname,
            markdown_body=f"本文围绕「{title}」展开实证分析，样本与方法各有侧重。",
            tmp_dir=tmp_path,
        )
        paper_ids.append(pid)
        content_hashes.add(sha)
        sha_by_ext[Path(fname).suffix.lower()] = sha
        async with session_factory() as s:
            pp = await add_paper_to_project(
                s, project_id=project_id, paper_id=pid, added_by="user", order=idx,
            )
            await set_inclusion(s, pp.id, "included")

    # ---- 2. 注册真实 ReviewTool + RunController；stub LLM 驱动 run 到 done ----
    registry = ToolRegistry()
    registry.register(ReviewTool(session_factory))
    publisher = SubscribableEventPublisher()
    ctrl = RunController(
        session_factory=session_factory,
        publisher=publisher,
        build_ctx=_make_build_ctx(registry),
    )

    topic = "分析师跟踪/盈余预测的影响因素（中国资本市场）"
    review_args = '{"topic": "%s"}' % topic
    call_count = 0

    async def fake_llm(router, model_names, messages, tools=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # 第 1 轮：agent 自主决定调 review 工具生成综述
            return _resp(_assistant(
                "我将为本项目入选论文生成文献综述。",
                tool_calls=[_tool_call("rev-1", "review__generate", review_args)],
            ))
        # 第 2 轮：纯文本 final answer（无 tool_calls）→ run done
        return _resp(_assistant("综述已生成，引用均经安全带校验，可在 RunLog 中核验。"))

    with patch("app.harness.engine.call_llm_with_fallback", new=fake_llm):
        run_id = await ctrl.create(
            project_id=project_id,
            user_prompt="请为本项目生成一份文献综述。",
            auto_confirm=True,
        )
        ctrl.start(run_id)

        async def _is_terminal():
            async with session_factory() as s:
                run = await repo.get_run(s, run_id)
            return run is not None and run.status in ("done", "failed", "cancelled")

        assert await _wait(_is_terminal), "agent run 应在限时内到达终态"

    async with session_factory() as s:
        run = await repo.get_run(s, run_id)
    assert run.status == "done", f"run 终态应为 done，实为 {run.status}"
    assert call_count >= 2, f"LLM 应被调用 ≥2 次（调工具+final），实为 {call_count}"

    # ---- 3. build_runlog 非空 + 结构完整 ----
    async with session_factory() as s:
        runlog = await build_runlog(s, run_id)

    assert runlog, "runlog 不应为空"
    manifest = runlog["manifest"]
    assert manifest["event_count"] > 0, "应有事件（review 工具发块事件 + 终态事件）"

    evidence_refs = runlog["evidence_refs"]
    assert evidence_refs, "evidence_refs 应非空（命中语料的真引用）"
    # 每条 green/yellow 证据必须带 source_content_sha256（runlog/v1 强制溯源）
    for ref in evidence_refs:
        if ref.get("match_quality") in ("green", "yellow"):
            assert ref.get("source_content_sha256"), (
                f"green/yellow 证据须带 source_content_sha256，实为 {ref!r}"
            )

    # validation_summary 存在且 fabricated_count 字段在 manifest（取自校验汇总）
    assert "fabricated_count" in manifest, "manifest 应含 fabricated_count"

    # ---- 4. verify_runlog ok=True（哈希链/seq/manifest/溯源/伪造门限全过） ----
    report = verify_runlog(
        runlog,
        corpus_content_hashes=content_hashes,
        max_fabricated=100,  # 离线 fake review 不应有伪造引用，门限给足够大
    )
    assert report.ok, (
        f"verify_runlog 应通过，未过项: "
        f"{[k for k, v in report.checks.items() if not v]}; errors={report.errors}"
    )
    # 逐项关键 check 显式断言（防 ok 因某项缺失而虚高）
    assert report.checks["hash_chain"], report.errors
    assert report.checks["seq_contiguous"], report.errors
    assert report.checks["manifest_counts"], report.errors
    assert report.checks["content_hash_match"], report.errors
    assert report.checks["evidence_traceable"], report.errors
    assert report.checks["zero_fabrication"], report.errors

    # ---- 5. 非 PDF 样例（docx/html）确实经同一管线进入综述语料（P2-d 真断言） ----
    # 不靠「evidence 命中 docx/html」（fake 综述只引前几篇，docx/html 未必被引用，那样脆弱），
    # 而直接断言：喂给综述的 records 的 content_sha256 集合含 docx 与 html 样例的 sha——
    # 证明非 PDF 论文经 load_project_corpus 同一管线进入了综述输入（非笼统 ⊆ 混合集合）。
    from app.review.load import load_project_corpus

    async with session_factory() as s:
        _paper_markdowns, _records, _skipped = await load_project_corpus(s, project_id)
    corpus_record_shas = {
        r.get("content_sha256") for r in _records if r.get("content_sha256")
    }
    # 三篇均有可读 markdown + sha，应全部进入语料（无被跳过），即非平凡集合。
    assert not _skipped, f"三篇样例均应进入语料、无跳过，实际 skipped={_skipped!r}"
    assert corpus_record_shas == content_hashes, (
        "喂给综述的 records 的 content_sha256 集合应等于三篇样例的全集"
    )
    # 关键：非 PDF 样例（docx/html）的 sha 确在综述语料里——能捕获「docx/html 未进语料」的退化。
    assert sha_by_ext[".docx"] in corpus_record_shas, "docx 样例须经同管线进入综述语料"
    assert sha_by_ext[".html"] in corpus_record_shas, "html 样例须经同管线进入综述语料"

    # 兼容性：证据溯源哈希仍须全部落在语料集合内（证据不可凭空，溯源闭环）。
    evidence_shas = {
        r.get("source_content_sha256") for r in evidence_refs
        if r.get("source_content_sha256")
    }
    assert evidence_shas <= content_hashes, "证据溯源哈希应全部落在多格式语料集合内"
    assert evidence_shas, "应至少有一条可溯源证据"
