# backend/page_map.py
"""行号 → PDF 页码映射：把 MinerU 的 content_list.json（每块带 page_idx）对齐到 full.md 的行。

算法：content_list 的块与 full.md 内容同序，且 page_idx 单调不减。逐块取一段「锚文本」，
从游标处向后在 full.md 中找到包含该锚的行，记录 (行号→页码) 锚点；游标只前移。最终把
页码变化点压成 breaks 列表，未被锚定的行（如跨行表格、图片）由前一锚点向后填充页码。

只锚定会出现在 full.md 里的块（text / image / table）；page_number / header / footer 这类
噪声块不进 full.md，必须跳过，否则其短文本会造成误匹配。
"""
from __future__ import annotations

import re
from typing import Any

# full.md 中实际出现的块类型；其余（page_number/header/footer）跳过
_ANCHORABLE = {"text", "image", "table"}
_MIN_ANCHOR = 4  # 锚文本最短长度，过短易误匹配 → 跳过，靠向后填充覆盖
_ANCHOR_LEN = 18  # 锚文本截取长度

_WS_RE = re.compile(r"\s+")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s")  # markdown 小标题行（## 1 Introduction 等）


def _ws_strip(s: str) -> str:
    """去全部空白，供块文本与 full.md 行做去空白覆盖匹配（容忍换行/缩进差异）。"""
    return _WS_RE.sub("", s or "")


def _block_match_text(block: dict) -> str:
    """取用于"行覆盖匹配"的块全文：text 用 text；table 用 table_body（与 full.md 中单行
    <table> HTML 同形）；image 无正文（返回空 → 退化为单锚行）。"""
    t = block.get("type")
    if t == "table":
        return block.get("table_body") or ""
    if t == "image":
        return ""
    return block.get("text") or ""


def _trim_trailing_noise(lines: list[str], start: int, end: int) -> int:
    """从 end 向 start 回退，裁掉尾部空行与孤立 markdown 小标题行（不低于 start）。
    仅在块全文未能在区间内对齐时的兜底，避免把下一节标题/尾随空行并进本块。"""
    e = end
    while e > start and (not lines[e].strip() or _HEADING_RE.match(lines[e])):
        e -= 1
    return e


def _block_anchor(block: dict) -> str:
    """取一段能在 full.md 中定位该块的锚文本；取不到返回空串。"""
    t = block.get("type")
    if t == "text":
        return (block.get("text") or "").strip()[:_ANCHOR_LEN]
    if t == "image":
        # full.md 形如 ![](images/<hash>.jpg)，用 hash 段做锚（极具区分度）
        path = block.get("img_path") or ""
        seg = path.split("/")[-1]
        return seg[:_ANCHOR_LEN]
    if t == "table":
        # 表格在 full.md 为单行 <table>…；锚定首个单元格文本
        body = block.get("table_body") or ""
        stripped = re.sub(r"<[^>]+>", "", body).strip()
        return stripped[:_ANCHOR_LEN]
    return ""


def build_line_page_map(full_md: str, content_list: list[dict]) -> dict[str, Any]:
    """构建行号(1-based) → PDF 页码(1-based) 的紧凑映射。

    Returns:
        {"total_lines": int, "total_pages": int, "breaks": [[start_line, page], ...]}
        breaks 按 start_line 升序；某行页码 = 最后一个 start_line<=该行 的 break 的页码。
    """
    lines = full_md.split("\n")
    n = len(lines)

    anchors: list[tuple[int, int]] = []  # (line_idx_0based, page_1based)
    cursor = 0
    last_page = 0
    for block in content_list:
        if block.get("type") not in _ANCHORABLE:
            continue
        try:
            page = int(block.get("page_idx", 0)) + 1
        except (TypeError, ValueError):
            continue
        if page < last_page:  # 单调性保护：拒绝疑似误匹配的回退
            continue
        anchor = _block_anchor(block)
        if len(anchor) < _MIN_ANCHOR:
            continue
        for i in range(cursor, n):
            if anchor in lines[i]:
                anchors.append((i, page))
                cursor = i + 1
                last_page = page
                break

    # 压成页码变化点
    breaks: list[list[int]] = []
    prev_page = None
    for line_idx, page in anchors:
        if page != prev_page:
            breaks.append([line_idx + 1, page])  # 1-based 行号
            prev_page = page
    # 保证第 1 行被覆盖
    if not breaks:
        breaks = [[1, 1]]
    elif breaks[0][0] > 1:
        breaks.insert(0, [1, breaks[0][1]])

    total_pages = max((p for _, p in anchors), default=1)
    return {"total_lines": n, "total_pages": total_pages, "breaks": breaks}


def page_for_line(page_map: dict[str, Any], line: int) -> int:
    """单行(1-based) → 页码(1-based)。breaks 升序，取最后一个 start_line<=line 的页码。"""
    breaks = page_map.get("breaks") or [[1, 1]]
    page = breaks[0][1]
    for start_line, pg in breaks:
        if start_line <= line:
            page = pg
        else:
            break
    return page


def page_label_for_range(page_map: dict[str, Any], start_line: int, end_line: int) -> str:
    """行号区间 → 中文页码标签：同页 "第7页"，跨页 "第7-9页"。"""
    if end_line < start_line:
        start_line, end_line = end_line, start_line
    p1 = page_for_line(page_map, start_line)
    p2 = page_for_line(page_map, end_line)
    return f"第{p1}页" if p1 == p2 else f"第{p1}-{p2}页"


def build_block_line_ranges(
    full_md: str, content_list: list[dict]
) -> dict[str, list[int]]:
    """构建块序号(0-based) → 在「真实 full.md」中的行号区间 [start_line, end_line]（1-based, 闭区间）。

    与 build_line_page_map 同源的锚定法：按块序遍历，游标只前移，对每个可锚定块
    （text / image / table）在 full.md 中找到含其锚文本的行作为该块起始行。块的行区间
    = [本块锚行, 本块自身正文覆盖的末行]——**止于本块文本自身末行**，不把后续空行/
    下一节小标题/不属于本块的段落并进来（否则点引用会把"证据句 + 一个不属于它的小标题"
    一起高亮，损害"溯源精确到段"，codex 终审/Track B 实证 P1）。

    末行算法：从锚行起逐行累加(去空白后)，直到累加文本覆盖本块去空白全文即止于该行；
    上界恒不越过下一个锚定块的锚行（capped）。罕见情形(OCR/换行差异致全文不覆盖)回退到
    上界并裁掉尾部空行与孤立 markdown 小标题行。

    务必对齐到「真实 full_md」而非 "\\n".join(b['text']) 的重建文本——后者会把块映射到
    伪行号，导致 UI 高亮定位到错误行。无法锚定的块（如纯 bbox、锚文本过短、噪声块）
    直接从结果中省略，不臆造行号。

    Returns:
        {"<block_idx>": [start_line, end_line], ...}  键为 str（便于 JSON 列往返）。
    """
    lines = full_md.split("\n")
    n = len(lines)

    # 先逐块锚定起始行（与 build_line_page_map 同样的 cursor-only-forward 策略）
    anchored: list[tuple[int, int]] = []  # (block_idx, start_line_0based)
    cursor = 0
    for block_idx, block in enumerate(content_list):
        if block.get("type") not in _ANCHORABLE:
            continue
        anchor = _block_anchor(block)
        if len(anchor) < _MIN_ANCHOR:
            continue
        for i in range(cursor, n):
            if anchor in lines[i]:
                anchored.append((block_idx, i))
                cursor = i + 1
                break

    # 把锚行扩成区间：末行止于本块自身正文覆盖处（上界=下一锚行,不越界）
    ranges: dict[str, list[int]] = {}
    for pos, (block_idx, start_0based) in enumerate(anchored):
        next_start = anchored[pos + 1][1] if pos + 1 < len(anchored) else n
        limit = min(next_start, n)  # 上界(不含)：恒不越过下一个锚定块的锚行
        block_norm = _ws_strip(_block_match_text(content_list[block_idx]))

        end_0based = start_0based
        if block_norm:
            acc = ""
            covered = False
            for i in range(start_0based, limit):
                acc += _ws_strip(lines[i])
                end_0based = i
                if block_norm in acc:  # 本块全文已被 [start..i] 覆盖 → 止于此行
                    covered = True
                    break
            if not covered:
                # 罕见：本块全文未在 [start, next) 内对齐（OCR/换行差异）→ 回退上界并裁尾部空行/孤立小标题
                end_0based = _trim_trailing_noise(lines, start_0based, limit - 1)
        if end_0based >= next_start:  # 安全边界：绝不并入下一锚块
            end_0based = next_start - 1
        if end_0based < start_0based:
            end_0based = start_0based
        ranges[str(block_idx)] = [start_0based + 1, end_0based + 1]  # 1-based 闭区间

    return ranges
