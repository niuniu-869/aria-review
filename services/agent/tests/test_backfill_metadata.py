"""P3-T1: POST /projects/{pid}/papers/backfill-metadata 元数据补全端点测试。

覆盖：
  1. 1 篇有 markdown、缺 abstract/creators → 调端点 → Paper.abstract/creators 被回填、updated=1
  2. 已有 abstract 的篇 → 不覆盖、skipped（onlyMissing=True 时不被选中，processed=0）
  3. 无 markdown 的篇（无 Attachment 或 mineru_status != done）→ skipped
  4. LLM 返回非 JSON → failed 不 500
  5. project 不存在 → 404 PROJECT_NOT_FOUND

  _parse_llm_json 单元测试（I-1 fix 验证）：
  6. 纯 JSON 字符串
  7. ```json fence 包裹
  8. JSON 后跟说明文字（旧贪婪正则会过度捕获导致失败）
  9. 前缀说明 + JSON
  10. 非 JSON → 返回 None
"""
from __future__ import annotations

import json
import tempfile
import textwrap
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.db import get_session
from app.main import app, get_r_client
from app.models import Attachment, Paper, ProjectPaper
from app.repositories.project import create_project


# ---------------------------------------------------------------------------
# FakeLLM fixture：可注入特定的 canned 返回文本
# ---------------------------------------------------------------------------

class FakeLLM:
    """可配置返回内容的离线 LLM（for backfill 测试）。"""

    def __init__(self, canned: str):
        self.canned = canned

    async def complete(self, messages, **kwargs) -> str:
        return self.canned

    async def stream(self, messages, **kwargs):
        yield self.canned


# ---------------------------------------------------------------------------
# aclient fixture（复用 conftest session_factory + fake_r）
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def aclient(session_factory, fake_r):
    """AsyncClient，覆盖 get_r_client 和 get_session（使用测试 DB）。"""

    async def _test_session():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_r_client] = lambda: fake_r
    app.dependency_overrides[get_session] = _test_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, session_factory
    app.dependency_overrides.clear()


async def _create_project(factory, name: str = "Backfill Test") -> int:
    async with factory() as s:
        proj = await create_project(s, {"name": name})
        return proj.id


_COUNTER = {"n": 0}


def _unique_dedup_key(prefix: str = "backfill-test") -> str:
    _COUNTER["n"] += 1
    return f"title:{prefix}-{_COUNTER['n']}"


async def _mk_paper_with_markdown(
    factory,
    pid: int,
    *,
    markdown_content: str,
    abstract: str | None = None,
    creators: list | None = None,
    year: int | None = None,
) -> tuple[int, Path]:
    """在测试 DB 中创建 Paper + Attachment(markdown_path)，并写 markdown 文件到临时目录。

    Returns:
        (paper_id, markdown_path)
    """
    md_dir = Path(tempfile.mkdtemp())
    md_path = md_dir / "test.md"
    md_path.write_text(markdown_content, encoding="utf-8")

    async with factory() as s:
        paper = Paper(
            title="Test Paper",
            abstract=abstract,
            creators=creators if creators is not None else [],
            year=year,
            source="upload",
            item_type="journalArticle",
            dedup_key=_unique_dedup_key(),
        )
        s.add(paper)
        await s.flush()

        att = Attachment(
            paper_id=paper.id,
            mineru_status="done",
            markdown_path=str(md_path),
        )
        s.add(att)

        pp = ProjectPaper(
            project_id=pid,
            paper_id=paper.id,
            inclusion_status="candidate",
        )
        s.add(pp)
        await s.commit()
        paper_id = paper.id

    return paper_id, md_path


async def _mk_paper_no_markdown(
    factory,
    pid: int,
    *,
    abstract: str | None = None,
) -> int:
    """在测试 DB 中创建 Paper（无 Attachment），返回 paper_id。"""
    async with factory() as s:
        paper = Paper(
            title="No Markdown Paper",
            abstract=abstract,
            creators=[],
            source="upload",
            item_type="journalArticle",
            dedup_key=_unique_dedup_key("no-md"),
        )
        s.add(paper)
        await s.flush()

        pp = ProjectPaper(
            project_id=pid,
            paper_id=paper.id,
            inclusion_status="candidate",
        )
        s.add(pp)
        await s.commit()
        return paper.id


# ---------------------------------------------------------------------------
# 测试 1: 1 篇有 markdown、缺 abstract/creators → 回填成功 updated=1
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backfill_updates_missing_fields(aclient, monkeypatch):
    """有 markdown + 缺 abstract/creators → LLM 返回 JSON → abstract/creators 被回填，updated=1。"""
    c, factory = aclient
    pid = await _create_project(factory, "Backfill Updates Test")

    md_content = textwrap.dedent("""\
        # Deep Learning for NLP: A Survey

        Authors: Zhang Wei; Li Ming; Wang Fang

        ## Abstract

        This paper presents a comprehensive survey of deep learning methods
        applied to natural language processing tasks.

        Keywords: deep learning, NLP, survey
    """)
    paper_id, _ = await _mk_paper_with_markdown(
        factory, pid,
        markdown_content=md_content,
        abstract=None,       # 缺 abstract
        creators=[],         # 缺 creators
    )

    # 注入 FakeLLM 返回结构化 JSON
    canned_json = json.dumps({
        "title": "Deep Learning for NLP: A Survey",
        "authors": ["Zhang Wei", "Li Ming", "Wang Fang"],
        "year": 2023,
        "abstract": "This paper presents a comprehensive survey of deep learning methods applied to NLP.",
        "keywords": ["deep learning", "NLP", "survey"],
    })
    fake_llm = FakeLLM(canned_json)
    monkeypatch.setattr("app.main.get_llm_client", lambda *a, **k: fake_llm)

    r = await c.post(f"/projects/{pid}/papers/backfill-metadata", json={"limit": 10, "onlyMissing": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["processed"] == 1
    assert body["updated"] == 1
    assert body["skipped"] == 0
    assert body["failed"] == 0
    # D-fix: available 为处理后真实剩余数，1 篇已成功回填，剩余 0 篇缺元数据
    assert body["available"] == 0, f"D-fix: 1 篇已回填，处理后 available 应为 0，实际 {body['available']}"

    # 验证 DB 字段被回填
    async with factory() as s:
        paper = (await s.execute(select(Paper).where(Paper.id == paper_id))).scalar_one()
        assert paper.abstract and "survey" in paper.abstract.lower()
        assert paper.creators and len(paper.creators) > 0
        assert paper.creators[0].get("literal") in ("Zhang Wei", "Li Ming", "Wang Fang")
        assert paper.year == 2023
        assert paper.keywords and "deep learning" in paper.keywords.lower()


# ---------------------------------------------------------------------------
# 测试 2: 已有 abstract 的篇 → onlyMissing=True 时不被选中
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backfill_skips_already_complete(aclient, monkeypatch):
    """abstract、creators、year 俱全的篇，onlyMissing=True 时不被选中（processed=0）。"""
    c, factory = aclient
    pid = await _create_project(factory, "Backfill Complete Test")

    md_content = "# Some Paper\n\nContent here."
    await _mk_paper_with_markdown(
        factory, pid,
        markdown_content=md_content,
        abstract="This is already a filled abstract.",
        creators=[{"literal": "Some Author"}],
        year=2020,  # F-05: year 也纳入 onlyMissing，需显式置齐才算「已完整」
    )

    fake_llm = FakeLLM('{"title": "x", "authors": ["New Author"], "year": 2020, "abstract": "overwrite", "keywords": []}')
    monkeypatch.setattr("app.main.get_llm_client", lambda *a, **k: fake_llm)

    r = await c.post(f"/projects/{pid}/papers/backfill-metadata", json={"limit": 10, "onlyMissing": True})
    assert r.status_code == 200, r.text
    body = r.json()
    # onlyMissing=True 时已完整的篇不被选入，所以 processed=0
    assert body["processed"] == 0
    assert body["updated"] == 0
    assert body["available"] == 0, f"所有篇均已完整，available 应为 0，实际 {body['available']}"


# ---------------------------------------------------------------------------
# 测试 3: 无 markdown 的篇 → skipped
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backfill_skips_no_markdown(aclient, monkeypatch):
    """无 Attachment（或 mineru_status != done）的篇 → skipped，不报 failed。"""
    c, factory = aclient
    pid = await _create_project(factory, "No Markdown Test")

    # 创建一篇无 Attachment 的 Paper（不设 markdown_path）
    paper_id = await _mk_paper_no_markdown(factory, pid)

    # 为无 Attachment 的 Paper 手动加一个 mineru_status=pending 的附件
    async with factory() as s:
        att = Attachment(
            paper_id=paper_id,
            mineru_status="pending",   # 非 done
            markdown_path=None,
        )
        s.add(att)
        await s.commit()

    fake_llm = FakeLLM('{"title": "x", "authors": [], "year": null, "abstract": null, "keywords": []}')
    monkeypatch.setattr("app.main.get_llm_client", lambda *a, **k: fake_llm)

    # onlyMissing=True：该 Paper 缺 abstract，但无 done 的 markdown → 查询中不被 att_sq 纳入
    r = await c.post(f"/projects/{pid}/papers/backfill-metadata", json={"limit": 10, "onlyMissing": True})
    assert r.status_code == 200, r.text
    body = r.json()
    # 因为 att_sq 只纳入 mineru_status=done 的 paper，本篇不被选中
    assert body["processed"] == 0
    assert body["failed"] == 0


# ---------------------------------------------------------------------------
# 测试 3b: 有 markdown_path 但文件不存在 → skipped（not updated/not failed）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backfill_skips_missing_markdown_file(aclient, monkeypatch):
    """Attachment.markdown_path 存在但文件不存在 → skipped，不报 failed。"""
    c, factory = aclient
    pid = await _create_project(factory, "Missing File Test")

    # 创建 Attachment with mineru_status=done 但指向不存在的文件路径
    async with factory() as s:
        paper = Paper(
            title="Missing File Paper",
            abstract=None,
            creators=[],
            source="upload",
            item_type="journalArticle",
            dedup_key=_unique_dedup_key("missing-file"),
        )
        s.add(paper)
        await s.flush()

        att = Attachment(
            paper_id=paper.id,
            mineru_status="done",
            markdown_path="/nonexistent/path/does_not_exist.md",
        )
        s.add(att)

        pp = ProjectPaper(
            project_id=pid,
            paper_id=paper.id,
            inclusion_status="candidate",
        )
        s.add(pp)
        await s.commit()

    fake_llm = FakeLLM('{}')
    monkeypatch.setattr("app.main.get_llm_client", lambda *a, **k: fake_llm)

    r = await c.post(f"/projects/{pid}/papers/backfill-metadata", json={"limit": 10, "onlyMissing": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["processed"] == 1
    assert body["updated"] == 0
    assert body["failed"] == 0
    assert body["skipped"] == 1


# ---------------------------------------------------------------------------
# 测试 4: LLM 返回非 JSON → failed 不 500
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backfill_llm_non_json_returns_failed(aclient, monkeypatch):
    """LLM 返回非 JSON 内容 → 该篇 failed=1，不触发 HTTP 500。"""
    c, factory = aclient
    pid = await _create_project(factory, "Non-JSON LLM Test")

    md_content = "# Paper About Something\n\nNo abstract here."
    await _mk_paper_with_markdown(
        factory, pid,
        markdown_content=md_content,
        abstract=None,
        creators=[],
    )

    # LLM 返回非 JSON（纯文本）
    fake_llm = FakeLLM("对不起，我无法提取结构化元数据。这是一个错误响应。")
    monkeypatch.setattr("app.main.get_llm_client", lambda *a, **k: fake_llm)

    r = await c.post(f"/projects/{pid}/papers/backfill-metadata", json={"limit": 10, "onlyMissing": True})
    assert r.status_code == 200, f"应返回 200，实际 {r.status_code}: {r.text}"
    body = r.json()
    assert body["processed"] == 1
    assert body["failed"] == 1
    assert body["updated"] == 0


# ---------------------------------------------------------------------------
# 测试 5: project 不存在 → 404 PROJECT_NOT_FOUND
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backfill_project_not_found(aclient):
    """project 不存在时返回 404 PROJECT_NOT_FOUND。"""
    c, _ = aclient

    r = await c.post("/projects/99999/papers/backfill-metadata", json={"limit": 10})
    assert r.status_code == 404
    assert r.json()["code"] == "PROJECT_NOT_FOUND"


# ---------------------------------------------------------------------------
# 测试 6: LLM 返回 JSON 但 abstract 已有（onlyMissing=False 时不覆盖已有 abstract）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backfill_does_not_overwrite_existing_abstract(aclient, monkeypatch):
    """即使 onlyMissing=False，已有 abstract 的篇不被 LLM 覆盖（仅回填空字段）。"""
    c, factory = aclient
    pid = await _create_project(factory, "No Overwrite Test")

    original_abstract = "The original abstract that should not be overwritten."
    md_content = "# My Paper\n\nContent."
    paper_id, _ = await _mk_paper_with_markdown(
        factory, pid,
        markdown_content=md_content,
        abstract=original_abstract,
        creators=[],              # creators 为空，允许被填
    )

    # LLM 返回新的 abstract（但已有字段不应被覆盖）
    canned_json = json.dumps({
        "title": "My Paper",
        "authors": ["New Author"],
        "year": 2024,
        "abstract": "This is the LLM generated abstract.",
        "keywords": ["keyword1"],
    })
    fake_llm = FakeLLM(canned_json)
    monkeypatch.setattr("app.main.get_llm_client", lambda *a, **k: fake_llm)

    # onlyMissing=False：即使已有 abstract 也处理（但服务函数不覆盖已有字段）
    r = await c.post(f"/projects/{pid}/papers/backfill-metadata", json={"limit": 10, "onlyMissing": False})
    assert r.status_code == 200, r.text

    # 验证 abstract 未被覆盖，但 creators（空）被填入
    async with factory() as s:
        paper = (await s.execute(select(Paper).where(Paper.id == paper_id))).scalar_one()
        assert paper.abstract == original_abstract, "原有 abstract 不应被覆盖"
        assert paper.creators and len(paper.creators) > 0, "空 creators 应被 LLM 填入"


# ---------------------------------------------------------------------------
# 测试 7: limit 约束生效
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backfill_limit_respected(aclient, monkeypatch):
    """limit=1 时，即使有 3 篇缺元数据的论文，也只处理 1 篇。"""
    c, factory = aclient
    pid = await _create_project(factory, "Limit Test")

    # 创建 3 篇缺 abstract 的 Paper
    for i in range(3):
        md_content = f"# Paper {i}\n\nContent {i}."
        await _mk_paper_with_markdown(
            factory, pid,
            markdown_content=md_content,
            abstract=None,
            creators=[],
        )

    canned_json = json.dumps({
        "title": "Paper",
        "authors": ["Author"],
        "year": 2023,
        "abstract": "An abstract.",
        "keywords": [],
    })
    fake_llm = FakeLLM(canned_json)
    monkeypatch.setattr("app.main.get_llm_client", lambda *a, **k: fake_llm)

    r = await c.post(f"/projects/{pid}/papers/backfill-metadata", json={"limit": 1, "onlyMissing": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["processed"] == 1, f"limit=1 时只应处理 1 篇，实际 processed={body['processed']}"
    # D-fix: available 为处理后真实剩余数，3 篇中处理了 1 篇（updated），剩余 2 篇仍缺元数据
    assert body["available"] == 2, f"D-fix: limit=1 处理 1 篇后，available 应为剩余 2 篇，实际 {body['available']}"


# ---------------------------------------------------------------------------
# 测试 8 (A-fix): 前一篇 LLM 失败 rollback 后，后一篇仍能成功处理
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backfill_rollback_isolation_first_fails_second_succeeds(aclient, monkeypatch):
    """A-fix: 前一篇 LLM 返回非 JSON（记为 failed + rollback），后一篇 LLM 返回合法 JSON → 后一篇仍 updated=1。

    验证 re-fetch 设计（s.get 重新取新鲜对象）使 rollback 不 expire 后续篇对象，
    确保"前一篇失败 rollback → 后一篇仍能正常处理"的隔离语义。
    """
    c, factory = aclient
    pid = await _create_project(factory, "Rollback Isolation Test")

    # 创建 2 篇缺 abstract 的 Paper
    for i in range(2):
        md_content = f"# Paper {i}\n\nContent {i}."
        await _mk_paper_with_markdown(
            factory, pid,
            markdown_content=md_content,
            abstract=None,
            creators=[],
        )

    # 第一次调用返回非 JSON（触发 failed + rollback），第二次返回合法 JSON（触发 updated）
    call_count = {"n": 0}
    valid_json = json.dumps({
        "title": "Paper",
        "authors": ["Author"],
        "year": 2023,
        "abstract": "A valid abstract.",
        "keywords": [],
    })

    class AlternatingLLM:
        async def complete(self, messages, **kwargs) -> str:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return "这不是 JSON，会导致 failed + rollback"  # 第一篇 → failed
            return valid_json  # 第二篇 → updated

        async def stream(self, messages, **kwargs):
            yield "x"

    monkeypatch.setattr("app.main.get_llm_client", lambda *a, **k: AlternatingLLM())

    r = await c.post(f"/projects/{pid}/papers/backfill-metadata", json={"limit": 10, "onlyMissing": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["processed"] == 2, f"应处理 2 篇，实际 processed={body['processed']}"
    assert body["failed"] == 1, f"第一篇应 failed，实际 {body['failed']}"
    assert body["updated"] == 1, f"A-fix: rollback 后第二篇应成功 updated=1，实际 {body['updated']}"


# ---------------------------------------------------------------------------
# 测试 9 (F-05): 仅缺 year 的篇 → onlyMissing=True 时也被选中并回填 year/container_title
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backfill_only_missing_covers_year(aclient, monkeypatch):
    """F-05: abstract+creators 已有、仅缺 year → onlyMissing=True 也应被选中（此前永不入选）。

    同时验证 container_title 为空时由 LLM 返回的 journal 回填。
    """
    c, factory = aclient
    pid = await _create_project(factory, "Backfill Year Test")

    md_content = "# Paper With Year In Text\n\nPublished 2021.\n\nContent."
    paper_id, _ = await _mk_paper_with_markdown(
        factory, pid,
        markdown_content=md_content,
        abstract="Already have an abstract.",
        creators=[{"literal": "Existing Author"}],
        year=None,  # 仅缺 year
    )

    canned_json = json.dumps({
        "title": "Paper With Year In Text",
        "authors": ["Existing Author"],
        "year": 2021,
        "abstract": "LLM abstract that must not overwrite",
        "keywords": [],
        "journal": "Journal of Testing",
    })
    fake_llm = FakeLLM(canned_json)
    monkeypatch.setattr("app.main.get_llm_client", lambda *a, **k: fake_llm)

    r = await c.post(f"/projects/{pid}/papers/backfill-metadata", json={"limit": 10, "onlyMissing": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["processed"] == 1, f"F-05: 仅缺 year 的篇应被选中，实际 processed={body['processed']}"
    assert body["updated"] == 1
    assert body["failed"] == 0

    async with factory() as s:
        paper = (await s.execute(select(Paper).where(Paper.id == paper_id))).scalar_one()
        assert paper.year == 2021
        # 已有字段不被覆盖
        assert paper.abstract == "Already have an abstract."
        assert paper.creators == [{"literal": "Existing Author"}]
        # container_title 从 LLM journal 回填
        assert paper.container_title == "Journal of Testing"


# ---------------------------------------------------------------------------
# 测试 10 (F-05): 已有 container_title → 不被 LLM journal 覆盖
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backfill_does_not_overwrite_existing_container_title(aclient, monkeypatch):
    """F-05: container_title 已有值时保持原样（与 year 同样的仅填空军规）。"""
    c, factory = aclient
    pid = await _create_project(factory, "No Overwrite Journal Test")

    paper_id, _ = await _mk_paper_with_markdown(
        factory, pid,
        markdown_content="# P\n\nContent.",
        abstract=None,   # 缺 abstract 以保证被 onlyMissing 选中
        creators=[],
    )
    async with factory() as s:
        paper = (await s.execute(select(Paper).where(Paper.id == paper_id))).scalar_one()
        paper.container_title = "Original Journal"
        await s.commit()

    canned_json = json.dumps({
        "title": "P",
        "authors": ["A"],
        "year": 2022,
        "abstract": "Filled abstract.",
        "keywords": [],
        "journal": "LLM Journal",
    })
    fake_llm = FakeLLM(canned_json)
    monkeypatch.setattr("app.main.get_llm_client", lambda *a, **k: fake_llm)

    r = await c.post(f"/projects/{pid}/papers/backfill-metadata", json={"limit": 10, "onlyMissing": True})
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 1

    async with factory() as s:
        paper = (await s.execute(select(Paper).where(Paper.id == paper_id))).scalar_one()
        assert paper.container_title == "Original Journal", "已有 container_title 不应被覆盖"
        assert paper.abstract == "Filled abstract."


# ---------------------------------------------------------------------------
# _parse_llm_json 单元测试（I-1 fix：花括号配对计数防贪婪过度捕获）
# ---------------------------------------------------------------------------

from app.services.metadata_backfill import _parse_llm_json  # noqa: E402


def test_parse_llm_json_pure_json():
    """① 纯 JSON 字符串直接解析成功。"""
    raw = '{"abstract": "hello", "authors": ["A", "B"], "year": 2023}'
    result = _parse_llm_json(raw)
    assert result is not None
    assert result["abstract"] == "hello"
    assert result["year"] == 2023


def test_parse_llm_json_fence_wrapped():
    """② ```json fence 包裹的 JSON 正确提取。"""
    raw = '```json\n{"abstract": "wrapped", "year": 2022}\n```'
    result = _parse_llm_json(raw)
    assert result is not None
    assert result["abstract"] == "wrapped"
    assert result["year"] == 2022


def test_parse_llm_json_trailing_text():
    """③ JSON 后跟说明文字——旧贪婪正则会过度捕获导致失败，新实现应正确截取第一个完整对象。"""
    raw = '{"abstract": "first"} 这是额外说明。{"other": "second"}'
    result = _parse_llm_json(raw)
    assert result is not None
    # 应取第一个 JSON 对象
    assert result["abstract"] == "first"
    assert "other" not in result


def test_parse_llm_json_prefix_text():
    """④ 前缀说明 + JSON——LLM 常见输出格式。"""
    raw = "以下是提取结果：\n\n{\"abstract\": \"with prefix\", \"year\": 2021}"
    result = _parse_llm_json(raw)
    assert result is not None
    assert result["abstract"] == "with prefix"
    assert result["year"] == 2021


def test_parse_llm_json_non_json_returns_none():
    """⑤ 非 JSON 纯文字 → 返回 None，不抛出异常。"""
    raw = "对不起，我无法提取结构化元数据。这是一个错误响应。"
    result = _parse_llm_json(raw)
    assert result is None


def test_parse_llm_json_empty_returns_none():
    """空字符串 → 返回 None。"""
    assert _parse_llm_json("") is None
    assert _parse_llm_json(None) is None  # type: ignore[arg-type]


def test_parse_llm_json_nested_braces():
    """含嵌套花括号的 JSON 能正确解析（配对计数不被嵌套迷惑）。"""
    raw = '{"meta": {"author": "X"}, "year": 2020} trailing garbage'
    result = _parse_llm_json(raw)
    assert result is not None
    assert result["year"] == 2020
    assert result["meta"]["author"] == "X"


def test_parse_llm_json_first_block_invalid_second_valid():
    """C-fix: 第一个花括号块非法 JSON，第二个合法——应继续扫描并返回第二个块的结果。

    LLM 常见格式："说明文字 {非JSON占位符或破损JSON} ... {真正的结构化JSON}"。
    旧实现遇到第一个块解析失败直接 return None，C-fix 后应继续找下一块。
    """
    # 第一个块是非法 JSON（没有引号），第二个块是合法 JSON
    raw = "Here is the result: {invalid json no quotes} and the real: {\"abstract\": \"correct\", \"year\": 2024}"
    result = _parse_llm_json(raw)
    assert result is not None, "第一块非法时应继续扫描第二块"
    assert result["abstract"] == "correct"
    assert result["year"] == 2024


def test_parse_llm_json_multiple_invalid_then_valid():
    """C-fix: 多个非法块后跟一个合法块——最终应返回合法块的解析结果。"""
    raw = "{bad1} {bad2 also} {\"key\": \"value\", \"num\": 42}"
    result = _parse_llm_json(raw)
    assert result is not None, "耗尽非法块后应找到合法块"
    assert result["key"] == "value"
    assert result["num"] == 42


def test_parse_llm_json_all_blocks_invalid_returns_none():
    """C-fix: 所有花括号块均非法时，应返回 None，不抛异常。"""
    raw = "{bad1} {bad2} {bad3 no closing quote}"
    result = _parse_llm_json(raw)
    assert result is None, "所有块均非法时应返回 None"
