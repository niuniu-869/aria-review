"""A2 · read_paper FileReader 导航单测（纯函数 + 工具层）。

覆盖：
- build_outline 行号/标题正确，附页标签。
- read_section 逐字切片 + max_chars 截断 + 越界夹取。
- search_evidence 命中带源坐标(block_idx/page_no/bbox/section_title)，且返回的 quote
  可经 EvidenceResolver 回定位到同一 block（零伪造，只采 exact/partial）。
- 工具层 outline/section/search_evidence 经 context 注入 papers 跑通。

领域无关验证：fixture 用工程领域语料（crashworthiness/有限元），证明导航非商科特化。
离线：纯结构操作零 LLM。
"""
from __future__ import annotations

import pytest

from app.structure.blocks import EvidenceResolver
from app.structure.page_map import build_line_page_map
from app.structure.reader import build_outline, read_section, search_evidence
from app.tools.read_paper import ReadPaperTool


# 工程领域 fixture（crashworthiness / 智能结构），证明领域无关。
FULL_MD = (
    "# Introduction\n"
    "Crashworthiness of thin-walled tubes under axial impact is a key safety metric.\n"
    "\n"
    "# Methods\n"
    "We adopt nonlinear finite element simulation in LS-DYNA to study energy absorption.\n"
    "\n"
    "# Results\n"
    "Specific energy absorption increased by 23 percent for the foam-filled design.\n"
)

CONTENT_LIST = [
    {"type": "text", "text": "Introduction", "text_level": 1, "page_idx": 0, "bbox": [0, 0, 100, 10]},
    {"type": "text", "text": "Crashworthiness of thin-walled tubes under axial impact is a key safety metric.",
     "page_idx": 0, "bbox": [0, 12, 100, 30]},
    {"type": "text", "text": "Methods", "text_level": 1, "page_idx": 1, "bbox": [0, 0, 100, 10]},
    {"type": "text", "text": "We adopt nonlinear finite element simulation in LS-DYNA to study energy absorption.",
     "page_idx": 1, "bbox": [0, 12, 100, 30]},
    {"type": "text", "text": "Specific energy absorption increased by 23 percent for the foam-filled design.",
     "page_idx": 2, "bbox": [0, 12, 100, 30]},
]


# ----------------------------------------------------------------- build_outline

def test_outline_titles_and_line_ranges():
    out = build_outline(FULL_MD)
    titles = [s["title"] for s in out]
    assert titles == ["Introduction", "Methods", "Results"]
    # 1-based 行号：'# Introduction' 在第 1 行
    assert out[0]["start_line"] == 1
    # 章节区间相接、递增
    assert out[0]["end_line"] >= out[0]["start_line"]
    assert out[1]["start_line"] > out[0]["end_line"] or out[1]["start_line"] == out[0]["end_line"] + 1


def test_outline_attaches_page_labels_when_page_map_given():
    page_map = build_line_page_map(FULL_MD, CONTENT_LIST)
    out = build_outline(FULL_MD, page_map=page_map)
    assert all("page_label" in s and s["page_label"] for s in out)


# ----------------------------------------------------------------- read_section

def test_read_section_verbatim_slice():
    out = build_outline(FULL_MD)
    methods = next(s for s in out if s["title"] == "Methods")
    sec = read_section(FULL_MD, methods["start_line"], methods["end_line"])
    assert "finite element simulation" in sec["text"]
    assert "Introduction" not in sec["text"]  # 不串到别的章节
    assert sec["truncated"] is False


def test_read_section_max_chars_truncates():
    sec = read_section(FULL_MD, 1, 999, max_chars=20)
    assert sec["truncated"] is True
    assert len(sec["text"]) == 20
    assert sec["total_chars"] > 20


def test_read_section_out_of_range_clamps():
    sec = read_section(FULL_MD, 1000, 2000)
    assert sec["text"] == ""  # 越界 → 空，不抛


# ----------------------------------------------------------------- search_evidence

def test_search_evidence_hit_carries_source_coords():
    hits = search_evidence(CONTENT_LIST, "energy absorption")
    assert hits, "应命中含 'energy absorption' 的块"
    h = hits[0]
    assert h["block_idx"] == 3
    assert h["page_no"] == 2  # page_idx=1 → 1-based 第 2 页
    assert h["section_title"] == "Methods"
    assert h["bbox"] == [0, 12, 100, 30]
    assert h["match_quality"] in ("exact", "partial")


def test_search_evidence_quote_reresolvable():
    """返回的 quote 必须可经 EvidenceResolver 回定位到同一 block（溯源闭环）。"""
    hits = search_evidence(CONTENT_LIST, "finite element")
    assert hits
    h = hits[0]
    loc = EvidenceResolver(CONTENT_LIST).resolve(h["quote"])
    assert loc["found"] and loc["block_idx"] == h["block_idx"]


def test_search_evidence_no_hit_returns_empty():
    assert search_evidence(CONTENT_LIST, "量子纠缠与黑洞蒸发") == []


def test_search_evidence_empty_inputs():
    assert search_evidence([], "x") == []
    assert search_evidence(CONTENT_LIST, "") == []


# ----------------------------------------------------------------- ReadPaperTool

def _ctx_with_paper(paper_id=7):
    return {"papers": {paper_id: {"full_md": FULL_MD, "content_list": CONTENT_LIST}}}


async def test_tool_outline():
    tool = ReadPaperTool()
    r = await tool.execute("outline", {"paper_id": 7}, _ctx_with_paper())
    assert r.success
    assert [d["title"] for d in r.data] == ["Introduction", "Methods", "Results"]


async def test_tool_section():
    tool = ReadPaperTool()
    r = await tool.execute("section", {"paper_id": 7, "start_line": 4, "end_line": 5}, _ctx_with_paper())
    assert r.success and len(r.data) == 1
    assert "finite element" in r.data[0]["text"]


async def test_tool_search_evidence():
    tool = ReadPaperTool()
    r = await tool.execute("search_evidence", {"paper_id": 7, "query": "energy absorption"}, _ctx_with_paper())
    assert r.success and r.data
    assert r.data[0]["block_idx"] == 3


async def test_tool_unknown_paper_fails_loud():
    tool = ReadPaperTool()
    r = await tool.execute("outline", {"paper_id": 999}, _ctx_with_paper())
    assert r.success is False  # 无法加载该 paper → 显式失败


async def test_tool_missing_paper_id_fails():
    tool = ReadPaperTool()
    r = await tool.execute("outline", {}, _ctx_with_paper())
    assert r.success is False


# ----------------------------------------------------------------- 注册可达性

def test_research_tools_reachable_in_default_registry():
    """read_paper / scratchpad 必须进默认工具池，否则真实 agent run 拿不到 function def
    （codex A2 P1）。也证明 GAP/价值 subagent 经 tool_ids 选择子集时确有目标工具可选。"""
    from app.agent.registry_factory import build_registry

    reg = build_registry(session_factory=None, r_client=None)
    assert reg.get("read_paper") is not None
    assert reg.get("scratchpad") is not None
    # scratchpad 写库 → 串行执行；read_paper 只读。
    assert reg.is_write_tool("scratchpad") is True
    assert reg.is_write_tool("read_paper") is False
    # function definitions 暴露三个 read_paper action + 三个 scratchpad action
    fns = {f["function"]["name"] for f in reg.get_function_definitions()}
    assert {"read_paper__outline", "read_paper__section", "read_paper__search_evidence"} <= fns
    assert {"scratchpad__add", "scratchpad__update", "scratchpad__list"} <= fns


# ----------------------------------------------------------------- F-12 失败原因区分

async def _mk_project(session_factory, name="F12"):
    from app.repositories.project import create_project
    async with session_factory() as s:
        proj = await create_project(s, {"name": name})
        return proj.id


async def _mk_paper(session_factory, title="T"):
    from app.repositories.library import add_paper
    async with session_factory() as s:
        paper = await add_paper(s, {"title": title, "source": "upload"})
        return paper.id


async def test_fail_reason_not_in_project(session_factory):
    """paper 存在但未关联项目 → not_in_project（文献不在本项目中）。"""
    project_id = await _mk_project(session_factory)
    paper_id = await _mk_paper(session_factory)

    tool = ReadPaperTool()
    r = await tool.execute(
        "outline", {"paper_id": paper_id},
        {"session_factory": session_factory, "project_id": project_id},
    )
    assert r.success is False
    assert "文献不在本项目中" in (r.error or "")


async def test_fail_reason_no_attachment(session_factory):
    """paper 已关联项目但无任何附件/结构 → no_attachment（尚无可用全文）。"""
    from app.repositories.project import add_paper_to_project

    project_id = await _mk_project(session_factory)
    paper_id = await _mk_paper(session_factory)
    async with session_factory() as s:
        await add_paper_to_project(s, project_id, paper_id)
        await s.commit()

    tool = ReadPaperTool()
    r = await tool.execute(
        "outline", {"paper_id": paper_id},
        {"session_factory": session_factory, "project_id": project_id},
    )
    assert r.success is False
    assert "文献尚无可用全文" in (r.error or "")


async def test_fail_reason_markdown_unreadable(session_factory, tmp_path):
    """有 markdown 附件但文件缺失（路径护栏/读盘失败）且无结构 → markdown_unreadable。"""
    from app.models import Attachment
    from app.repositories.project import add_paper_to_project

    project_id = await _mk_project(session_factory)
    paper_id = await _mk_paper(session_factory)
    sha = "d" * 64
    async with session_factory() as s:
        # markdown_path 指向不存在的文件 → 护栏 is_file 检查失败
        s.add(Attachment(
            paper_id=paper_id, path=str(tmp_path / "x.pdf"), sha256=sha,
            mineru_status="done",
            markdown_path=str(tmp_path / "fulltext" / f"{sha}.md"),
        ))
        await add_paper_to_project(s, project_id, paper_id)
        await s.commit()

    tool = ReadPaperTool()
    r = await tool.execute(
        "outline", {"paper_id": paper_id},
        {"session_factory": session_factory, "project_id": project_id},
    )
    assert r.success is False
    assert "全文文件读取失败" in (r.error or "")


async def test_db_load_happy_path_unchanged(session_factory, tmp_path):
    """DB 路径 happy path 不变：markdown 在 fulltext/ 下且文件名 <sha256>.md → outline 成功。"""
    from app.models import Attachment
    from app.repositories.project import add_paper_to_project

    project_id = await _mk_project(session_factory)
    paper_id = await _mk_paper(session_factory)
    sha = "e" * 64
    md_dir = tmp_path / "fulltext"
    md_dir.mkdir()
    (md_dir / f"{sha}.md").write_text(FULL_MD, encoding="utf-8")
    async with session_factory() as s:
        s.add(Attachment(
            paper_id=paper_id, path=str(tmp_path / "x.pdf"), sha256=sha,
            mineru_status="done", markdown_path=str(md_dir / f"{sha}.md"),
        ))
        await add_paper_to_project(s, project_id, paper_id)
        await s.commit()

    tool = ReadPaperTool()
    r = await tool.execute(
        "outline", {"paper_id": paper_id},
        {"session_factory": session_factory, "project_id": project_id},
    )
    assert r.success, r.error
    assert [d["title"] for d in r.data] == ["Introduction", "Methods", "Results"]
