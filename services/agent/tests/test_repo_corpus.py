"""Task 1.5: Corpus 仓储测试。

测试关注点:
  - build_corpus_snapshot 从 project included 论文构建 Corpus + CorpusPaper 冻结快照
  - corpus_paper 行数 = included 论文数
  - corpus_paper 含正确的 inclusion_status_snapshot / record_hash / csl_json_snapshot
  - content_hash 幂等：相同集合重复调用返回同一 Corpus.id，不新建
  - 只有 included 的论文进入快照（candidate/excluded 不进）
  - content_hash 顺序无关：同 included 集合不同 order → 同一 Corpus（锁 M2）
  - 空 included 集合：不报错，corpus_paper 0 行（锁 M2）

session fixture 由 conftest.py 提供。
"""
import hashlib
import json
import pytest
import pytest_asyncio
from sqlalchemy import select


@pytest_asyncio.fixture
async def project_with_papers(session):
    """建 1 个 project + 3 篇 paper，其中 2 篇 included、1 篇 excluded。"""
    from app.repositories.library import add_paper
    from app.repositories.project import create_project, add_paper_to_project, set_inclusion

    proj = await create_project(session, {"name": "Corpus Test Project"})

    p1 = await add_paper(session, {"title": "Paper 1", "doi": "10.1/p1",
                                    "csl_json": {"title": "Paper 1", "DOI": "10.1/p1"}})
    p2 = await add_paper(session, {"title": "Paper 2", "doi": "10.1/p2",
                                    "csl_json": {"title": "Paper 2", "DOI": "10.1/p2"}})
    p3 = await add_paper(session, {"title": "Paper 3 (excluded)", "doi": "10.1/p3"})

    pp1 = await add_paper_to_project(session, proj.id, p1.id)
    pp2 = await add_paper_to_project(session, proj.id, p2.id)
    pp3 = await add_paper_to_project(session, proj.id, p3.id)

    await set_inclusion(session, pp1.id, "included")
    await set_inclusion(session, pp2.id, "included")
    await set_inclusion(session, pp3.id, "excluded", reason="OOS")

    return proj, [p1, p2], p3


async def test_build_corpus_snapshot_creates_corpus(session, project_with_papers):
    """build_corpus_snapshot 创建 Corpus 记录，document_count = included 论文数。"""
    from app.repositories.corpus import build_corpus_snapshot

    proj, included_papers, _ = project_with_papers
    corpus = await build_corpus_snapshot(session, proj.id)

    assert corpus.id is not None
    assert corpus.project_id == proj.id
    assert corpus.document_count == 2
    assert corpus.content_hash  # 非空


async def test_build_corpus_snapshot_creates_corpus_papers(session, project_with_papers):
    """build_corpus_snapshot 写入正确数量的 corpus_paper 冻结快照行。"""
    from app.repositories.corpus import build_corpus_snapshot
    from app.models import CorpusPaper

    proj, included_papers, excluded_paper = project_with_papers
    corpus = await build_corpus_snapshot(session, proj.id)

    q = select(CorpusPaper).where(CorpusPaper.corpus_id == corpus.id)
    rows = (await session.execute(q)).scalars().all()

    assert len(rows) == 2  # 只含 included 的 2 篇

    paper_ids = {r.paper_id for r in rows}
    assert {p.id for p in included_papers} == paper_ids

    # excluded 论文不应在快照里
    assert excluded_paper.id not in paper_ids


async def test_corpus_paper_snapshot_fields(session, project_with_papers):
    """corpus_paper 快照包含 inclusion_status_snapshot / record_hash / csl_json_snapshot。"""
    from app.repositories.corpus import build_corpus_snapshot
    from app.models import CorpusPaper

    proj, included_papers, _ = project_with_papers
    corpus = await build_corpus_snapshot(session, proj.id)

    q = select(CorpusPaper).where(CorpusPaper.corpus_id == corpus.id)
    rows = (await session.execute(q)).scalars().all()

    for row in rows:
        assert row.inclusion_status_snapshot == "included"
        assert row.record_hash  # 非空 hash
        # p1/p2 有 csl_json，快照应复制
        paper = next(p for p in included_papers if p.id == row.paper_id)
        if paper.csl_json:
            assert row.csl_json_snapshot == paper.csl_json


async def test_build_corpus_snapshot_idempotent(session, project_with_papers):
    """相同 included 集合重复调用返回同一 Corpus（content_hash 唯一约束幂等）。"""
    from app.repositories.corpus import build_corpus_snapshot

    proj, _, _ = project_with_papers
    c1 = await build_corpus_snapshot(session, proj.id)
    c2 = await build_corpus_snapshot(session, proj.id)
    assert c1.id == c2.id
    assert c1.content_hash == c2.content_hash


async def test_build_corpus_snapshot_only_included(session, project_with_papers):
    """candidate 状态的论文不进入 corpus 快照。"""
    from app.repositories.corpus import build_corpus_snapshot
    from app.repositories.project import add_paper_to_project
    from app.repositories.library import add_paper
    from app.models import CorpusPaper

    proj, included_papers, _ = project_with_papers
    # 加一篇 candidate（未设 inclusion）
    p_cand = await add_paper(session, {"title": "Candidate Only", "doi": "10.1/cand"})
    await add_paper_to_project(session, proj.id, p_cand.id)  # 默认 candidate

    corpus = await build_corpus_snapshot(session, proj.id)
    q = select(CorpusPaper).where(CorpusPaper.corpus_id == corpus.id)
    rows = (await session.execute(q)).scalars().all()

    paper_ids = {r.paper_id for r in rows}
    assert p_cand.id not in paper_ids
    assert len(rows) == 2  # 只有原 2 篇 included


async def test_content_hash_order_independent(session):
    """同一 included 集合，ProjectPaper.order 不同 → content_hash 相同（顺序无关）。

    锁 M2：_content_hash 对 record_hashes 排序后聚合，顺序无关。
    """
    from app.repositories.library import add_paper
    from app.repositories.project import create_project, add_paper_to_project, set_inclusion
    from app.repositories.corpus import build_corpus_snapshot

    proj = await create_project(session, {"name": "Order Test"})
    p1 = await add_paper(session, {"title": "Paper Alpha", "doi": "10.1/alpha"})
    p2 = await add_paper(session, {"title": "Paper Beta", "doi": "10.1/beta"})

    # 先以 order=0,1 添加
    pp1 = await add_paper_to_project(session, proj.id, p1.id, order=0)
    pp2 = await add_paper_to_project(session, proj.id, p2.id, order=1)
    await set_inclusion(session, pp1.id, "included")
    await set_inclusion(session, pp2.id, "included")

    c1 = await build_corpus_snapshot(session, proj.id)

    # 改变 order（不影响论文集合本身）
    from sqlalchemy import update
    from app.models import ProjectPaper
    await session.execute(
        update(ProjectPaper).where(ProjectPaper.id == pp1.id).values(order=99)
    )
    await session.execute(
        update(ProjectPaper).where(ProjectPaper.id == pp2.id).values(order=0)
    )
    await session.commit()

    c2 = await build_corpus_snapshot(session, proj.id)

    assert c1.content_hash == c2.content_hash, "content_hash 应与论文集顺序无关"
    assert c1.id == c2.id, "相同 content_hash 应命中同一 Corpus 行"


async def test_build_corpus_snapshot_empty_included(session):
    """空 included 集合：不报错，corpus_paper 0 行，corpus.document_count = 0。

    锁 M2：边界情况不抛异常。
    """
    from app.repositories.project import create_project
    from app.repositories.corpus import build_corpus_snapshot
    from app.models import CorpusPaper

    proj = await create_project(session, {"name": "Empty Corpus Project"})
    corpus = await build_corpus_snapshot(session, proj.id)

    assert corpus.id is not None
    assert corpus.document_count == 0

    q = select(CorpusPaper).where(CorpusPaper.corpus_id == corpus.id)
    rows = (await session.execute(q)).scalars().all()
    assert len(rows) == 0, "空集合不应有 corpus_paper 行"
