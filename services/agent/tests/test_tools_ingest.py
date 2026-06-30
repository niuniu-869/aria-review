"""IngestTool 单元测试（离线，FakeLLM/patch parse_pdfs + 测试库）。

覆盖（对齐作战方案 §10.2 工程坑）：
  1. parse 显式 paths：解析成功 → 建 Paper/Attachment + add_paper_to_project（项目可见）
  2. cached 计入成功：第二次解析同 PDF（sha256 缓存命中）→ status=cached 仍算成功
  3. 幂等关联：同一 PDF 重复 parse → 第二次 already_linked，不重复建 ProjectPaper
  4. 失败隔离：单篇 MinerU failed → failed 计数，其余篇仍成功且关联
  5. parse 项目内未解析论文（省略 paths）：按附件 path 解析尚未 OCR-done 的篇
  6. 空批次：项目内无待解析论文 → success=True、空 data、合理 summary
  7. registry 注册 + function_definitions 合法（ingest__parse）
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.harness.tools import ToolRegistry
from app.models import Attachment, Paper, ProjectPaper
from app.repositories.library import add_paper
from app.repositories.project import (
    add_paper_to_project,
    create_project,
    find_project_paper,
)
from app.tools.ingest import IngestTool


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def session_factory(session):
    return async_sessionmaker(session.bind, expire_on_commit=False)


@pytest.fixture(autouse=True)
def isolated_corpora(tmp_path, monkeypatch):
    """每测试用独立 corpora_dir，避免 sha256 markdown 缓存跨测试泄漏（done↔cached 漂移）。"""
    monkeypatch.setattr("app.config.settings.corpora_dir", str(tmp_path / "corpora"))


_SAMPLE_MD = """\
# Ingest Tool Test Paper

Authors: Alice Test

## Abstract

This paper validates the IngestTool agent wiring.
"""


def _fake_parse_pdfs_factory(results_map: dict | None = None, raise_exc: bool = False):
    """patch app.ingest.fulltext.parse_pdfs：按文件名映射 done/failed 状态。"""

    async def _fake(paths, language="en", max_files=200, *, _client=None):
        if raise_exc:
            raise RuntimeError("MinerU 整批失败（模拟）")
        out = []
        for p in paths:
            p = Path(p)
            status = (results_map or {}).get(p.name, "done")
            out.append({
                "name": p.name,
                "path": str(p),
                "status": status,
                "markdown": _SAMPLE_MD if status == "done" else None,
                "err": None if status == "done" else "MinerU 模拟失败",
            })
        return out

    return _fake


async def _new_project(factory, name: str = "Ingest Test") -> int:
    async with factory() as s:
        proj = await create_project(s, {"name": name})
        return proj.id


def _make_pdf(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_bytes(f"%PDF-1.4 {content}".encode())
    return p


# ---------------------------------------------------------------------------
# Test 1: parse 显式 paths → 建库 + add_paper_to_project
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_parse_explicit_paths_links_to_project(session_factory, tmp_path, monkeypatch):
    pid = await _new_project(session_factory)
    pdf = _make_pdf(tmp_path, "a.pdf", "alpha")

    monkeypatch.setattr("app.ingest.fulltext.parse_pdfs", _fake_parse_pdfs_factory())

    tool = IngestTool(session_factory)
    r = await tool.execute("parse", {"project_id": pid, "paths": [str(pdf)]})

    assert r.success, r.error
    assert len(r.data) == 1
    assert r.data[0]["status"] == "done"
    paper_id = r.data[0]["paper_id"]
    assert paper_id is not None
    assert "解析 1 篇" in r.summary
    assert "新关联到项目 1 篇" in r.summary

    # add_paper_to_project 生效：ReviewTool 才看得到 included 语料
    async with session_factory() as s:
        pp = await find_project_paper(s, pid, paper_id)
        assert pp is not None, "解析后必须 add_paper_to_project"
        att = (await s.execute(
            select(Attachment).where(Attachment.paper_id == paper_id)
        )).scalar_one()
        assert att.mineru_status == "done"
        assert att.markdown_path and Path(att.markdown_path).exists()


# ---------------------------------------------------------------------------
# Test 2 + 3: cached 计成功 + 幂等关联（重复 parse 同 PDF）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cached_counts_as_success_and_link_idempotent(session_factory, tmp_path, monkeypatch):
    pid = await _new_project(session_factory)
    pdf = _make_pdf(tmp_path, "cache.pdf", "cache-content")

    monkeypatch.setattr("app.ingest.fulltext.parse_pdfs", _fake_parse_pdfs_factory())

    tool = IngestTool(session_factory)
    r1 = await tool.execute("parse", {"project_id": pid, "paths": [str(pdf)]})
    assert r1.success and r1.data[0]["status"] == "done"

    # 第二次：sha256.md 已存在 → cached（仍算成功），关联幂等（already_linked）
    r2 = await tool.execute("parse", {"project_id": pid, "paths": [str(pdf)]})
    assert r2.success, r2.error
    assert r2.data[0]["status"] == "cached", "缓存命中应为 cached"
    assert "解析 1 篇（含缓存命中 1 篇）" in r2.summary, r2.summary
    assert "已在项目 1 篇" in r2.summary

    # ProjectPaper 不重复建（幂等）
    async with session_factory() as s:
        cnt = (await s.execute(
            select(func.count()).select_from(ProjectPaper).where(ProjectPaper.project_id == pid)
        )).scalar_one()
        assert cnt == 1, f"重复 parse 不应重复关联，实际 {cnt}"


# ---------------------------------------------------------------------------
# Test 4: 失败隔离 — 单篇 failed 不拖垮其他篇
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failure_isolation(session_factory, tmp_path, monkeypatch):
    pid = await _new_project(session_factory)
    good = _make_pdf(tmp_path, "good.pdf", "g")
    bad = _make_pdf(tmp_path, "bad.pdf", "b")

    monkeypatch.setattr(
        "app.ingest.fulltext.parse_pdfs",
        _fake_parse_pdfs_factory({"bad.pdf": "failed"}),
    )

    tool = IngestTool(session_factory)
    r = await tool.execute("parse", {"project_id": pid, "paths": [str(good), str(bad)]})

    assert r.success, r.error
    statuses = {row["pdf"]: row["status"] for row in r.data}
    assert statuses["good.pdf"] == "done"
    assert statuses["bad.pdf"] == "failed"
    assert "解析 1 篇" in r.summary
    assert "失败 1 篇" in r.summary

    # good 篇仍关联到项目；bad 篇没有 paper
    async with session_factory() as s:
        cnt = (await s.execute(
            select(func.count()).select_from(ProjectPaper).where(ProjectPaper.project_id == pid)
        )).scalar_one()
        assert cnt == 1


# ---------------------------------------------------------------------------
# Test 5: parse 项目内未解析论文（省略 paths）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_parse_project_unparsed_papers(session_factory, tmp_path, monkeypatch):
    pid = await _new_project(session_factory)
    pdf = _make_pdf(tmp_path, "unparsed.pdf", "u")

    # 种一篇 paper + attachment（有 path，但 mineru_status != done）+ 关联项目
    async with session_factory() as s:
        paper = await add_paper(s, {"title": "Unparsed Paper", "source": "upload"})
        att = Attachment(paper_id=paper.id, path=str(pdf), mineru_status="pending")
        s.add(att)
        await add_paper_to_project(s, pid, paper.id)
        await s.commit()
        target_paper_id = paper.id

    monkeypatch.setattr("app.ingest.fulltext.parse_pdfs", _fake_parse_pdfs_factory())

    tool = IngestTool(session_factory)
    # 省略 paths → 自动解析项目内未 OCR-done 的论文
    r = await tool.execute("parse", {"project_id": pid})

    assert r.success, r.error
    assert len(r.data) == 1
    assert r.data[0]["status"] == "done"

    # 同一 PDF 内容会 dedup 到同一 paper（add_paper 幂等）；已存在关联 → already_linked
    async with session_factory() as s:
        # 该 paper 现在应有 done 附件（sha256 缓存或新解析）
        done_atts = (await s.execute(
            select(func.count()).select_from(Attachment).where(
                Attachment.mineru_status == "done"
            )
        )).scalar_one()
        assert done_atts >= 1


# ---------------------------------------------------------------------------
# Test 5b: 项目模式收敛 — 无 paths 连调两次，第二次必为空批次（codex P0-1-fix）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_project_mode_converges_no_duplicate_stacking(
    session_factory, tmp_path, monkeypatch,
):
    """同项目无 paths 连调两次：第二次必空批次、原 pending 附件已回写 done、
    不堆叠重复 done 附件，且第二次不新建 Paper。

    复现并锁死 codex P0-1 缺陷：旧实现只回原始 path、ingest 按 OCR 标题另建 Paper，
    从不回写原 pending 附件 → 原 paper 永不进 done 集合 → 同 path 反复被选中、
    缓存命中后堆叠重复 done 附件，永不收敛。
    """
    pid = await _new_project(session_factory)
    pdf = _make_pdf(tmp_path, "converge.pdf", "converge-content")

    # 种一篇 paper（标题与 OCR markdown 标题不同 → dedup 到不同 paper）+ pending 附件
    async with session_factory() as s:
        paper = await add_paper(s, {"title": "Seed Title Differs From OCR", "source": "upload"})
        s.add(Attachment(paper_id=paper.id, path=str(pdf), mineru_status="pending"))
        await add_paper_to_project(s, pid, paper.id)
        await s.commit()
        seed_paper_id = paper.id
        seed_att_id = (await s.execute(
            select(Attachment.id).where(Attachment.paper_id == seed_paper_id)
        )).scalar_one()

    monkeypatch.setattr("app.ingest.fulltext.parse_pdfs", _fake_parse_pdfs_factory())

    tool = IngestTool(session_factory)

    # 第一次：省略 paths → 解析项目内未 OCR-done 的篇
    r1 = await tool.execute("parse", {"project_id": pid})
    assert r1.success, r1.error
    assert len(r1.data) == 1 and r1.data[0]["status"] == "done"

    # 原始 pending 附件已回写为 done（带 markdown_path）→ 退出未解析队列
    async with session_factory() as s:
        seed_att = await s.get(Attachment, seed_att_id)
        assert seed_att.mineru_status == "done", "原始 pending 附件必须回写为 done（收敛关键）"
        assert seed_att.markdown_path, "回写 done 的附件必须带 markdown_path"
        paper_cnt_after_r1 = (await s.execute(
            select(func.count()).select_from(Paper)
        )).scalar_one()
        done_att_after_r1 = (await s.execute(
            select(func.count()).select_from(Attachment).where(
                Attachment.mineru_status == "done"
            )
        )).scalar_one()

    # 第二次：必为空批次（已收敛），绝不再次处理同 path
    r2 = await tool.execute("parse", {"project_id": pid})
    assert r2.success, r2.error
    assert r2.data == [], f"第二次连调必为空批次（已收敛），实际 {r2.data}"

    # 第二次不新建 Paper、不堆叠新 done 附件
    async with session_factory() as s:
        paper_cnt_after_r2 = (await s.execute(
            select(func.count()).select_from(Paper)
        )).scalar_one()
        done_att_after_r2 = (await s.execute(
            select(func.count()).select_from(Attachment).where(
                Attachment.mineru_status == "done"
            )
        )).scalar_one()

    assert paper_cnt_after_r2 == paper_cnt_after_r1, (
        f"第二次连调不应新建 Paper（r1={paper_cnt_after_r1} r2={paper_cnt_after_r2}）"
    )
    assert done_att_after_r2 == done_att_after_r1, (
        f"第二次连调不应堆叠 done 附件（r1={done_att_after_r1} r2={done_att_after_r2}）"
    )


# ---------------------------------------------------------------------------
# Test 6: 空批次 — 项目内无待解析论文
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_batch_no_unparsed(session_factory):
    pid = await _new_project(session_factory)
    tool = IngestTool(session_factory)
    r = await tool.execute("parse", {"project_id": pid})

    assert r.success
    assert r.data == []
    assert "没有尚未解析" in r.summary or "无需解析" in r.summary


@pytest.mark.asyncio
async def test_missing_project_id(session_factory):
    tool = IngestTool(session_factory)
    r = await tool.execute("parse", {})
    assert not r.success
    assert "project_id" in (r.error or "")


# ---------------------------------------------------------------------------
# Test 7: registry 注册 + function_definitions
# ---------------------------------------------------------------------------

def test_ingest_tool_function_definitions(session_factory):
    reg = ToolRegistry()
    reg.register(IngestTool(session_factory))

    assert reg.is_write_tool("ingest"), "IngestTool 应为写工具（串行）"

    defs = reg.get_function_definitions()
    names = {d["function"]["name"] for d in defs}
    assert "ingest__parse" in names

    for fd in defs:
        assert fd["type"] == "function"
        params = fd["function"]["parameters"]
        assert params["type"] == "object"
        assert "project_id" in params["properties"]
        assert "paths" in params["properties"]
        assert params["properties"]["paths"]["type"] == "array"
