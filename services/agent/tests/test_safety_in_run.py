"""P3-2: 安全带强制 review 工具 — run 内端到端校验。

覆盖:
  - load_project_corpus: 从 project 加载 included 论文 → (paper_markdowns, records)，
    records 每条带 content_sha256 (= Attachment.sha256)。
  - ReviewTool._execute: 跑 map+reduce (复用 run_review，内部经 GuardedStream)：
      * emit 块级事件: review_complete (一条完整全文，非逐 token) /
        validation_summary / evidence_refs / review_progress
      * 伪造引用计入 validation_summary.fabricated_citations / fabricated_spans
      * 回写 live LoopState: state.evidence_refs (每条带 source_content_sha256) /
        state.validation_summary (含 fabricated_spans)
  - state 注入 + save_state 落库: step_once 前注入 ctx.tool_context["state"]，
    工具回写后 save_state 把 evidence_refs / validation_summary 落 agent_run 列。
  - 安全带不可绕过: ReviewTool 不接受"跳过校验"参数。

伪造引用构造手法 (沿用 test_review_synthesis 既有手法):
  patch app.review.synthesis.LLMRouter (has_any_key=False → Fake) +
  patch app.review.synthesis._build_fake_review 返回含真引用 (命中 records) +
  超界编号 [99] (越界 → cite_check 判 red → 伪造) 的综述文本。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.tools.review_tool import ReviewTool
from app.review.load import load_project_corpus
from app.repositories.library import add_paper
from app.repositories.project import (
    create_project,
    add_paper_to_project,
    set_inclusion,
)
from app.models import Attachment


# ======================================================================
# 测试夹具：在 project 下建 2 篇 included 论文 + markdown 文件 + Attachment
# ======================================================================

_SHA_A = "a" * 64
_SHA_B = "f" * 64
_DOI_A = "10.1234/alpha"
_DOI_B = "10.5678/beta"


async def _seed_project(session_factory, tmp_path) -> int:
    """建 1 个 project + 2 篇 included 论文（含 markdown 文件 + Attachment.sha256）。

    返回 project_id。
    """
    async with session_factory() as s:
        proj = await create_project(s, {"name": "P3-2 测试项目"})

        # 论文 A
        md_a = tmp_path / f"{_SHA_A}.md"
        md_a.write_text("# Paper Alpha\n\n关于主题 X 的实证研究全文。", encoding="utf-8")
        paper_a = await add_paper(s, {
            "title": "Paper Alpha",
            "creators": [{"literal": "Author A"}],
            "year": 2020,
            "doi": _DOI_A,
            "source": "upload",
        })
        s.add(Attachment(
            paper_id=paper_a.id,
            path=str(tmp_path / "a.pdf"),
            sha256=_SHA_A,
            mineru_status="done",
            markdown_path=str(md_a),
        ))

        # 论文 B
        md_b = tmp_path / f"{_SHA_B}.md"
        md_b.write_text("# Paper Beta\n\n关于主题 X 的综述全文。", encoding="utf-8")
        paper_b = await add_paper(s, {
            "title": "Paper Beta",
            "creators": [{"literal": "Author B"}],
            "year": 2021,
            "doi": _DOI_B,
            "source": "upload",
        })
        s.add(Attachment(
            paper_id=paper_b.id,
            path=str(tmp_path / "b.pdf"),
            sha256=_SHA_B,
            mineru_status="done",
            markdown_path=str(md_b),
        ))
        await s.commit()

        # 关联 + included
        pp_a = await add_paper_to_project(s, proj.id, paper_a.id, added_by="user", order=0)
        pp_b = await add_paper_to_project(s, proj.id, paper_b.id, added_by="user", order=1)
        await set_inclusion(s, pp_a.id, "included")
        await set_inclusion(s, pp_b.id, "included")

        return proj.id


# ======================================================================
# load_project_corpus
# ======================================================================

class TestLoadProjectCorpus:
    @pytest.mark.asyncio
    async def test_records_carry_content_sha256(self, session_factory, tmp_path):
        project_id = await _seed_project(session_factory, tmp_path)
        async with session_factory() as s:
            paper_markdowns, records, skipped = await load_project_corpus(s, project_id)

        assert len(paper_markdowns) == 2
        assert len(records) == 2
        assert skipped == []
        # records 1-based idx，与综述 [n] 对齐
        assert [r["idx"] for r in records] == [1, 2]
        # 每条 record 带 content_sha256 (= Attachment.sha256)
        shas = {r["content_sha256"] for r in records}
        assert shas == {_SHA_A, _SHA_B}
        # markdown 实际读到
        assert all(pm["markdown"] for pm in paper_markdowns)

    @pytest.mark.asyncio
    async def test_only_included_papers(self, session_factory, tmp_path):
        """excluded / candidate 论文不应进入 records。"""
        project_id = await _seed_project(session_factory, tmp_path)
        # 再加一篇 candidate（不 included）
        async with session_factory() as s:
            paper_c = await add_paper(s, {
                "title": "Paper Gamma", "year": 2022, "doi": "10.9/gamma", "source": "upload",
            })
            await add_paper_to_project(s, project_id, paper_c.id, order=2)  # 默认 candidate
            await s.commit()

        async with session_factory() as s:
            _, records, _ = await load_project_corpus(s, project_id)
        titles = {r["title"] for r in records}
        assert "Paper Gamma" not in titles

    @pytest.mark.asyncio
    async def test_skips_paper_without_markdown(self, session_factory, tmp_path):
        """codex P1-4：included 论文 B 无可读 markdown（无 Attachment / 文件不存在）→
        不进 records/paper_markdowns，且记入 skipped；论文 A（有 markdown+sha256）正常纳入。

        绝不喂空 markdown 进语料（静默垃圾输入 + 无溯源证据）。
        """
        async with session_factory() as s:
            proj = await create_project(s, {"name": "P1-4 跳过空 markdown"})

            # 论文 A：有 markdown 文件 + Attachment.sha256
            md_a = tmp_path / f"{_SHA_A}.md"
            md_a.write_text("# Paper Alpha\n\n有正文。", encoding="utf-8")
            paper_a = await add_paper(s, {
                "title": "Paper Alpha", "creators": [{"literal": "Author A"}],
                "year": 2020, "doi": _DOI_A, "source": "upload",
            })
            s.add(Attachment(
                paper_id=paper_a.id, path=str(tmp_path / "a.pdf"),
                sha256=_SHA_A, mineru_status="done", markdown_path=str(md_a),
            ))

            # 论文 B：无 Attachment（无 markdown、无 sha256）
            paper_b = await add_paper(s, {
                "title": "Paper Beta", "creators": [{"literal": "Author B"}],
                "year": 2021, "doi": _DOI_B, "source": "upload",
            })
            await s.commit()

            pp_a = await add_paper_to_project(s, proj.id, paper_a.id, added_by="user", order=0)
            pp_b = await add_paper_to_project(s, proj.id, paper_b.id, added_by="user", order=1)
            await set_inclusion(s, pp_a.id, "included")
            await set_inclusion(s, pp_b.id, "included")
            project_id = proj.id

        async with session_factory() as s:
            paper_markdowns, records, skipped = await load_project_corpus(s, project_id)

        # 只含 A
        assert len(records) == 1
        assert records[0]["title"] == "Paper Alpha"
        assert len(paper_markdowns) == 1
        # B 在 skipped
        assert len(skipped) == 1
        assert skipped[0].get("paper_id") == paper_b.id

    @pytest.mark.asyncio
    async def test_skips_paper_with_missing_markdown_file(self, session_factory, tmp_path):
        """有 Attachment.sha256 但 markdown 文件读失败（路径不存在）→ 空串 → 跳过并记 skipped。"""
        async with session_factory() as s:
            proj = await create_project(s, {"name": "P1-4 markdown 文件缺失"})
            paper_b = await add_paper(s, {
                "title": "Paper NoFile", "creators": [{"literal": "B"}],
                "year": 2021, "doi": _DOI_B, "source": "upload",
            })
            # markdown_path 指向不存在的文件 → read_text 失败 → 空串
            s.add(Attachment(
                paper_id=paper_b.id, path=str(tmp_path / "b.pdf"),
                sha256=_SHA_B, mineru_status="done",
                markdown_path=str(tmp_path / "does_not_exist.md"),
            ))
            await s.commit()
            pp_b = await add_paper_to_project(s, proj.id, paper_b.id, added_by="user", order=0)
            await set_inclusion(s, pp_b.id, "included")
            project_id = proj.id

        async with session_factory() as s:
            paper_markdowns, records, skipped = await load_project_corpus(s, project_id)

        assert records == []
        assert paper_markdowns == []
        assert len(skipped) == 1
        assert skipped[0].get("paper_id") == paper_b.id


# ======================================================================
# ReviewTool — 块级事件 + 伪造计入日志 + state 回写
# ======================================================================

# 含真引用（命中 records）+ 超界编号 [99]（伪造）的 fake 综述
_FAKE_REVIEW_WITH_FABRICATION = (
    "## 1. 引言\n\n"
    f"已有研究表明该现象普遍存在 [1]，另据 {_DOI_A} 的实证分析显示重要规律。\n\n"
    "## 2. 主要发现\n\n"
    "综合来看 [2] 提供了关键证据，但也有学者 [99] 提出截然不同的结论。\n\n"
    "## 3. 结论\n\n综合上述文献，仍有研究空间 [1]。\n"
)


async def _run_review_tool(session_factory, project_id):
    """调 ReviewTool._execute，返回 (tool_result, emitted_events, state)。"""
    from app.harness.engine import LoopState

    emitted: list[dict] = []

    async def _emit(ev: dict) -> None:
        emitted.append(ev)

    state = LoopState(messages=[])
    context = {
        "run_id": 123,
        "project_id": project_id,
        "emit": _emit,
        "session_factory": session_factory,
        "override": None,
        "state": state,  # step_once 注入的 live LoopState
    }

    tool = ReviewTool(session_factory)

    with patch("app.review.synthesis.LLMRouter") as MockRouter, \
         patch(
             "app.review.synthesis._build_fake_review",
             return_value=_FAKE_REVIEW_WITH_FABRICATION,
         ):
        MockRouter.from_config.return_value.has_any_key.return_value = False
        result = await tool.execute(
            "generate",
            {"topic": "主题 X 的研究进展"},
            context,
        )
    return result, emitted, state


class TestReviewToolInRun:
    @pytest.mark.asyncio
    async def test_review_tool_emits_blocks_and_logs_fabrication(
        self, session_factory, tmp_path
    ):
        project_id = await _seed_project(session_factory, tmp_path)
        result, emitted, state = await _run_review_tool(session_factory, project_id)

        assert result.success, f"工具应成功: {result.error}"

        types = [e.get("type") for e in emitted]

        # 1) review_complete 是一条完整全文块事件（非逐 token）
        completes = [e for e in emitted if e.get("type") == "review_complete"]
        assert len(completes) == 1, f"应恰有一条 review_complete，实得 {types}"
        review_md = completes[0].get("review_md", "")
        assert "引言" in review_md and len(review_md) > 50

        # 2) validation_summary 块事件，含伪造引用
        vsum_events = [e for e in emitted if e.get("type") == "validation_summary"]
        assert len(vsum_events) == 1
        vsum = vsum_events[0]
        assert vsum.get("fabricated_citations", 0) >= 1, "超界 [99] 应被判为伪造"
        assert vsum.get("fabricated_spans"), "fabricated_spans 应非空"

        # 3) evidence_refs 块事件
        eref_events = [e for e in emitted if e.get("type") == "evidence_refs"]
        assert len(eref_events) == 1

        # 4) state 回写：evidence_refs 非空且每条带 source_content_sha256
        assert state.evidence_refs, "state.evidence_refs 应非空"
        assert all(isinstance(e, dict) for e in state.evidence_refs), "应为 JSON-able dict"
        assert all(
            e.get("source_content_sha256") in (_SHA_A, _SHA_B)
            for e in state.evidence_refs
        ), "每条证据应携带文档内容溯源哈希"

        # 5) state.validation_summary 含 fabricated_spans
        assert state.validation_summary is not None
        assert "fabricated_spans" in state.validation_summary
        assert state.validation_summary.get("fabricated_citations", 0) >= 1

    @pytest.mark.asyncio
    async def test_validator_unavailable_fails_closed(self, session_factory, tmp_path):
        """codex P0-2 连带：reduce 校验器崩溃 (ValidationUnavailableError) →
        generate_review 产 error 事件 → ReviewTool 不发 review_complete、success=False。

        绝不能让未校验文本作为 review_complete 放行。
        """
        project_id = await _seed_project(session_factory, tmp_path)

        from app.harness.engine import LoopState

        emitted: list[dict] = []

        async def _emit(ev: dict) -> None:
            emitted.append(ev)

        state = LoopState(messages=[])
        context = {
            "run_id": 123,
            "project_id": project_id,
            "emit": _emit,
            "session_factory": session_factory,
            "override": None,
            "state": state,
        }
        tool = ReviewTool(session_factory)

        def _boom(*args, **kwargs):
            raise RuntimeError("校验器崩溃（模拟）")

        # patch GuardedStream 内调用的校验函数使其崩溃 → fail-closed
        with patch("app.review.synthesis.LLMRouter") as MockRouter, \
             patch(
                 "app.review.synthesis._build_fake_review",
                 return_value=_FAKE_REVIEW_WITH_FABRICATION,
             ), \
             patch("app.safety.guarded_stream.check_citations_against_records", _boom):
            MockRouter.from_config.return_value.has_any_key.return_value = False
            result = await tool.execute(
                "generate", {"topic": "主题 X 的研究进展"}, context,
            )

        # 1) 工具失败
        assert result.success is False, "校验器崩溃时工具应失败（fail-closed）"
        # 2) 绝不发 review_complete（未校验文本不得放行）
        types = [e.get("type") for e in emitted]
        assert "review_complete" not in types, (
            f"校验器崩溃时不得发 review_complete，实得 {types}"
        )

    @pytest.mark.asyncio
    async def test_no_skip_validation_param(self, session_factory, tmp_path):
        """安全带不可绕过：ReviewTool 的 generate schema 不暴露任何跳过校验参数。"""
        tool = ReviewTool(session_factory)
        schema = tool.action_schemas.get("generate", {})
        props = set(schema.get("properties", {}).keys())
        forbidden = {"skip_validation", "no_guard", "skip_guard", "strategy", "disable_safety"}
        assert not (props & forbidden), f"不应暴露绕过校验参数: {props & forbidden}"

    @pytest.mark.asyncio
    async def test_not_a_write_tool(self, session_factory):
        """ReviewTool 不是写工具（不需确认 gate）。"""
        tool = ReviewTool(session_factory)
        assert "write" not in tool.tags

    @pytest.mark.asyncio
    async def test_emit_failure_does_not_lose_validated_review(
        self, session_factory, tmp_path
    ):
        """codex P1-5：emit 在发 review_complete 时崩溃 → 不得让"已成功校验并回写的
        review"被判失败；state.evidence_refs/validation_summary 仍与校验结果一致（不半套）。

        emit 只是通知通道；校验已通过且 state 已完整回写时，emit 失败不应丢失成功结果。
        """
        from app.harness.engine import LoopState

        project_id = await _seed_project(session_factory, tmp_path)

        emitted: list[dict] = []

        async def _emit(ev: dict) -> None:
            # 在发 review_complete 时崩溃（其余事件正常）
            if ev.get("type") == "review_complete":
                raise RuntimeError("emit 通道崩溃（模拟）")
            emitted.append(ev)

        state = LoopState(messages=[])
        context = {
            "run_id": 123,
            "project_id": project_id,
            "emit": _emit,
            "session_factory": session_factory,
            "override": None,
            "state": state,
        }
        tool = ReviewTool(session_factory)

        with patch("app.review.synthesis.LLMRouter") as MockRouter, \
             patch(
                 "app.review.synthesis._build_fake_review",
                 return_value=_FAKE_REVIEW_WITH_FABRICATION,
             ):
            MockRouter.from_config.return_value.has_any_key.return_value = False
            result = await tool.execute(
                "generate", {"topic": "主题 X 的研究进展"}, context,
            )

        # 1) emit 失败不丢失成功校验（工具仍成功）
        assert result.success is True, (
            f"emit 失败不应让已校验的 review 被判失败: {result.error}"
        )
        # 2) state 与校验结果一致（完整回写，非半套）
        assert state.evidence_refs, "state.evidence_refs 应已完整回写"
        assert all(
            e.get("source_content_sha256") in (_SHA_A, _SHA_B)
            for e in state.evidence_refs
        )
        assert state.validation_summary is not None
        assert "fabricated_spans" in state.validation_summary
        assert state.validation_summary.get("fabricated_citations", 0) >= 1


# ======================================================================
# state 回写 → save_state 落库
# ======================================================================

class TestStateWritebackPersisted:
    @pytest.mark.asyncio
    async def test_save_state_persists_evidence_and_validation(
        self, session_factory, tmp_path
    ):
        """ReviewTool 回写 state 后，save_state 把 evidence_refs / validation_summary 落库。"""
        from app.repositories.agent_run import create_run, save_state, get_run

        project_id = await _seed_project(session_factory, tmp_path)

        # 建一个 run
        async with session_factory() as s:
            run = await create_run(s, project_id=project_id, plan="生成综述")
            run_id = run.id

        result, emitted, state = await _run_review_tool(session_factory, project_id)
        assert result.success

        # 模拟 step_once 之后 save_state（state 是单一真源）
        async with session_factory() as s:
            await save_state(s, run_id, state)

        # 重新载入，确认落库
        async with session_factory() as s:
            run2 = await get_run(s, run_id)
        assert run2.evidence_refs, "evidence_refs 应已落库"
        assert all(
            e.get("source_content_sha256") in (_SHA_A, _SHA_B)
            for e in run2.evidence_refs
        )
        assert run2.validation_summary is not None
        assert run2.validation_summary.get("fabricated_citations", 0) >= 1
