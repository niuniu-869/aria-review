"""B2 单元测试：content_list → StructureBlock[] + EvidenceResolver 逐字定位（纯函数，无 DB）。

复用 B1 内联合成契约样例：
- 块视图须带页码/章节/行区间，噪声块（page_number）不入；
- EvidenceResolver 把逐字 quote 定位回 block（命中→found True；查不到→found False）。
"""
from __future__ import annotations

from app.structure.blocks import EvidenceResolver, content_list_to_blocks
from app.structure.page_map import build_block_line_ranges, build_line_page_map
from helpers_contract import contract_content_list, contract_full_markdown


def _load_fixtures() -> tuple[str, list[dict]]:
    return contract_full_markdown(), contract_content_list()


def test_blocks_carry_page_section_and_lines():
    full_md, cl = _load_fixtures()
    pm = build_line_page_map(full_md, cl)
    ranges = build_block_line_ranges(full_md, cl)

    blocks = content_list_to_blocks(cl, pm, ranges)

    # page_number 噪声块不入（fixture 末块 type=page_number）
    assert all(b.type in {"text", "title", "table", "image"} for b in blocks)
    assert len(blocks) == len([b for b in cl if b.get("type") in {"text", "table", "image"}])

    assert all(b.page_no >= 1 for b in blocks)
    # 行区间或为精确区间(start<=end, start>=1)或为 None(不可定位); 本 fixture 全部可锚定
    assert all(
        b.md_line_start is None or (b.md_line_start >= 1 and b.md_line_start <= b.md_line_end)
        for b in blocks
    )
    assert all(b.md_line_start is not None for b in blocks), "本 fixture 各块均可锚定,应有精确行区间"

    # 至少一个正文块（text_level None）有非空章节
    body_with_section = [
        b for b in blocks if b.type == "text" and b.text_level is None and b.section_title
    ]
    assert body_with_section, "应有正文块带非空 section_title"

    # 至少一个标题块
    assert any(b.type == "title" for b in blocks)

    # section_title 精确归节(fixture 含编号小标题后)：结果区正文应归 "3 Results" 而非 "Abstract"
    results_body = next(
        b for b in blocks
        if b.type == "text" and "Across all three datasets" in b.text_preview
    )
    assert results_body.section_title == "3 Results", (
        f"结果区正文 section_title 应为 '3 Results',实为 {results_body.section_title!r}"
    )


def test_evidence_resolver_locates_quote():
    _, cl = _load_fixtures()
    resolver = EvidenceResolver(cl)

    sample_quote = next(
        b["text"][:20]
        for b in cl
        if b.get("type") == "text" and b.get("text") and b.get("text_level") is None
    )
    ev = resolver.resolve(sample_quote)

    assert ev["found"] is True
    assert ev["page_no"] >= 1
    assert ev["block_idx"] is not None
    assert ev["table_idx"] is None


def test_evidence_resolver_miss_returns_not_found():
    _, cl = _load_fixtures()
    resolver = EvidenceResolver(cl)
    ev = resolver.resolve("zzz nonexistent quote 9j3k")
    assert ev["found"] is False
    assert ev["block_idx"] is None
    assert ev["page_no"] is None


def test_unanchored_block_yields_null_line_range_not_fabricated():
    """零伪造：锚文本不在 full.md 的块 → md_line_start/end 为 None,绝不伪造页首行号(codex 二审 P1)。"""
    full_md = "Intro line one.\nSecond paragraph here is present.\n"
    cl = [
        {"type": "text", "text": "Intro line one.", "text_level": None, "page_idx": 0, "bbox": None},
        {"type": "text", "text": "GHOST sentence absent from the markdown xyz9k", "text_level": None,
         "page_idx": 0, "bbox": None},
    ]
    pm = build_line_page_map(full_md, cl)
    ranges = build_block_line_ranges(full_md, cl)
    blocks = content_list_to_blocks(cl, pm, ranges)
    ghost = next(b for b in blocks if b.text_preview.startswith("GHOST"))
    assert ghost.md_line_start is None and ghost.md_line_end is None
    # 可锚定块仍拿到精确区间
    intro = next(b for b in blocks if b.text_preview.startswith("Intro"))
    assert intro.md_line_start is not None


def test_resolver_skips_noise_blocks_alignment():
    """resolve 不应返回噪声块(page_number/header/footer)的 block_idx——它不在 StructureResponse.blocks 里。"""
    cl = [
        {"type": "text", "text": "Real body paragraph about graph neural networks.",
         "text_level": None, "page_idx": 0, "bbox": None},
        {"type": "footer", "text": "Confidential running footer Acme Journal volume twelve 2026",
         "page_idx": 0, "bbox": None},
    ]
    resolver = EvidenceResolver(cl)
    # 命中正文块正常
    ev = resolver.resolve("Real body paragraph about graph")
    assert ev["found"] is True and ev["block_idx"] == 0
    # 噪声块文本(足够长、独有)未被索引 → 查不到(found False),绝不返回噪声块 idx=1
    ev2 = resolver.resolve("Confidential running footer Acme Journal volume twelve 2026")
    assert ev2["found"] is False and ev2["block_idx"] is None


# ---------------------------------------------------------------------------
# markdown_to_content_list：无结构全文(如 Sciverse)合成 content_list → 可走 B4 溯源
# ---------------------------------------------------------------------------

from app.structure.blocks import markdown_to_content_list


def test_markdown_to_content_list_splits_paragraphs_and_headings():
    md = (
        "# The Information Content of IPO Prospectuses\n\n"
        "Using word content analysis, we decompose information into standard "
        "and informative components.\n\n"
        "## Method\n\n"
        "The opposite is true for standard content."
    )
    cl = markdown_to_content_list(md)
    assert len(cl) == 4
    assert cl[0]["type"] == "text" and cl[0]["text_level"] == 1
    assert cl[0]["text"] == "The Information Content of IPO Prospectuses"  # 去掉 #
    assert "text_level" not in cl[1]  # 正文段无 text_level
    assert cl[2]["text_level"] == 2  # ## → level 2


def test_markdown_to_content_list_drives_evidence_resolver():
    """合成块能被 EvidenceResolver 按逐字 quote 定位回 block(可点击溯源的根)。"""
    md = (
        "# Title\n\n"
        "Greater informative content results in more accurate offer prices "
        "and less underpricing."
    )
    cl = markdown_to_content_list(md)
    res = EvidenceResolver(cl).resolve("less underpricing")
    assert res["found"] is True
    assert res["block_idx"] == 1  # 命中正文段(非标题)
    assert res["page_no"] == 1
    assert res["section_title"] == "Title"  # 章节追踪生效


def test_markdown_to_content_list_empty():
    assert markdown_to_content_list("") == []
    assert markdown_to_content_list("\n\n  \n\n") == []


def test_markdown_to_content_list_chunks_giant_paragraph():
    """退化形态: 超长无空行段落 → 切碎成多块, 每块 <= 上限(codex P2 兜底)。"""
    from app.structure.blocks import _MAX_BLOCK_CHARS
    giant = "x" * (_MAX_BLOCK_CHARS * 3 + 500)  # 无空行/无换行
    cl = markdown_to_content_list(giant)
    assert len(cl) >= 3
    assert all(len(b["text"]) <= _MAX_BLOCK_CHARS for b in cl)


def test_markdown_to_content_list_normal_paragraph_not_chunked():
    """正常段(<=上限)不被切, 保持整段成块(行为不变)。"""
    para = "Greater informative content results in less underpricing. " * 30  # ~1.7k < 4k
    cl = markdown_to_content_list(para)
    assert len(cl) == 1
