"""Task 1.5: Project 仓储测试。

测试关注点:
  - create_project 写入，返回带 id 的 Project
  - add_paper_to_project 正常关联，inclusion_status 默认 candidate
  - add_paper_to_project 重复调用幂等（唯一约束不报错，返回已有行）
  - set_inclusion 更新 inclusion_status

session fixture 由 conftest.py 提供。
"""

async def test_create_project(session):
    """create_project 写入新 Project，返回带 id 的对象。"""
    from app.repositories.project import create_project

    proj = await create_project(session, {"name": "My Review", "research_question": "What is X?"})
    assert proj.id is not None
    assert proj.name == "My Review"
    assert proj.research_question == "What is X?"


async def test_add_paper_to_project(session):
    """add_paper_to_project 创建 ProjectPaper 关联，默认 inclusion_status=candidate。"""
    from app.repositories.project import create_project, add_paper_to_project
    from app.repositories.library import add_paper

    proj = await create_project(session, {"name": "P1"})
    paper = await add_paper(session, {"title": "Paper A", "doi": "10.1/a"})

    pp = await add_paper_to_project(session, proj.id, paper.id)
    assert pp.id is not None
    assert pp.project_id == proj.id
    assert pp.paper_id == paper.id
    assert pp.inclusion_status == "candidate"


async def test_add_paper_to_project_idempotent(session):
    """重复 add_paper_to_project 应幂等，不报唯一约束冲突，返回同一行 id。"""
    from app.repositories.project import create_project, add_paper_to_project
    from app.repositories.library import add_paper

    proj = await create_project(session, {"name": "P2"})
    paper = await add_paper(session, {"title": "Paper B", "doi": "10.1/b"})

    pp1 = await add_paper_to_project(session, proj.id, paper.id)
    pp2 = await add_paper_to_project(session, proj.id, paper.id)
    assert pp2.id == pp1.id


async def test_set_inclusion(session):
    """set_inclusion 能更新 inclusion_status 为 included/excluded/maybe。"""
    from app.repositories.project import create_project, add_paper_to_project, set_inclusion
    from app.repositories.library import add_paper

    proj = await create_project(session, {"name": "P3"})
    paper = await add_paper(session, {"title": "Paper C", "doi": "10.1/c"})
    pp = await add_paper_to_project(session, proj.id, paper.id)

    updated = await set_inclusion(session, pp.id, "included")
    assert updated.inclusion_status == "included"

    updated2 = await set_inclusion(session, pp.id, "excluded", reason="Out of scope")
    assert updated2.inclusion_status == "excluded"
    assert updated2.exclusion_reason == "Out of scope"
