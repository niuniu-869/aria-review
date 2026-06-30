"""P1-7: project_svc service 层单元测试。

用 session fixture 直接调用 service 函数，不经 HTTP 层。
"""
from __future__ import annotations

import pytest

from app.errors import ApiError
from app.repositories.library import add_paper
from app.repositories.project import add_paper_to_project, create_project
from app.services import project_svc


# ---------------------------------------------------------------------------
# list_projects_dto / create_project_dto
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_projects_empty(session):
    """无项目时返回空列表。"""
    result = await project_svc.list_projects_dto(session)
    assert result == []


@pytest.mark.asyncio
async def test_create_and_list_projects(session):
    """创建两个项目后 list 返回正确 DTO 字段。"""
    dto1 = await project_svc.create_project_dto(session, name="项目A", research_question="Q1")
    dto2 = await project_svc.create_project_dto(session, name="项目B", description="描述")

    assert dto1["id"] is not None
    assert dto1["name"] == "项目A"
    assert "createdAt" in dto1

    items = await project_svc.list_projects_dto(session)
    names = [x["name"] for x in items]
    assert "项目A" in names
    assert "项目B" in names


# ---------------------------------------------------------------------------
# get_project_dto
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_project_dto_not_found(session):
    """不存在的 project_id 返回 None。"""
    result = await project_svc.get_project_dto(session, project_id=99999)
    assert result is None


@pytest.mark.asyncio
async def test_get_project_dto_with_counts(session):
    """get_project_dto 正确返回 paperCount/includedCount。"""
    proj = await create_project(session, {"name": "CountTest"})
    p1 = await add_paper(session, {"title": "Pa", "doi": "10.1/pa"})
    p2 = await add_paper(session, {"title": "Pb", "doi": "10.1/pb"})
    pp1 = await add_paper_to_project(session, proj.id, p1.id)
    await add_paper_to_project(session, proj.id, p2.id)

    # 将 p1 设为 included
    from app.repositories.project import set_inclusion
    await set_inclusion(session, pp1.id, "included")

    dto = await project_svc.get_project_dto(session, proj.id)
    assert dto is not None
    assert dto["id"] == proj.id
    assert dto["paperCount"] == 2
    assert dto["includedCount"] == 1


# ---------------------------------------------------------------------------
# list_project_papers_dto
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_project_papers_dto(session):
    """list_project_papers_dto 返回 paperId/title/year/inclusionStatus/screeningScore/hasExtraction。"""
    proj = await create_project(session, {"name": "PaperList"})
    paper = await add_paper(session, {"title": "Title X", "doi": "10.1/x", "year": 2022})
    await add_paper_to_project(session, proj.id, paper.id)

    items = await project_svc.list_project_papers_dto(session, proj.id)
    assert len(items) == 1
    item = items[0]
    assert item["paperId"] == paper.id
    assert item["title"] == "Title X"
    assert item["year"] == 2022
    assert item["inclusionStatus"] == "candidate"
    assert item["screeningScore"] is None
    # W5-b: hasExtraction 默认为 False（无 paper_extraction 行）
    assert item["hasExtraction"] is False


@pytest.mark.asyncio
async def test_list_project_papers_dto_has_extraction(session):
    """list_project_papers_dto 在 paper 有 paper_extraction 行时 hasExtraction=True。"""
    from app.repositories.extraction import upsert_extraction

    proj = await create_project(session, {"name": "ExtList"})
    paper = await add_paper(session, {"title": "ExtPaper", "doi": "10.1/ext"})
    await add_paper_to_project(session, proj.id, paper.id)

    # 注入一条 extraction 记录
    await upsert_extraction(
        session,
        paper_id=paper.id,
        fields={"research_question": "RQ", "method": "M"},
        model="test",
    )
    await session.commit()

    items = await project_svc.list_project_papers_dto(session, proj.id)
    assert len(items) == 1
    assert items[0]["hasExtraction"] is True


# ---------------------------------------------------------------------------
# update_inclusion_dto
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_inclusion_dto_valid(session):
    """update_inclusion_dto 正确更新状态并返回 DTO。"""
    proj = await create_project(session, {"name": "Inclusion"})
    paper = await add_paper(session, {"title": "Incl Paper", "doi": "10.1/incl"})
    await add_paper_to_project(session, proj.id, paper.id)

    result = await project_svc.update_inclusion_dto(
        session, proj.id, paper.id, status="included", score=90
    )
    assert result["paperId"] == paper.id
    assert result["inclusionStatus"] == "included"
    assert result["screeningScore"] == 90


@pytest.mark.asyncio
async def test_update_inclusion_dto_excluded_with_reason(session):
    """excluded 状态（reason 字段由 set_inclusion 写入，DTO 不直接暴露 reason）。"""
    proj = await create_project(session, {"name": "Excl"})
    paper = await add_paper(session, {"title": "Excl Paper", "doi": "10.1/excl"})
    await add_paper_to_project(session, proj.id, paper.id)

    result = await project_svc.update_inclusion_dto(
        session, proj.id, paper.id, status="excluded", reason="Out of scope"
    )
    assert result["inclusionStatus"] == "excluded"


@pytest.mark.asyncio
async def test_update_inclusion_dto_invalid_status(session):
    """非法 status 应抛出 ApiError 400 VALIDATION_ERROR。"""
    proj = await create_project(session, {"name": "InvStatus"})
    paper = await add_paper(session, {"title": "Bad", "doi": "10.1/bad"})
    await add_paper_to_project(session, proj.id, paper.id)

    with pytest.raises(ApiError) as exc_info:
        await project_svc.update_inclusion_dto(
            session, proj.id, paper.id, status="unknown_status"
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_update_inclusion_dto_not_in_project(session):
    """文献未关联到项目时抛出 ApiError 404 PROJECT_PAPER_NOT_FOUND。"""
    proj = await create_project(session, {"name": "NotLinked"})
    paper = await add_paper(session, {"title": "Orphan", "doi": "10.1/orp2"})
    # 故意不 add_paper_to_project

    with pytest.raises(ApiError) as exc_info:
        await project_svc.update_inclusion_dto(
            session, proj.id, paper.id, status="included"
        )
    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "PROJECT_PAPER_NOT_FOUND"


# ---------------------------------------------------------------------------
# get_paper_detail_dto
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_paper_detail_dto_happy(session):
    """正常情况返回完整 detail DTO。"""
    proj = await create_project(session, {"name": "Detail"})
    paper = await add_paper(session, {
        "title": "Detail Paper",
        "doi": "10.1/det",
        "abstract": "An abstract.",
        "year": 2023,
    })
    await add_paper_to_project(session, proj.id, paper.id)

    dto = await project_svc.get_paper_detail_dto(session, proj.id, paper.id)
    assert dto["paperId"] == paper.id
    assert dto["title"] == "Detail Paper"
    assert dto["abstract"] == "An abstract."
    assert dto["inclusionStatus"] == "candidate"
    assert isinstance(dto["tags"], list)
    assert isinstance(dto["notes"], list)


@pytest.mark.asyncio
async def test_get_paper_detail_dto_not_in_project(session):
    """文献未关联项目时抛出 404。"""
    proj = await create_project(session, {"name": "NoLink"})
    paper = await add_paper(session, {"title": "Orp", "doi": "10.1/orp3"})

    with pytest.raises(ApiError) as exc_info:
        await project_svc.get_paper_detail_dto(session, proj.id, paper.id)
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# C项修复：update_inclusion_dto 返回真实 hasAbstract/hasPdf/ocrStatus
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_inclusion_dto_returns_real_status_fields(session):
    """C项: patch 后返回的 DTO 应含正确的 hasAbstract/hasPdf/ocrStatus 字段。

    无附件时：hasAbstract=True（有 abstract），hasPdf=False，ocrStatus="none"。
    """
    proj = await create_project(session, {"name": "StatusFieldTest"})
    paper = await add_paper(session, {
        "title": "Status Paper",
        "doi": "10.1/sftest",
        "abstract": "Some abstract text",
    })
    await add_paper_to_project(session, proj.id, paper.id)

    result = await project_svc.update_inclusion_dto(
        session, proj.id, paper.id, status="included"
    )

    # 基本字段
    assert result["paperId"] == paper.id
    assert result["inclusionStatus"] == "included"
    # C项：真实状态字段
    assert result["hasAbstract"] is True       # 有 abstract
    assert result["hasPdf"] is False           # 无附件
    assert result["ocrStatus"] == "none"       # 无附件 → none


@pytest.mark.asyncio
async def test_update_inclusion_dto_no_abstract_has_abstract_false(session):
    """C项: paper 无摘要时 hasAbstract 应为 False。"""
    proj = await create_project(session, {"name": "NoAbstractTest"})
    paper = await add_paper(session, {
        "title": "No Abstract Paper",
        "doi": "10.1/noabs",
        # 不设 abstract
    })
    await add_paper_to_project(session, proj.id, paper.id)

    result = await project_svc.update_inclusion_dto(
        session, proj.id, paper.id, status="candidate"
    )
    assert result["hasAbstract"] is False
    assert result["hasPdf"] is False
    assert result["ocrStatus"] == "none"
