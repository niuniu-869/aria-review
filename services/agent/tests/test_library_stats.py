"""Task 1: 文献库统计聚合测试。

核心正确性：一篇多附件不得过计数。
"""
import pytest
from app.repositories import library as lib
from app.repositories import project as proj


@pytest.mark.asyncio
async def test_stats_no_overcount_with_multiple_attachments(session):
    """一篇 paper 挂 2 个 attachment（1 done 1 pending），不得把 totalPapers/withPdf 算成 2。"""
    p = await lib.add_paper(session, {"title": "A", "abstract": "abs"})
    await lib.add_attachment(session, p.id, sha256="x1", mineru_status="done")
    await lib.add_attachment(session, p.id, sha256="x2", mineru_status="pending")
    await session.commit()
    s = await lib.compute_library_stats(session)
    assert s["totalPapers"] == 1
    assert s["withPdf"] == 1            # 有附件即算 1，不按附件数
    assert s["ocr"]["done"] == 1        # 多附件取最完成态 done>processing>pending>failed>none
    assert s["withMetadata"] == 1       # abstract 非空


@pytest.mark.asyncio
async def test_project_stats_inclusion_breakdown(session):
    """项目作用域：inclusion breakdown 统计正确，withMetadata 口径正确。"""
    pr = await proj.create_project(session, {"name": "P1"})
    p1 = await lib.add_paper(session, {"title": "T1"})           # 无元数据(仅 title)
    p2 = await lib.add_paper(session, {"title": "T2", "abstract": "a"})
    await proj.add_paper_to_project(session, pr.id, p1.id)
    pp2 = await proj.add_paper_to_project(session, pr.id, p2.id)
    await proj.set_inclusion(session, pp2.id, "included")
    await session.commit()
    s = await lib.compute_library_stats(session, project_id=pr.id)
    assert s["projectPapers"] == 2
    assert s["inclusion"]["included"] == 1
    assert s["inclusion"]["candidate"] == 1
    assert s["withMetadata"] == 1       # 仅 p2 有 abstract


@pytest.mark.asyncio
async def test_empty_project_stats_returns_zeros(session):
    """M5-①: 空库(0 paper) compute_library_stats 返回全零不报错。"""
    pr = await proj.create_project(session, {"name": "EmptyProject"})
    await session.commit()
    s = await lib.compute_library_stats(session, project_id=pr.id)
    assert s["projectPapers"] == 0
    assert s["withMetadata"] == 0
    assert s["withPdf"] == 0
    assert s["ocr"]["done"] == 0
    assert s["ocr"]["none"] == 0
    assert s["inclusion"]["included"] == 0
    assert s["inclusion"]["candidate"] == 0


@pytest.mark.asyncio
async def test_attachment_with_none_mineru_status(session):
    """M5-②: 有附件但 mineru_status=None → 该篇 ocr 归 none 且 withPdf 计入。"""
    p = await lib.add_paper(session, {"title": "PdfNoOcr"})
    await lib.add_attachment(session, p.id, sha256="abc123", mineru_status=None)
    await session.commit()
    s = await lib.compute_library_stats(session)
    assert s["withPdf"] == 1       # 有附件即算有 pdf
    assert s["ocr"]["none"] == 1   # mineru_status=None → rank=0 → none


@pytest.mark.asyncio
async def test_csl_json_only_counts_as_metadata(session):
    """M5-③: 仅 csl_json 非空且 abstract=None → withMetadata=1。"""
    import json as _json
    p = await lib.add_paper(
        session,
        {"title": "CslOnly", "csl_json": _json.dumps({"title": "CslOnly"}), "abstract": None},
    )
    await session.commit()
    s = await lib.compute_library_stats(session)
    assert s["withMetadata"] == 1  # csl_json 非空即算有元数据
