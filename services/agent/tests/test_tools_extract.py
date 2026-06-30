"""ExtractTool 单元测试（离线，FakeLLM + 测试库）。

覆盖（对齐作战方案 §10.2 — 单篇函数复刻 endpoint 的项目内查询/limit/跳过已处理）：
  1. structured：1 篇 OCR-done → upsert paper_extraction，extracted=1
  2. structured 批量跳过已抽取（SQL 层）：3 篇，1 篇已抽取，reextract=false → 只处理 2 篇
  3. structured reextract=true → 全部处理
  4. structured limit 截断 + available 不受 limit 影响
  5. metadata：回填缺 abstract/creators 的篇，仅填空字段
  6. metadata only_missing=true 排除已全的篇
  7. 缺 project_id / 不支持 action
  8. registry 注册 + function_definitions（extract__structured / extract__metadata）

ExtractTool 用 app.tools.extract.get_llm_client 构造 LLM（非 app.main），
故测试直接 monkeypatch app.tools.extract.get_llm_client 注入 FakeLLM。
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.harness.tools import ToolRegistry
from app.models import Attachment, Paper, PaperExtraction, ProjectPaper
from app.repositories.extraction import upsert_extraction
from app.repositories.project import create_project
from app.tools.extract import ExtractTool


# ---------------------------------------------------------------------------
# FakeLLM + fixtures
# ---------------------------------------------------------------------------

class FakeLLM:
    def __init__(self, canned: str):
        self.canned = canned
        self.model = "fake-extract-model"

    async def complete(self, messages, **kwargs) -> str:
        return self.canned

    async def stream(self, messages, **kwargs):
        yield self.canned


_STRUCTURED_JSON = json.dumps({
    "research_question": "How does X affect Y in agentic systems?",
    "method": "Controlled experiment across 12 conditions.",
    "findings": "X improves Y by 18%.",
    "dataset": "Synthetic + real benchmark (4k samples).",
    "contribution": "First quantitative measurement of the X->Y effect.",
})

_METADATA_JSON = json.dumps({
    "abstract": "This paper studies agentic data pipelines and trust.",
    "authors": ["Alice Backfill", "Bob Meta"],
    "year": 2024,
    "keywords": ["agent", "trust", "pipeline"],
})


@pytest.fixture
def session_factory(session):
    return async_sessionmaker(session.bind, expire_on_commit=False)


def _patch_llm(monkeypatch, llm) -> None:
    monkeypatch.setattr("app.tools.extract.get_llm_client", lambda *a, **k: llm)


async def _new_project(factory, name: str = "Extract Tool Test") -> int:
    async with factory() as s:
        proj = await create_project(s, {"name": name})
        return proj.id


_COUNTER = {"n": 0}


async def _mk_ocr_paper(factory, pid: int, *, markdown: str = "# P\n\nbody.",
                        abstract=None, creators=None) -> int:
    """建 Paper + OCR-done Attachment（写真 markdown 文件）+ 关联项目，返回 paper_id。"""
    _COUNTER["n"] += 1
    md_dir = Path(tempfile.mkdtemp())
    md_path = md_dir / "doc.md"
    md_path.write_text(markdown, encoding="utf-8")

    async with factory() as s:
        paper = Paper(
            title="Extract Tool Paper",
            abstract=abstract,
            creators=creators if creators is not None else [],
            source="upload",
            item_type="journalArticle",
            dedup_key=f"title:extract-tool-{_COUNTER['n']}",
        )
        s.add(paper)
        await s.flush()
        s.add(Attachment(paper_id=paper.id, mineru_status="done", markdown_path=str(md_path)))
        s.add(ProjectPaper(project_id=pid, paper_id=paper.id, inclusion_status="candidate"))
        await s.commit()
        return paper.id


# ---------------------------------------------------------------------------
# structured
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_structured_creates_extraction(session_factory, monkeypatch):
    pid = await _new_project(session_factory)
    paper_id = await _mk_ocr_paper(session_factory, pid)
    _patch_llm(monkeypatch, FakeLLM(_STRUCTURED_JSON))

    tool = ExtractTool(session_factory)
    r = await tool.execute("structured", {"project_id": pid, "limit": 10})

    assert r.success, r.error
    row = r.data[0]
    assert row["processed"] == 1
    assert row["extracted"] == 1
    assert row["available"] == 0
    assert "结构化抽取" in r.summary

    async with session_factory() as s:
        ext = (await s.execute(
            select(PaperExtraction).where(PaperExtraction.paper_id == paper_id)
        )).scalar_one()
        assert ext.research_question and "agentic" in ext.research_question.lower()


@pytest.mark.asyncio
async def test_structured_skips_already_extracted_sql_level(session_factory, monkeypatch):
    """3 篇 OCR-done，1 篇已抽取，reextract=false limit=5 → 只处理 2 篇（SQL 层排除）。"""
    pid = await _new_project(session_factory)
    pids = [await _mk_ocr_paper(session_factory, pid) for _ in range(3)]

    # 预先给第 1 篇插入 extraction
    async with session_factory() as s:
        await upsert_extraction(s, pids[0], {
            "research_question": "pre", "method": "pre", "findings": "pre",
            "dataset": "pre", "contribution": "pre",
        }, model="pre")
        await s.commit()

    _patch_llm(monkeypatch, FakeLLM(_STRUCTURED_JSON))
    tool = ExtractTool(session_factory)
    r = await tool.execute("structured", {"project_id": pid, "limit": 5, "reextract": False})

    row = r.data[0]
    assert row["processed"] == 2, f"应只处理 2 篇未抽取，实际 {row['processed']}"
    assert row["extracted"] == 2
    assert row["available"] == 0

    # 已有 extraction 未被覆盖
    async with session_factory() as s:
        ext0 = (await s.execute(
            select(PaperExtraction).where(PaperExtraction.paper_id == pids[0])
        )).scalar_one()
        assert ext0.research_question == "pre"


@pytest.mark.asyncio
async def test_structured_reextract_processes_all(session_factory, monkeypatch):
    pid = await _new_project(session_factory)
    pids = [await _mk_ocr_paper(session_factory, pid) for _ in range(3)]
    async with session_factory() as s:
        for p in pids[:2]:
            await upsert_extraction(s, p, {
                "research_question": "old", "method": "old", "findings": "old",
                "dataset": "old", "contribution": "old",
            }, model="old")
        await s.commit()

    _patch_llm(monkeypatch, FakeLLM(_STRUCTURED_JSON))
    tool = ExtractTool(session_factory)
    r = await tool.execute("structured", {"project_id": pid, "limit": 10, "reextract": True})

    row = r.data[0]
    assert row["processed"] == 3
    assert row["extracted"] == 3
    assert row["available"] == 3


@pytest.mark.asyncio
async def test_structured_limit_and_available(session_factory, monkeypatch):
    pid = await _new_project(session_factory)
    for _ in range(5):
        await _mk_ocr_paper(session_factory, pid)

    _patch_llm(monkeypatch, FakeLLM(_STRUCTURED_JSON))
    tool = ExtractTool(session_factory)
    r = await tool.execute("structured", {"project_id": pid, "limit": 2})

    row = r.data[0]
    assert row["processed"] == 2
    assert row["extracted"] == 2
    # available = 处理后剩余待抽取（5 - 2 = 3）
    assert row["available"] == 3


# ---------------------------------------------------------------------------
# metadata
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_metadata_backfills_missing_fields(session_factory, monkeypatch):
    pid = await _new_project(session_factory)
    # 缺 abstract + creators 的篇
    paper_id = await _mk_ocr_paper(session_factory, pid, abstract=None, creators=[])
    _patch_llm(monkeypatch, FakeLLM(_METADATA_JSON))

    tool = ExtractTool(session_factory)
    r = await tool.execute("metadata", {"project_id": pid, "limit": 10})

    assert r.success, r.error
    row = r.data[0]
    assert row["processed"] == 1
    assert row["updated"] == 1
    assert "元数据回填" in r.summary

    async with session_factory() as s:
        paper = await s.get(Paper, paper_id)
        assert paper.abstract and "agentic" in paper.abstract.lower()
        assert paper.creators and len(paper.creators) == 2
        assert paper.year == 2024


@pytest.mark.asyncio
async def test_metadata_only_missing_excludes_complete(session_factory, monkeypatch):
    """only_missing=true：已有 abstract 且 creators 的篇被 SQL 排除 → processed=0。"""
    pid = await _new_project(session_factory)
    await _mk_ocr_paper(
        session_factory, pid,
        abstract="already has abstract",
        creators=[{"literal": "Existing Author"}],
    )

    _patch_llm(monkeypatch, FakeLLM(_METADATA_JSON))
    tool = ExtractTool(session_factory)
    r = await tool.execute("metadata", {"project_id": pid, "limit": 10, "only_missing": True})

    row = r.data[0]
    assert row["processed"] == 0, f"已全的篇应被排除，实际 {row['processed']}"
    assert row["available"] == 0


# ---------------------------------------------------------------------------
# 边界 + registry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_project_id(session_factory):
    tool = ExtractTool(session_factory)
    r = await tool.execute("structured", {})
    assert not r.success
    assert "project_id" in (r.error or "")


@pytest.mark.asyncio
async def test_unsupported_action(session_factory):
    tool = ExtractTool(session_factory)
    r = await tool.execute("bogus", {"project_id": 1})
    assert not r.success


def test_extract_tool_function_definitions(session_factory):
    reg = ToolRegistry()
    reg.register(ExtractTool(session_factory))

    assert reg.is_write_tool("extract"), "ExtractTool 应为写工具（串行）"

    defs = reg.get_function_definitions()
    names = {d["function"]["name"] for d in defs}
    assert "extract__structured" in names
    assert "extract__metadata" in names

    for fd in defs:
        assert fd["type"] == "function"
        params = fd["function"]["parameters"]
        assert params["type"] == "object"
        assert "project_id" in params["properties"]
