"""B4a 精读溯源测试 — map 阶段 key_point.source_quote 定位回 block。

覆盖:
  - 离线 (FakeLLM 强制): summarize_paper 把 source_quote 定位回 block（页/块/锚点）
  - KeyPoint to_dict/from_dict 往返保真（含 B4a 溯源字段）
  - EvidenceRef 新增字段往返保真 + from_record 向后兼容（新字段恒 None）
  - 真实 LLM (有 key 才跑): 精读产出带 source_quote 的 key_points 且能定位回 block

离线分支：无 @pytest.mark.allow_real_llm_router 时 conftest 的 _no_real_llm 强制
LLMRouter.has_any_key()==False → summarize_paper 走 FakeLLMClient（确定/无成本）。
"""
from __future__ import annotations

import os

import pytest

from app.review.read import KeyPoint, summarize_paper
from app.safety.evidence import EvidenceRef
from helpers_contract import contract_content_list, contract_full_markdown


# ======================================================================
# 离线 (FakeLLM 强制) — 溯源定位闭环
# ======================================================================

@pytest.mark.asyncio
async def test_summarize_paper_locates_source_quote_offline():
    """FakeLLM 的 key_point.source_quote 能被 EvidenceResolver 定位回 block（页/块/锚点）。

    构造一个 content_list，其正文块文本包含 Fake 产出的 source_quote 字符串，
    使 resolver.resolve() 命中。
    """
    content_list = [
        {"type": "text",
         "text": "Intro. Fake source quote for provenance test. More.",
         "text_level": None, "page_idx": 0, "bbox": [10.0, 20.0, 500.0, 60.0]},
    ]
    summary = await summarize_paper(
        markdown="some markdown body",
        meta={"paper_id": "7", "title": "T"},
        topic="x",
        content_list=content_list,
    )

    assert not summary.is_error(), summary.error
    assert summary.key_points, "应产出 key_points"
    # 至少一条 key_point 带非空 source_quote
    assert any(kp.source_quote.strip() for kp in summary.key_points), "应有 source_quote"

    # 被定位的 key_point：block_idx / page_no / anchor_id 均落地
    located = [kp for kp in summary.key_points if kp.block_idx is not None]
    assert located, "至少一条 source_quote 应能定位回 block"
    kp = located[0]
    assert kp.block_idx is not None
    assert kp.page_no is not None and kp.page_no >= 1
    assert kp.anchor_id is not None
    # anchor_id 形如 a{paper_id}_{block_idx}_{seq}
    assert kp.anchor_id.startswith("a7_"), kp.anchor_id
    # bbox / section_title 也随定位写回（本 fixture 给了 bbox）
    assert kp.bbox == [10.0, 20.0, 500.0, 60.0]


@pytest.mark.asyncio
async def test_summarize_paper_without_content_list_skips_location_offline():
    """不传 content_list 时仍正常摘要，但溯源字段保持 None（向后兼容）。"""
    summary = await summarize_paper(
        markdown="some markdown body",
        meta={"paper_id": "8", "title": "T2"},
        topic="x",
    )
    assert not summary.is_error(), summary.error
    assert summary.key_points
    # source_quote 仍由 Fake 产出（新字段流通），但未定位 → block_idx/anchor_id 为 None
    assert all(kp.block_idx is None for kp in summary.key_points)
    assert all(kp.anchor_id is None for kp in summary.key_points)


@pytest.mark.asyncio
async def test_summarize_paper_unlocatable_quote_keeps_summary_offline():
    """source_quote 在 content_list 中找不到时不报错，定位字段保持 None，摘要照常返回。"""
    content_list = [
        {"type": "text", "text": "完全无关的正文内容，没有 fake 引文。",
         "text_level": None, "page_idx": 0, "bbox": None},
    ]
    summary = await summarize_paper(
        markdown="body",
        meta={"paper_id": "9", "title": "T3"},
        topic="x",
        content_list=content_list,
    )
    assert not summary.is_error(), summary.error
    assert summary.key_points
    # Fake 的 source_quote 不在这份 content_list 中 → 未命中
    assert all(kp.block_idx is None for kp in summary.key_points)


# ======================================================================
# KeyPoint 往返保真（含 B4a 溯源字段）
# ======================================================================

def test_keypoint_roundtrip_preserves_provenance():
    kp = KeyPoint(
        claim="GNN 在大规模引用网络上优于传统方法。",
        section="4.2 Results",
        source_quote="Our GNN model achieves an F1 of 0.91 on the citation graph.",
        block_idx=12,
        page_no=5,
        bbox=[72.0, 100.0, 523.0, 180.0],
        section_title="4. Experiments",
        anchor_id="a7_12_0",
    )
    kp2 = KeyPoint.from_dict(kp.to_dict())
    assert kp2.claim == kp.claim
    assert kp2.section == kp.section
    assert kp2.source_quote == kp.source_quote
    assert kp2.block_idx == kp.block_idx
    assert kp2.page_no == kp.page_no
    assert kp2.bbox == kp.bbox
    assert kp2.section_title == kp.section_title
    assert kp2.anchor_id == kp.anchor_id


def test_keypoint_from_dict_legacy_shape_backward_compatible():
    """旧形态（只有 claim/section）仍能 from_dict，新字段取默认值。"""
    kp = KeyPoint.from_dict({"claim": "c", "section": "s"})
    assert kp.claim == "c"
    assert kp.section == "s"
    assert kp.source_quote == ""
    assert kp.block_idx is None
    assert kp.page_no is None
    assert kp.anchor_id is None


# ======================================================================
# EvidenceRef 新增字段往返 + from_record 向后兼容
# ======================================================================

_RECORD = {
    "title": "Deep Learning for Bibliometrics",
    "authors": "Doe J",
    "year": 2023,
    "doi": "10.1234/abc",
}


def test_evidence_ref_new_fields_roundtrip():
    """显式设置 B4a 块级字段后，to_dict→from_dict 保真。"""
    ref = EvidenceRef.from_record(paper_id=1, record=_RECORD, span="[1]", claim="见[1]。")
    ref.page_no = 5
    ref.block_idx = 12
    ref.bbox = [72.0, 100.0, 523.0, 180.0]
    ref.table_idx = 3
    ref.cell_row = 2
    ref.cell_col = 4
    ref.section_title = "4. Experiments"
    ref.anchor_id = "a7_12_0"

    ref2 = EvidenceRef.from_dict(ref.to_dict())
    assert ref2.page_no == 5
    assert ref2.block_idx == 12
    assert ref2.bbox == [72.0, 100.0, 523.0, 180.0]
    assert ref2.table_idx == 3
    assert ref2.cell_row == 2
    assert ref2.cell_col == 4
    assert ref2.section_title == "4. Experiments"
    assert ref2.anchor_id == "a7_12_0"


def test_evidence_ref_from_record_new_fields_default_none():
    """from_record 不接受新字段 → 它们恒为 None（向后兼容，签名未变）。"""
    ref = EvidenceRef.from_record(paper_id=1, record=_RECORD)
    assert ref.page_no is None
    assert ref.block_idx is None
    assert ref.bbox is None
    assert ref.table_idx is None
    assert ref.cell_row is None
    assert ref.cell_col is None
    assert ref.section_title is None
    assert ref.anchor_id is None


def test_evidence_ref_from_dict_missing_new_fields_default_none():
    """旧序列化 dict（无新字段）→ from_dict 把新字段补 None（向后兼容）。"""
    minimal = {"paper_id": 3, "record_hash": "a" * 64}
    ref = EvidenceRef.from_dict(minimal)
    assert ref.page_no is None
    assert ref.block_idx is None
    assert ref.bbox is None
    assert ref.anchor_id is None


# ======================================================================
# 真实 LLM (有 DEEPSEEK_API_KEY 才跑；无 key → SKIP，绝不 fake-pass)
# ======================================================================

@pytest.mark.allow_real_llm_router
@pytest.mark.skipif(not os.getenv("DEEPSEEK_API_KEY"), reason="需真实 DEEPSEEK_API_KEY")
@pytest.mark.asyncio
async def test_summarize_paper_real_llm_locates_blocks():
    """真实 DeepSeek 精读 → key_points 带 source_quote 且能定位回 block(页/块)。"""
    full_md = contract_full_markdown()
    content_list = contract_content_list()
    summary = await summarize_paper(
        markdown=full_md,
        meta={"paper_id": "99", "title": "Deep Learning Approaches for Bibliometric Network Analysis"},
        topic="graph neural networks for bibliometric networks",
        content_list=content_list,
    )
    assert not summary.is_error(), summary.error
    assert summary.key_points, "真实精读应产出 key_points"
    assert any(kp.source_quote.strip() for kp in summary.key_points), "应有 source_quote"
    located = [kp for kp in summary.key_points if kp.block_idx is not None and kp.page_no]
    assert located, "至少一条 key_point 的 source_quote 应能定位回原文 block(页/块)"


# ======================================================================
# B4b/B4c — build_provenance_and_anchors + run_review provenance_map 贯通
# ======================================================================

from app.review.read import PaperSummary  # noqa: E402
from app.review.synthesis import build_provenance_and_anchors  # noqa: E402
from app.review.orchestrate import run_review  # noqa: E402
from app.harness.engine import LoopState  # noqa: E402


def test_build_provenance_and_anchors_unit():
    """纯函数：已定位引用被包裹 occurrence anchor，未定位引用原样保留（诚实）。"""
    located_kp = KeyPoint(
        claim="GNNs improve link prediction",
        section="Results",
        source_quote="GNN improves accuracy",
        block_idx=3,
        page_no=2,
        bbox=[10.0, 20.0, 500.0, 60.0],
        section_title="4. Experiments",
        anchor_id="a10_3_0",
    )
    summaries = [
        PaperSummary(paper_id="10", title="P1", key_points=[located_kp]),
        # 第二篇无已定位 key_point（[2] 不应被包裹）
        PaperSummary(paper_id="11", title="P2",
                     key_points=[KeyPoint(claim="other", section="x")]),
    ]
    records = [
        {"idx": 1, "paper_id": 10, "attachment_id": 55},
        {"idx": 2, "paper_id": 11, "attachment_id": 56},
    ]
    review_md = "GNNs improve link prediction by a large margin [1]. Another claim [2]."

    annotated, pmap = build_provenance_and_anchors(review_md, summaries, records)

    assert "[[anchor:" in annotated
    # [1] 被包裹为 occurrence anchor
    assert "[[anchor:a10_3_0__occ0]][1][[/anchor]]" in annotated
    # [2] 无 located kp → 原样保留，且其前后不带 anchor 包裹
    assert "[2]" in annotated
    assert "[[anchor:" not in annotated.split("Another claim ")[1]
    # provenance_map
    assert len(pmap) >= 1
    wrapped_id = "a10_3_0__occ0"
    assert wrapped_id in pmap
    entry = pmap[wrapped_id]
    assert entry["paper_id"] == 10
    assert entry["block_idx"] == 3
    assert entry["page_no"] == 2
    assert entry["attachment_id"] == 55
    assert entry["quote"] == "GNN improves accuracy"


def test_build_provenance_and_anchors_robust_empty():
    """空 review_md / 无定位 → 原样返回，provenance_map 空。"""
    annotated, pmap = build_provenance_and_anchors("", [], [])
    assert annotated == ""
    assert pmap == {}
    # 有摘要但都无定位
    summaries = [PaperSummary(paper_id="1", title="T",
                              key_points=[KeyPoint(claim="c", section="s")])]
    md = "Some claim [1]."
    annotated2, pmap2 = build_provenance_and_anchors(md, summaries, [{"idx": 1, "paper_id": 1}])
    assert annotated2 == md  # 未定位引用原样保留，绝不伪造 anchor
    assert pmap2 == {}


def test_no_anchor_when_ambiguous_no_overlap():
    """零伪造（codex Wave2 P1）：某篇有【多条】已定位 key_point，但引用上下文与任何一条都无 token
    重叠时，不得在多候选里乱指——该 [n] 原样保留，不锚定。"""
    kp1 = KeyPoint(claim="alpha beta gamma", section="s", source_quote="alpha beta gamma",
                   block_idx=1, page_no=1, anchor_id="a1_1_0")
    kp2 = KeyPoint(claim="delta epsilon zeta", section="s", source_quote="delta epsilon zeta",
                   block_idx=2, page_no=1, anchor_id="a1_2_1")
    summaries = [PaperSummary(paper_id="1", title="T", key_points=[kp1, kp2])]
    # 引用上下文（'omega lambda'）与两条 located 的 token 均无交集 → 多候选无信号 → 不锚定
    md = "omega lambda totally unrelated context [1]."
    annotated, pmap = build_provenance_and_anchors(md, summaries, [{"idx": 1, "paper_id": 1}])
    assert "[[anchor:" not in annotated, "多候选无重叠时不应乱指,应原样保留 [n]"
    assert pmap == {}


def test_single_located_anchors_even_without_overlap():
    """仅 1 条已定位时即使上下文无重叠也锚定（无歧义,是本篇唯一溯源,非伪造）。"""
    kp = KeyPoint(claim="alpha beta", section="s", source_quote="alpha beta",
                  block_idx=5, page_no=3, anchor_id="a1_5_0")
    summaries = [PaperSummary(paper_id="1", title="T", key_points=[kp])]
    md = "omega lambda unrelated [1]."
    annotated, pmap = build_provenance_and_anchors(md, summaries, [{"idx": 1, "paper_id": 1}])
    assert "[[anchor:" in annotated
    assert len(pmap) == 1
    entry = next(iter(pmap.values()))
    assert entry["block_idx"] == 5 and entry["page_no"] == 3


@pytest.mark.asyncio
async def test_run_review_emits_provenance_offline():
    """离线（FakeLLM）：run_review 产出 provenance_map，且 review_md 注入 anchor。

    FakeLLM 的 source_quote 固定为 'Fake source quote for provenance test.'；
    content_list 含该文本 → 定位命中 → provenance_map 有带 page/block 的条目。
    """
    content_list = [
        {"type": "text",
         "text": "Intro. Fake source quote for provenance test. End.",
         "text_level": None, "page_idx": 0, "bbox": None},
    ]
    paper_markdowns = [{
        "meta": {"paper_id": "10", "title": "T"},
        "markdown": "some body",
        "content_list": content_list,
    }]
    records = [{"idx": 1, "paper_id": 10, "attachment_id": 55}]

    result = await run_review("topic", paper_markdowns, records)

    assert result["error"] is None
    pmap = result["provenance_map"]
    assert pmap, "应至少产出 1 条 provenance"
    located = [v for v in pmap.values() if v.get("block_idx") is not None and v.get("page_no")]
    assert located, "至少一条 provenance 带 block_idx + page_no"
    assert "[[anchor:" in result["review_md"]
    # stats 计数同步
    assert result["stats"].get("provenance_entries", 0) >= 1


def test_loopstate_provenance_roundtrip():
    """LoopState.provenance_map 序列化往返 + 旧快照容错。"""
    s = LoopState(messages=[], provenance_map={"a1": {"paper_id": 1}})
    assert LoopState.from_json(s.to_json()).provenance_map == {"a1": {"paper_id": 1}}
    # 旧快照（无 provenance_map 键）→ None
    assert LoopState.from_json({"messages": []}).provenance_map is None


@pytest.mark.allow_real_llm_router
@pytest.mark.skipif(not os.getenv("DEEPSEEK_API_KEY"), reason="需真实 DEEPSEEK_API_KEY")
@pytest.mark.asyncio
async def test_run_review_provenance_real_llm_end_to_end():
    """真实 DeepSeek：综述产出 provenance_map，且 review_md 的 anchor 能映射回带 page/block 的条目。

    注（B4b/B4c 实测发现）：synthesis skill 对"单篇文献"会拒绝写综述（要求至少 3-5 篇
    做横向比较/共识分歧），返回不含任何 [n] 的说明文本 → 无引用可锚定。这是真实模型对
    单篇语料的合理行为，并非 provenance 后处理的缺陷。因此本端到端用例喂 3 篇真实语料
    （同一 fixture 论文，不同 paper_id/idx），使 reduce 真正产出带 [n] 引用的多篇综述，
    从而验证「已定位 key_point → review_md anchor → provenance_map 条目」整条链路。
    """
    full_md = contract_full_markdown()
    content_list = contract_content_list()
    paper_markdowns = [
        {"meta": {"paper_id": str(pid),
                  "title": f"Deep Learning Approaches for Bibliometric Network Analysis ({tag})"},
         "markdown": full_md, "content_list": content_list}
        for pid, tag in [(10, "A"), (11, "B"), (12, "C")]
    ]
    records = [
        {"idx": i + 1, "paper_id": pid, "attachment_id": 50 + pid,
         "title": "Deep Learning...", "content_sha256": "x"}
        for i, pid in enumerate([10, 11, 12])
    ]
    result = await run_review("graph neural networks for bibliometric analysis", paper_markdowns, records)
    assert result["error"] is None
    pmap = result["provenance_map"]
    assert pmap, "真实综述应产出至少一条 provenance"
    located = [v for v in pmap.values() if v.get("page_no") and v.get("block_idx") is not None]
    assert located, "至少一条 provenance 带 page_no+block_idx"
    # review_md 里至少一个 anchor id 能在 provenance_map 找到
    import re as _re
    ids = _re.findall(r"\[\[anchor:([^\]]+)\]\]", result["review_md"])
    assert any(i in pmap for i in ids), "review_md 的 anchor 必须映射到 provenance_map"


# ======================================================================
# F-18 — EvidenceRef 块级锚点回填 + F-13 review_chars 剔除 anchor 标记
# ======================================================================

from app.review.orchestrate import _strip_anchor_marks  # noqa: E402


@pytest.mark.asyncio
async def test_run_review_backfills_evidence_anchors_offline():
    """F-18：run_review 把已定位 key_point 的块级锚点回填到同篇 EvidenceRef。

    此前 from_record 产出的 EvidenceRef 其 page_no/block_idx/bbox/section_title/
    anchor_id 恒为 None；回填后命中文献的证据应带可审计的原文坐标。
    """
    content_list = [
        {"type": "text",
         "text": "Intro. Fake source quote for provenance test. End.",
         "text_level": None, "page_idx": 0, "bbox": [10.0, 20.0, 500.0, 60.0]},
    ]
    paper_markdowns = [{
        "meta": {"paper_id": "10", "title": "T"},
        "markdown": "some body",
        "content_list": content_list,
    }]
    records = [{"idx": 1, "paper_id": 10, "attachment_id": 55}]

    result = await run_review("topic", paper_markdowns, records)

    assert result["error"] is None
    refs = result["evidence_refs"]
    assert refs, "fake 综述含 [1] 引用，应产出 EvidenceRef"
    anchored = [r for r in refs if r.anchor_id]
    assert anchored, "至少一条 EvidenceRef 应带 anchor_id（F-18 回填）"
    ref = anchored[0]
    assert ref.block_idx is not None
    assert ref.page_no is not None and ref.page_no >= 1
    assert ref.bbox == [10.0, 20.0, 500.0, 60.0]
    assert ref.anchor_id.startswith("a10_")


@pytest.mark.asyncio
async def test_run_review_no_structure_leaves_anchors_none_offline():
    """无 DocumentStructure（content_list=None）→ EvidenceRef 锚点保持 None（现状不变）。"""
    paper_markdowns = [{"meta": {"paper_id": "10", "title": "T"}, "markdown": "some body"}]
    records = [{"idx": 1, "paper_id": 10, "attachment_id": 55}]

    result = await run_review("topic", paper_markdowns, records)

    assert result["error"] is None
    refs = result["evidence_refs"]
    assert refs
    assert all(r.anchor_id is None and r.block_idx is None for r in refs)


@pytest.mark.asyncio
async def test_run_review_review_chars_strip_anchor_marks_offline():
    """F-13：stats.review_chars 剔除 [[anchor:]] 包裹标记，并同值入 validation_summary。"""
    content_list = [
        {"type": "text",
         "text": "Intro. Fake source quote for provenance test. End.",
         "text_level": None, "page_idx": 0, "bbox": None},
    ]
    paper_markdowns = [{
        "meta": {"paper_id": "10", "title": "T"},
        "markdown": "some body",
        "content_list": content_list,
    }]
    records = [{"idx": 1, "paper_id": 10, "attachment_id": 55}]

    result = await run_review("topic", paper_markdowns, records)

    assert result["error"] is None
    assert "[[anchor:" in result["review_md"], "本 fixture 应注入 anchor 标记"
    stripped = _strip_anchor_marks(result["review_md"])
    assert result["stats"]["review_chars"] == len(stripped)
    assert result["stats"]["review_chars"] < len(result["review_md"])
    assert result["validation_summary"]["review_chars"] == result["stats"]["review_chars"]
