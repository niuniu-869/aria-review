"""B1 持久化测试：_store_parsed_pdf 透传 content_list → 落库 DocumentStructure。

用内联合成契约样例端到端验证：
- DocumentStructure 行写入且字段正确（page_count / has_bbox / sha256 / 块区间）
- 重复调用不崩、不在同一 attachment 上堆叠多条结构行（幂等/upsert）
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import func, select

from app.ingest.fulltext import _store_parsed_pdf
from app.models import DocumentStructure
from helpers_contract import contract_content_list, contract_full_markdown

_SHA = "deadbeef" * 8  # 64-hex 占位 PDF sha256


def _load_fixtures() -> tuple[str, list[dict]]:
    return contract_full_markdown(), contract_content_list()


async def test_store_parsed_pdf_persists_document_structure(
    session, tmp_path, monkeypatch
):
    """落库一条 DocumentStructure，字段与 content_list 一致。"""
    monkeypatch.setattr("app.config.settings.corpora_dir", str(tmp_path / "corpora"))
    markdown, content_list = _load_fixtures()

    r = await _store_parsed_pdf(
        Path("张三_2020_A Study.pdf"),
        _SHA,
        markdown,
        content_list=content_list,
        session=session,
    )
    attachment_id = r["attachment_id"]
    assert attachment_id is not None

    row = (
        await session.execute(
            select(DocumentStructure).where(
                DocumentStructure.attachment_id == attachment_id
            )
        )
    ).scalar_one_or_none()

    assert row is not None
    assert row.page_count >= 1
    assert row.has_bbox == any(b.get("bbox") for b in content_list)
    assert isinstance(row.block_line_ranges, dict) and row.block_line_ranges
    assert row.markdown_sha256 == hashlib.sha256(
        markdown.encode("utf-8")
    ).hexdigest()
    assert row.source_pdf_sha256 == _SHA
    # has_bbox=True 时坐标空间应标记为 mineru_1000
    assert row.bbox_coord_space == "mineru_1000"
    # content_list 原样透传
    assert isinstance(row.content_list, list) and len(row.content_list) == len(content_list)


async def test_store_parsed_pdf_idempotent_no_duplicate_structure(
    session, tmp_path, monkeypatch
):
    """同 path/sha 调用两次不崩，且任一 attachment 至多一条结构行（无堆叠）。"""
    monkeypatch.setattr("app.config.settings.corpora_dir", str(tmp_path / "corpora"))
    markdown, content_list = _load_fixtures()

    path = Path("张三_2020_A Study.pdf")
    r1 = await _store_parsed_pdf(
        path, _SHA, markdown, content_list=content_list, session=session
    )
    r2 = await _store_parsed_pdf(
        path, _SHA, markdown, content_list=content_list, session=session
    )

    # 两次都成功
    assert r1["status"] == "done"
    assert r2["status"] == "done"

    # 每个 attachment 至多一条结构行（attachment_id unique → upsert，不堆叠）
    rows = (
        await session.execute(
            select(
                DocumentStructure.attachment_id, func.count(DocumentStructure.id)
            ).group_by(DocumentStructure.attachment_id)
        )
    ).all()
    assert rows, "应至少有一条 DocumentStructure"
    for _att_id, cnt in rows:
        assert cnt == 1, f"attachment {_att_id} 出现 {cnt} 条结构行（应唯一）"
