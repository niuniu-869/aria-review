"""B1 单元测试：page_map 行号↔页码映射 + 块行区间（纯函数，无 DB）。

用内联合成契约样例验证锚定能落地：
页码映射单调非减、块行区间落在 full.md 真实行范围内且按块序有序不重叠。
"""
from __future__ import annotations

import re

from helpers_contract import contract_content_list, contract_full_markdown
from app.structure.page_map import (
    build_block_line_ranges,
    build_line_page_map,
    page_for_line,
)


def _load_fixtures() -> tuple[str, list[dict]]:
    return contract_full_markdown(), contract_content_list()


def test_pagemap_monotonic_and_lookup():
    """页码映射：total_pages >= 1，且逐行 page_for_line 单调非减。"""
    full_md, content_list = _load_fixtures()
    pm = build_line_page_map(full_md, content_list)

    assert pm["total_pages"] >= 1
    assert pm["total_lines"] >= 1

    pages = [page_for_line(pm, line) for line in range(1, pm["total_lines"] + 1)]
    assert pages == sorted(pages), f"页码映射非单调: {pages}"
    # fixture 跨 2 页：确认 break 真实存在（不是退化的全 1 页）
    assert pm["total_pages"] >= 2


def test_block_line_ranges_valid():
    """块行区间：非空，每个区间落在 [1, total_lines]，且按块序有序不重叠。"""
    full_md, content_list = _load_fixtures()
    pm = build_line_page_map(full_md, content_list)
    ranges = build_block_line_ranges(full_md, content_list)

    assert ranges, "block_line_ranges 不应为空"

    # 每个区间合法：1 <= start <= end <= total_lines
    total = pm["total_lines"]
    for key, (start, end) in ranges.items():
        assert 1 <= start <= end <= total, f"块 {key} 区间非法: {(start, end)}"

    # 块序号递增 → 区间有序且不重叠（前块 end < 后块 start）
    ordered_keys = sorted(ranges, key=lambda k: int(k))
    prev_end = 0
    for key in ordered_keys:
        start, end = ranges[key]
        assert start > prev_end, f"块 {key} 起始 {start} 与前块 end {prev_end} 重叠/逆序"
        prev_end = end


def test_block_ranges_do_not_absorb_next_section_heading():
    """回归(codex终审/Track B 实证 P1)：块行区间末行必须止于本块正文，绝不把【后续】
    孤立小标题(## N ...)并进来——否则点引用会高亮"证据句 + 不属于它的小标题"。
    允许块自身起始行是标题(title 块),但起始行之后不得再出现 markdown 小标题行。"""
    full_md, content_list = _load_fixtures()
    lines = full_md.split("\n")
    heading = re.compile(r"^\s*#{1,6}\s")
    ranges = build_block_line_ranges(full_md, content_list)

    for key, (start, end) in ranges.items():
        for ln in range(start + 1, end + 1):  # 起始行之后
            assert not heading.match(lines[ln - 1]), (
                f"块 {key} 区间 [{start},{end}] 吞入了后续小标题行 {ln}: {lines[ln - 1]!r}"
            )

    # 具体守护实证的两处：摘要正文块不含 '## 1 Introduction'，结果正文块不含 '## 4 Conclusion'
    def _range_for_first_anchor(substr: str) -> tuple[int, int]:
        for i, b in enumerate(content_list):
            if substr in (b.get("text") or ""):
                return tuple(ranges[str(i)])
        raise AssertionError(f"未找到含 {substr!r} 的块")

    s, e = _range_for_first_anchor("This study investigates")
    assert all("Introduction" not in lines[ln - 1] for ln in range(s, e + 1))
    s, e = _range_for_first_anchor("Across all three datasets")
    assert all("Conclusion" not in lines[ln - 1] for ln in range(s, e + 1))


def test_block_ranges_multiline_table_image_and_fallback():
    """补 build_block_line_ranges 各分支正例(codex P3 覆盖缺口)：多行正文覆盖、表格单行、
    图片单锚、块文本未对齐时 fallback 裁尾部空行/标题。"""
    # ① 多行正文：块文本跨 full.md 两行 → 区间须覆盖两行,止于正文末,不含后续 ## Next。
    # （首行须 ≥18 字以含锚前缀——锚定按整行匹配,这是搬来的锚定法固有约束,真实段落首行均够长。）
    full_ml = "This paragraph starts on one line\nand wraps onto a second line here.\n## Next\n"
    cl_ml = [{"type": "text",
              "text": "This paragraph starts on one line and wraps onto a second line here.",
              "page_idx": 0}]
    assert build_block_line_ranges(full_ml, cl_ml)["0"] == [1, 2]

    # ② 表格块：full.md 单行 <table> → 区间单行,不吞后续 ## After
    tbody = '<table><tr><td>Cell A</td></tr></table>'
    full_t = f"intro line\n{tbody}\n## After\n"
    cl_t = [{"type": "text", "text": "intro line", "page_idx": 0},
            {"type": "table", "table_body": tbody, "page_idx": 0}]
    rt = build_block_line_ranges(full_t, cl_t)
    assert rt["1"] == [2, 2]

    # ③ 图片块：无正文 → 退化为单锚行(不吞后续 caption/标题)
    full_img = "![](images/abcd1234efgh.jpg)\n## Caption Heading\n"
    cl_img = [{"type": "image", "img_path": "images/abcd1234efgh.jpg", "page_idx": 0}]
    assert build_block_line_ranges(full_img, cl_img)["0"] == [1, 1]

    # ④ 未对齐 fallback：块全文有未匹配尾部 → 锚行命中但全文不覆盖 → 裁尾部空行+标题,止于锚行
    full_fb = "anchorzzz body text\n\n## Heading\n"
    cl_fb = [{"type": "text", "text": "anchorzzz body text BUT WITH EXTRA UNMATCHED TAIL", "page_idx": 0}]
    assert build_block_line_ranges(full_fb, cl_fb)["0"] == [1, 1]
