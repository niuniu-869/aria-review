"""B2 单元测试：content_list → StructureTable[] + 网格展开（colspan/rowspan）。

复用 B1 内联合成契约样例：样例含 colspan="2" 的 <table>，展开后应为 4 列矩形网格。
另直接单测 _parse_rows 的 colspan 展开不变式（首列放文本、其后补空、行宽对齐）。
"""
from __future__ import annotations

from app.structure.page_map import build_line_page_map
from helpers_contract import contract_content_list, contract_full_markdown
from app.structure.tables import _parse_rows, content_list_to_tables


def _load_fixtures() -> tuple[str, list[dict]]:
    return contract_full_markdown(), contract_content_list()


def test_tables_grid_rectangular_and_located():
    full_md, cl = _load_fixtures()
    pm = build_line_page_map(full_md, cl)

    tables = content_list_to_tables(cl, pm)
    assert len(tables) >= 1

    t = tables[0]
    # 网格矩形：每行宽度 == n_cols
    assert all(len(row) == t.n_cols for row in t.grid)
    # colspan="2" 展开后至少 4 列
    assert t.n_cols >= 4
    assert t.table_idx == 0
    # fixture 富化后(补入编号小标题块)表块序号为 10；用 content_list 实际定位避免硬编码漂移
    expected_block_idx = next(i for i, b in enumerate(cl) if b.get("type") == "table")
    assert t.block_idx == expected_block_idx
    assert t.page_no == 2


def test_parse_rows_expands_colspan():
    html = (
        "<table>"
        "<tr><td colspan=\"2\">Span Header</td><td>C3</td></tr>"
        "<tr><td>a</td><td>b</td><td>c</td></tr>"
        "</table>"
    )
    grid = _parse_rows(html)
    # 行宽一致
    widths = {len(r) for r in grid}
    assert len(widths) == 1
    # colspan 文本落首列，其后补空
    assert grid[0][0] == "Span Header"
    assert grid[0][1] == ""
    assert grid[0][2] == "C3"
