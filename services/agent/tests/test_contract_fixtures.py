"""B6 离线契约样例校验：守护前端 playwright 联调用的数据形状。

完全离线（不调 LLM）：校验内联合成样例，确保它们对得上 StructureResponse schema
与前端「anchor → provenance_map → 跳原文」依赖的链路。
"""
from __future__ import annotations

import re

from app.schemas import StructureResponse

from helpers_contract import (
    contract_review_with_provenance,
    contract_structure_payload,
)


def test_sample_structure_fixture_matches_contract():
    """synthetic structure payload 必须 schema 忠实地 round-trip 回 StructureResponse。"""
    d = contract_structure_payload()
    resp = StructureResponse.model_validate(d)  # schema 忠实：失败即契约漂移

    assert resp.page_count >= 1

    # blocks：非空，每块带核心定位字段（md_line_start 可为 int 或 None）
    assert resp.blocks, "structure payload 的 blocks 不应为空"
    for b in resp.blocks:
        assert b.block_idx is not None
        assert b.type
        assert b.page_no is not None
        assert b.section_title is not None  # 可为 ""，但字段必须存在

    # tables：非空，首表网格须矩形（每行列数 == n_cols），守护 colspan/rowspan 展开
    assert resp.tables, "structure payload 的 tables 不应为空"
    t = resp.tables[0]
    assert all(len(r) == t.n_cols for r in t.grid), "首表网格非矩形"


def test_sample_review_fixture_has_provenance():
    """synthetic review payload 必须含真实 anchor + provenance 链路。"""
    d = contract_review_with_provenance()
    assert {"review_md", "provenance_map"} <= d.keys()

    review_md = d["review_md"]
    provenance_map = d["provenance_map"]

    assert "[[anchor:" in review_md, "review_md 应含 occurrence anchor"
    assert len(provenance_map) >= 1, "provenance_map 不应为空"

    # 每条 provenance entry 须含前端定位所需的【完整】契约字段（codex B6 P2：含 attachment_id/
    # bbox/table_idx/cell_row/cell_col，前端 SourceViewer 像素级/单元格定位依赖）。
    _REQUIRED_KEYS = {
        "paper_id", "attachment_id", "page_no", "block_idx", "bbox",
        "table_idx", "cell_row", "cell_col", "section_title", "quote",
    }
    for entry in provenance_map.values():
        assert _REQUIRED_KEYS <= entry.keys(), f"provenance entry 缺字段: {_REQUIRED_KEYS - entry.keys()}"

    # 前端核心链路：review_md 里解析出的 anchor id 必须【全部】是 provenance_map 的 key
    # （codex B6 P2：不能只保证一个映射；坏 fixture 留 1 个有效 anchor 也应被测出）。
    anchor_ids = re.findall(r"\[\[anchor:([^\]]+)\]\]", review_md)
    assert anchor_ids, "应能从 review_md 解析出至少一个 anchor id"
    unmapped = [a for a in anchor_ids if a not in provenance_map]
    assert not unmapped, f"review_md 中存在未映射到 provenance_map 的 anchor: {unmapped[:5]}"

    # 至少一条 entry 真正定位到了 page_no + block_idx（非全 None）
    assert any(
        e.get("page_no") is not None and e.get("block_idx") is not None
        for e in provenance_map.values()
    ), "至少一条 provenance entry 应有非空 page_no + block_idx"
