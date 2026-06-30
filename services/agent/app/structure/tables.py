"""表格视图：把 MinerU content_list 里的 <table> 块解析成 colspan/rowspan 已展开的
列对齐网格（StructureTable）。

HTML <table> 解析逻辑搬自 FS_Agent backend/report_reader.py（通用，与财报无关），**去财报**：
只保留 _strip_tags / _span / _parse_cells / _parse_rows / table_to_rows / parse_outline /
extract_tables（且 extract_tables 去掉 guess 字段），不搬任何 SCE / 报表类型猜测 / 单位探测 /
附注表 / financial 逻辑。单元格文本逐字保留（含 OCR 错），仅去标签与首尾空白。
"""
from __future__ import annotations

import re
from typing import Any

from ..schemas import StructureTable

# 匹配 <table>...</table>（点号默认不匹配换行，用 re.DOTALL）
_RE_TABLE = re.compile(r"<table[\s>].*?</table>", re.DOTALL | re.IGNORECASE)
# 匹配 <tr>...</tr>
_RE_TR = re.compile(r"<tr[\s>].*?</tr>", re.DOTALL | re.IGNORECASE)
# 匹配 <td ...>内容</td>（th 同样处理），分别捕获 (属性, 内容)；闭合标签容忍 </td >
_RE_CELL = re.compile(r"<t[dh]([^>]*)>(.*?)</t[dh]\s*>", re.DOTALL | re.IGNORECASE)
_RE_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
# 从属性串里取 colspan / rowspan（兼容带引号与不带引号）
_RE_COLSPAN = re.compile(r"colspan\s*=\s*[\"']?(\d+)", re.IGNORECASE)
_RE_ROWSPAN = re.compile(r"rowspan\s*=\s*[\"']?(\d+)", re.IGNORECASE)


def _strip_tags(html: str) -> str:
    """去除字符串内所有 HTML 标签，返回纯文本（首尾空白去除）。
    <br> 先转空格（避免 `2024年<br>12月31日` 被粘连）；其余标签删除。
    注意：**单元格数字文本逐字保留**，不做任何数字格式规整。"""
    return re.sub(r"<[^>]+>", "", _RE_BR.sub(" ", html)).strip()


def _span(attrs: str, pat: re.Pattern) -> int:
    """从单元格属性串里取 colspan/rowspan，缺省/非法为 1。"""
    m = pat.search(attrs or "")
    if not m:
        return 1
    try:
        return max(1, int(m.group(1)))
    except ValueError:
        return 1


def _parse_cells(tr_html: str) -> list[tuple[str, int, int]]:
    """解析一个 <tr>，返回 [(单元格文本, colspan, rowspan)]（文本逐字保留）。"""
    out: list[tuple[str, int, int]] = []
    for c in _RE_CELL.finditer(tr_html):
        attrs, content = c.group(1), c.group(2)
        out.append((_strip_tags(content), _span(attrs, _RE_COLSPAN), _span(attrs, _RE_ROWSPAN)))
    return out


def _parse_rows(table_html: str) -> list[list[str]]:
    """从 table HTML 提取并**网格展开**所有行，返回对齐的 list[list[str]]。

    处理合并单元格（关键：否则数值会错列）：
    - colspan=c：文本放该跨度的**第一列**，其余 c-1 列补空串（段标题如「流动资产：」横跨多列时，
      不会把后面数据列挤位）。
    - rowspan=r：文本**向下携带**到后续 r-1 行的同一列（跨行标签按 HTML 语义在每行复现）。
    - 所有行补齐到最大列宽，保证列对齐（第 N 列在各行恒为同一字段）。
    单元格文本**逐字保留**（含 OCR 错，仅去标签与首尾空白）。
    """
    parsed = [_parse_cells(m.group(0)) for m in _RE_TR.finditer(table_html)]
    grid: list[list[str]] = []
    pending: dict[int, tuple[int, str]] = {}  # 列 -> (剩余行数, 文本)，跨行携带

    for cells in parsed:
        row: list[str] = []
        col = 0
        i = 0
        while True:
            # 1) 当前列若被上方 rowspan 占用，先填它
            if col in pending:
                remaining, text = pending[col]
                row.append(text)
                if remaining - 1 > 0:
                    pending[col] = (remaining - 1, text)
                else:
                    del pending[col]
                col += 1
                continue
            # 2) 还有本行新单元格 → 按 colspan 展开
            if i < len(cells):
                text, cs, rs = cells[i]
                i += 1
                for k in range(cs):
                    cell_text = text if k == 0 else ""   # colspan：首列放文本，余列补空
                    row.append(cell_text)
                    if rs > 1:                            # rowspan：登记向下携带
                        pending[col] = (rs - 1, cell_text)
                    col += 1
                continue
            # 3) 本行新单元格用尽，但更右列仍有待填的 rowspan → 补空推进到那一列
            if any(c > col for c in pending):
                row.append("")
                col += 1
                continue
            break
        grid.append(row)

    width = max((len(r) for r in grid), default=0)
    for r in grid:
        if len(r) < width:
            r.extend([""] * (width - len(r)))
    return grid


def table_to_rows(text: str, index: int) -> dict[str, Any]:
    """解析文本中第 index 个 <table>（0-indexed），返回结构化结果。

    返回：
        {index, n_rows, rows, compact}
    或越界时：
        {error: str}

    rows：list[list[str]]，已按 colspan/rowspan **网格展开并对齐列**（见 _parse_rows）。
    compact：多行字符串，每行格式为 ``cell | cell | cell``。
    单元格文本**逐字保留**——这是审计抓 OCR 错的前提，绝不在解析时规整数字。
    """
    if index < 0:
        return {"error": f"index {index} 越界（负数不合法）"}

    tables = list(_RE_TABLE.finditer(text))
    if index >= len(tables):
        return {"error": f"index {index} 越界，文本中共有 {len(tables)} 个表"}

    table_html = tables[index].group(0)
    rows = _parse_rows(table_html)
    n_rows = len(rows)

    # 渲染 compact：每行用 " | " 连接
    compact_lines = [" | ".join(row) for row in rows]
    compact = "\n".join(compact_lines)

    return {
        "index": index,
        "n_rows": n_rows,
        "rows": rows,
        "compact": compact,
    }


def parse_outline(text: str) -> list[dict[str, Any]]:
    """扫描所有 Markdown 标题，返回 [{start_line, end_line, title, level}]。
    级别 = 行首 # 的个数；每个标题范围到下一个标题前一行，最后一个到文件末尾。"""
    lines = text.splitlines()
    headings: list[dict[str, Any]] = []
    for idx, line in enumerate(lines, start=1):
        s = line.strip()
        if s.startswith("#"):
            level = len(s) - len(s.lstrip("#"))
            headings.append({"start_line": idx, "title": s, "level": level, "end_line": idx})
    for i in range(len(headings)):
        start = headings[i]["start_line"]
        end = (headings[i + 1]["start_line"] - 1) if i + 1 < len(headings) else len(lines)
        headings[i]["end_line"] = end
    return headings


def extract_tables(text: str) -> list[dict[str, Any]]:
    """扫描文本中所有 <table>...</table> 块（兼容单行与跨行），
    返回 [{index, start_line, end_line, n_rows, n_cols, preview}]。

    - index：从 0 起。
    - start_line / end_line：1-indexed 行号（与 parse_outline 保持一致）。
    - n_rows：<tr> 数量；n_cols：网格展开后的最大列宽。
    - preview：前两行紧凑文本（cell | cell 格式，截 200 字符）。
    """
    result: list[dict[str, Any]] = []

    for idx, m in enumerate(_RE_TABLE.finditer(text)):
        table_html = m.group(0)
        start_char = m.start()
        end_char = m.end() - 1  # 最后一个字符的位置

        # 计算 start_line / end_line（1-indexed）
        start_line = text[:start_char].count("\n") + 1
        end_line = text[:end_char + 1].count("\n") + 1

        # 解析行和列
        rows = _parse_rows(table_html)
        n_rows = len(rows)
        n_cols = max((len(r) for r in rows), default=0)

        # preview：前两行 compact 文本，截 200
        preview_rows = rows[:2]
        preview = "\n".join(" | ".join(r) for r in preview_rows)
        if len(preview) > 200:
            preview = preview[:200]

        result.append({
            "index": idx,
            "start_line": start_line,
            "end_line": end_line,
            "n_rows": n_rows,
            "n_cols": n_cols,
            "preview": preview,
        })

    return result


def content_list_to_tables(content_list: list[dict], page_map: dict) -> list[StructureTable]:
    """把 MinerU content_list 里的 type=="table" 块转成 StructureTable[]（按出现序）。

    grid 由 _parse_rows 展开（colspan/rowspan 已处理），n_cols 为补齐后最大列宽。
    block_idx 为该表在 content_list 的序号（用于定位/高亮），table_idx 为 0-based 表计数。
    """
    out: list[StructureTable] = []
    table_idx = 0
    for block_idx, block in enumerate(content_list or []):
        if block.get("type") != "table":
            continue
        grid = _parse_rows(block.get("table_body") or "")
        n_rows = len(grid)
        n_cols = max((len(r) for r in grid), default=0)
        try:
            page_no = int(block.get("page_idx", 0)) + 1
        except (TypeError, ValueError):
            page_no = 1
        if page_no < 1:
            page_no = 1
        out.append(StructureTable(
            table_idx=table_idx,
            block_idx=block_idx,
            page_no=page_no,
            bbox=block.get("bbox"),
            n_rows=n_rows,
            n_cols=n_cols,
            grid=grid,
            caption=block.get("caption") or "",
        ))
        table_idx += 1
    return out
