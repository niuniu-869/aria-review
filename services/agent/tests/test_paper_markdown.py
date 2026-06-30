"""P3: GET /projects/{pid}/papers/{paperId}/markdown — MinerU 解析全文端点测试。

覆盖：
  1. 有 markdown 的 paper → 200 + available=true + length>0 + markdown 非空 + sha256。
  2. 无 attachment/markdown 的 paper → 200 + available=false（不 500）。
  3. 不存在 / 未关联本项目的 paper → 404。
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from app.config import settings
from app.db import get_session
from app.main import app, get_r_client
from app.models import Attachment, Paper, ProjectPaper
from app.repositories.project import create_project


@pytest_asyncio.fixture
async def aclient(session_factory, fake_r):
    async def _test_session():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_r_client] = lambda: fake_r
    app.dependency_overrides[get_session] = _test_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, session_factory
    app.dependency_overrides.clear()


_COUNTER = {"n": 0}


def _dedup() -> str:
    _COUNTER["n"] += 1
    return f"title:md-test-{_COUNTER['n']}"


async def _mk_paper(factory, pid: int, *, markdown: str | None) -> int:
    """建 Paper(+ProjectPaper)，markdown 非 None 时附 Attachment + 写盘。"""
    async with factory() as s:
        paper = Paper(
            title="MD Test Paper",
            creators=[],
            source="upload",
            item_type="journalArticle",
            dedup_key=_dedup(),
        )
        s.add(paper)
        await s.flush()
        if markdown is not None:
            # 端点路径约束：文件须在 fulltext/ 目录且名为 <sha256>.md，故测试照此布局
            _COUNTER["n"] += 1
            sha = f"{_COUNTER['n']:064x}"  # 64-hex 占位 sha
            md_dir = Path(settings.corpora_dir) / "fulltext"
            md_dir.mkdir(parents=True, exist_ok=True)
            md_path = md_dir / f"{sha}.md"
            md_path.write_text(markdown, encoding="utf-8")
            s.add(Attachment(
                paper_id=paper.id,
                mineru_status="done",
                markdown_path=str(md_path),
                sha256=sha,
            ))
        s.add(ProjectPaper(project_id=pid, paper_id=paper.id, inclusion_status="included"))
        await s.commit()
        return paper.id


@pytest.mark.asyncio
async def test_markdown_available(aclient):
    c, factory = aclient
    async with factory() as s:
        pid = (await create_project(s, {"name": "MD Avail"})).id
    paper_id = await _mk_paper(factory, pid, markdown="# 标题\n\n正文内容很多字。")
    r = await c.get(f"/projects/{pid}/papers/{paper_id}/markdown")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["available"] is True
    assert d["length"] > 0
    assert "正文内容很多字" in d["markdown"]
    assert d["sha256"]


@pytest.mark.asyncio
async def test_markdown_unavailable_when_no_attachment(aclient):
    c, factory = aclient
    async with factory() as s:
        pid = (await create_project(s, {"name": "MD None"})).id
    paper_id = await _mk_paper(factory, pid, markdown=None)
    r = await c.get(f"/projects/{pid}/papers/{paper_id}/markdown")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["available"] is False
    assert d["length"] == 0


@pytest.mark.asyncio
async def test_markdown_404_for_unlinked_paper(aclient):
    c, factory = aclient
    async with factory() as s:
        pid = (await create_project(s, {"name": "MD 404"})).id
    r = await c.get(f"/projects/{pid}/papers/999999/markdown")
    assert r.status_code == 404
