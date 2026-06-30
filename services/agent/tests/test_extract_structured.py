"""P3-T3: POST /projects/{pid}/papers/extract-structured 结构化抽取端点测试。

覆盖：
  1. 1 篇有 markdown → 调端点 → paper_extraction 行 upsert（字段写入）、extracted=1
  2. 重复调用（reextract=false）→ 已有 extraction 的篇被 SQL 排除，processed=0 skipped=0
  3. reextract=true → 覆盖更新（upsert 不重复建行，断言行数仍 1）
  4. 非 JSON → failed 不 500
  5. project 404
  6. get_paper_detail 返回 extraction
  7. [I-1] 3 篇 OCR-done，1 篇已抽取，limit=5 reextract=false → only 2 篇被处理，available 正确
  8. [I-1] reextract=true → 3 篇全部处理，available=3
  9. [I-1] available 不受 limit 截断
"""
from __future__ import annotations

import json
import tempfile
import textwrap
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, func

from app.db import get_session
from app.main import app, get_r_client
from app.models import Attachment, Paper, ProjectPaper, PaperExtraction
from app.repositories.project import create_project
from app.repositories.extraction import upsert_extraction


# ---------------------------------------------------------------------------
# FakeLLM fixture
# ---------------------------------------------------------------------------

class FakeLLM:
    """可配置返回内容的离线 LLM（for extract-structured 测试）。"""

    def __init__(self, canned: str):
        self.canned = canned
        self.model = "fake-model"

    async def complete(self, messages, **kwargs) -> str:
        return self.canned

    async def stream(self, messages, **kwargs):
        yield self.canned


# ---------------------------------------------------------------------------
# aclient fixture
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


async def _create_project(factory, name: str = "Extract Test") -> int:
    async with factory() as s:
        proj = await create_project(s, {"name": name})
        return proj.id


_COUNTER = {"n": 0}


def _unique_dedup_key(prefix: str = "extract-test") -> str:
    _COUNTER["n"] += 1
    return f"title:{prefix}-{_COUNTER['n']}"


async def _mk_paper_with_markdown(
    factory,
    pid: int,
    *,
    markdown_content: str,
) -> tuple[int, Path]:
    """在测试 DB 中创建 Paper + Attachment(markdown_path=done)，并写 markdown 文件。

    Returns:
        (paper_id, markdown_path)
    """
    md_dir = Path(tempfile.mkdtemp())
    md_path = md_dir / "test.md"
    md_path.write_text(markdown_content, encoding="utf-8")

    async with factory() as s:
        paper = Paper(
            title="Extraction Test Paper",
            abstract=None,
            creators=[],
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


_STRUCTURED_JSON = json.dumps({
    "research_question": "How do deep learning models improve NLP performance?",
    "method": "Systematic literature review with meta-analysis of 50 studies.",
    "findings": "Deep learning outperforms traditional ML by 15% on BLEU score.",
    "dataset": "CommonCrawl, Wikipedia corpus (2.5B tokens)",
    "contribution": "First systematic quantitative comparison of DL vs ML for NLP.",
})


# ---------------------------------------------------------------------------
# 测试 1: 1 篇有 markdown → upsert paper_extraction 行，extracted=1
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_creates_extraction_row(aclient, monkeypatch):
    """1 篇有 markdown → 调端点 → paper_extraction 行创建，字段写入正确，extracted=1。"""
    c, factory = aclient
    pid = await _create_project(factory, "Extract Creates Test")

    md_content = textwrap.dedent("""\
        # Deep Learning for NLP: A Survey

        ## Research Question
        How do deep learning models improve NLP performance compared to traditional ML?

        ## Methodology
        Systematic literature review with meta-analysis.

        ## Results
        Deep learning outperforms traditional ML by 15%.

        ## Dataset
        CommonCrawl and Wikipedia corpus.
    """)
    paper_id, _ = await _mk_paper_with_markdown(factory, pid, markdown_content=md_content)

    fake_llm = FakeLLM(_STRUCTURED_JSON)
    monkeypatch.setattr("app.main.get_llm_client", lambda *a, **k: fake_llm)

    r = await c.post(f"/projects/{pid}/papers/extract-structured", json={"limit": 10})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["processed"] == 1
    assert body["extracted"] == 1
    assert body["skipped"] == 0
    assert body["failed"] == 0

    # 验证 DB 有 paper_extraction 行且字段正确
    async with factory() as s:
        ext = (
            await s.execute(
                select(PaperExtraction).where(PaperExtraction.paper_id == paper_id)
            )
        ).scalar_one_or_none()
        assert ext is not None, "paper_extraction 行应存在"
        assert "deep learning" in (ext.research_question or "").lower()
        assert ext.method is not None
        assert ext.findings is not None
        assert ext.dataset is not None
        assert ext.contribution is not None


# ---------------------------------------------------------------------------
# 测试 2: 重复调用（reextract=false）→ 已有 extraction 的篇 skipped
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_skips_existing_when_not_reextract(aclient, monkeypatch):
    """第一次调用成功后，第二次 reextract=false 时，已有 extraction 的篇被 SQL 排除（processed=0, skipped=0）。"""
    c, factory = aclient
    pid = await _create_project(factory, "Extract Skip Test")

    md_content = "# Paper\n\nContent about research."
    paper_id, _ = await _mk_paper_with_markdown(factory, pid, markdown_content=md_content)

    fake_llm = FakeLLM(_STRUCTURED_JSON)
    monkeypatch.setattr("app.main.get_llm_client", lambda *a, **k: fake_llm)

    # 第一次调用
    r1 = await c.post(f"/projects/{pid}/papers/extract-structured", json={"limit": 10, "reextract": False})
    assert r1.status_code == 200
    assert r1.json()["extracted"] == 1

    # 第二次调用（reextract=false）—— SQL 层已排除已抽取的篇，processed=0
    r2 = await c.post(f"/projects/{pid}/papers/extract-structured", json={"limit": 10, "reextract": False})
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["processed"] == 0, \
        f"第二次 reextract=false 时 SQL 排除已有 extraction，processed 应为 0，实际 {body2['processed']}"
    assert body2["skipped"] == 0, \
        f"SQL 已排除，skipped 也应为 0，实际 {body2['skipped']}"
    assert body2["extracted"] == 0
    assert body2["available"] == 0, \
        f"所有篇都已抽取，available 应为 0，实际 {body2['available']}"


# ---------------------------------------------------------------------------
# 测试 3: reextract=true → 覆盖更新（行数仍 1，断言更新了内容）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_reextract_updates_row(aclient, monkeypatch):
    """reextract=true → 已有 extraction 被覆盖更新，paper_extraction 行数仍为 1（不重复建行）。"""
    c, factory = aclient
    pid = await _create_project(factory, "Reextract Test")

    md_content = "# Paper\n\nResearch on distributed systems."
    paper_id, _ = await _mk_paper_with_markdown(factory, pid, markdown_content=md_content)

    # 第一次抽取
    first_json = json.dumps({
        "research_question": "Original question about distributed systems",
        "method": "Original method",
        "findings": "Original findings",
        "dataset": "Original dataset",
        "contribution": "Original contribution",
    })
    monkeypatch.setattr("app.main.get_llm_client", lambda *a, **k: FakeLLM(first_json))
    r1 = await c.post(f"/projects/{pid}/papers/extract-structured", json={"limit": 10})
    assert r1.status_code == 200
    assert r1.json()["extracted"] == 1

    # 第二次抽取（reextract=true，不同 LLM 返回）
    second_json = json.dumps({
        "research_question": "Updated question about cloud computing",
        "method": "Updated method with different approach",
        "findings": "Updated findings show 30% improvement",
        "dataset": "New updated dataset",
        "contribution": "Updated contribution to the field",
    })
    monkeypatch.setattr("app.main.get_llm_client", lambda *a, **k: FakeLLM(second_json))
    r2 = await c.post(f"/projects/{pid}/papers/extract-structured", json={"limit": 10, "reextract": True})
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["extracted"] == 1, f"reextract=true 时应重新抽取，实际 extracted={body2['extracted']}"

    # 验证只有 1 行，且内容被更新
    async with factory() as s:
        count_q = select(func.count()).where(PaperExtraction.paper_id == paper_id)
        row_count = (await s.execute(count_q)).scalar_one()
        assert row_count == 1, f"upsert 后应只有 1 行，实际 {row_count}"

        ext = (
            await s.execute(
                select(PaperExtraction).where(PaperExtraction.paper_id == paper_id)
            )
        ).scalar_one()
        assert "cloud computing" in (ext.research_question or "").lower(), \
            f"research_question 应被更新，实际 {ext.research_question!r}"


# ---------------------------------------------------------------------------
# 测试 4: 非 JSON 回包 → failed 不 500
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_non_json_llm_response(aclient, monkeypatch):
    """LLM 返回非 JSON 内容 → 该篇 failed=1，不触发 HTTP 500。"""
    c, factory = aclient
    pid = await _create_project(factory, "Non-JSON Test")

    md_content = "# Paper\n\nContent about something."
    await _mk_paper_with_markdown(factory, pid, markdown_content=md_content)

    fake_llm = FakeLLM("对不起，我无法处理这个请求。这是纯文本错误响应，不是 JSON。")
    monkeypatch.setattr("app.main.get_llm_client", lambda *a, **k: fake_llm)

    r = await c.post(f"/projects/{pid}/papers/extract-structured", json={"limit": 10})
    assert r.status_code == 200, f"应返回 200，实际 {r.status_code}: {r.text}"
    body = r.json()
    assert body["processed"] == 1
    assert body["failed"] == 1
    assert body["extracted"] == 0


# ---------------------------------------------------------------------------
# 测试 5: project 不存在 → 404
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_project_not_found(aclient):
    """project 不存在时返回 404 PROJECT_NOT_FOUND。"""
    c, _ = aclient

    r = await c.post("/projects/99999/papers/extract-structured", json={"limit": 10})
    assert r.status_code == 404
    assert r.json()["code"] == "PROJECT_NOT_FOUND"


# ---------------------------------------------------------------------------
# 测试 6: get_paper_detail 返回 extraction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_paper_detail_includes_extraction(aclient, monkeypatch):
    """paper_extraction 存在时，GET /projects/{pid}/papers/{pid} 的 extraction 字段非 null。"""
    c, factory = aclient
    pid = await _create_project(factory, "Detail With Extraction Test")

    md_content = "# A paper\n\nSome content about research methodology."
    paper_id, _ = await _mk_paper_with_markdown(factory, pid, markdown_content=md_content)

    fake_llm = FakeLLM(_STRUCTURED_JSON)
    monkeypatch.setattr("app.main.get_llm_client", lambda *a, **k: fake_llm)

    # 先抽取
    r_extract = await c.post(f"/projects/{pid}/papers/extract-structured", json={"limit": 10})
    assert r_extract.status_code == 200
    assert r_extract.json()["extracted"] == 1

    # 再查 paper detail
    r_detail = await c.get(f"/projects/{pid}/papers/{paper_id}")
    assert r_detail.status_code == 200, r_detail.text
    detail = r_detail.json()

    assert "extraction" in detail, "PaperDetail 应有 extraction 字段"
    ext = detail["extraction"]
    assert ext is not None, "已有 paper_extraction 时 extraction 应非 null"
    assert "researchQuestion" in ext
    assert ext["researchQuestion"] is not None
    assert ext["method"] is not None
    assert ext["findings"] is not None


@pytest.mark.asyncio
async def test_paper_detail_extraction_null_when_no_extraction(aclient, monkeypatch):
    """无 paper_extraction 记录时，PaperDetail.extraction 为 null。"""
    c, factory = aclient
    pid = await _create_project(factory, "Detail No Extraction Test")

    md_content = "# A paper\n\nContent."
    paper_id, _ = await _mk_paper_with_markdown(factory, pid, markdown_content=md_content)

    r_detail = await c.get(f"/projects/{pid}/papers/{paper_id}")
    assert r_detail.status_code == 200, r_detail.text
    detail = r_detail.json()

    assert "extraction" in detail
    assert detail["extraction"] is None, "无 paper_extraction 时 extraction 应为 null"


# ---------------------------------------------------------------------------
# 测试 7 (I-1): 3 篇 OCR-done，1 篇已抽取，limit=5 reextract=false → 只处理另外 2 篇
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_i1_only_unextracted_papers_processed(aclient, monkeypatch):
    """[I-1] 3 篇 OCR-done，1 篇已有 extraction，limit=5 reextract=false → 只处理 2 篇，available=2。"""
    c, factory = aclient
    pid = await _create_project(factory, "I1 SQL Filter Test")

    fake_llm = FakeLLM(_STRUCTURED_JSON)
    monkeypatch.setattr("app.main.get_llm_client", lambda *a, **k: fake_llm)

    # 创建 3 篇 OCR-done 论文
    paper_ids = []
    for i in range(3):
        paper_id, _ = await _mk_paper_with_markdown(
            factory, pid,
            markdown_content=f"# Paper {i}\n\nContent {i}.",
        )
        paper_ids.append(paper_id)

    # 手动给第 1 篇预先插入 extraction（模拟已抽取状态）
    async with factory() as s:
        await upsert_extraction(s, paper_ids[0], {
            "research_question": "Pre-existing question",
            "method": "Pre-existing method",
            "findings": "Pre-existing findings",
            "dataset": "Pre-existing dataset",
            "contribution": "Pre-existing contribution",
        }, model="pre-inserted")
        await s.commit()

    # 调用端点：limit=5, reextract=false
    r = await c.post(f"/projects/{pid}/papers/extract-structured", json={"limit": 5, "reextract": False})
    assert r.status_code == 200, r.text
    body = r.json()

    # 只有 2 篇尚无 extraction，应只处理这 2 篇
    assert body["processed"] == 2, f"应处理 2 篇尚无 extraction 的论文，实际 processed={body['processed']}"
    assert body["extracted"] == 2, f"应抽取 2 篇，实际 extracted={body['extracted']}"
    assert body["skipped"] == 0
    assert body["failed"] == 0
    # D-fix: available 为处理后真实剩余数，2 篇均成功抽取，剩余 0 篇尚无 extraction
    assert body["available"] == 0, f"D-fix: 2 篇抽取后 available 应为 0，实际 {body['available']}"

    # 验证已有 extraction 的篇内容未被覆盖
    async with factory() as s:
        ext0 = (await s.execute(
            select(PaperExtraction).where(PaperExtraction.paper_id == paper_ids[0])
        )).scalar_one()
        assert "Pre-existing" in (ext0.research_question or ""), \
            "预先存在的 extraction 不应被 reextract=false 覆盖"


# ---------------------------------------------------------------------------
# 测试 8 (I-1): reextract=true → 3 篇全部处理，available=3
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_i1_reextract_true_processes_all(aclient, monkeypatch):
    """[I-1] reextract=true → 3 篇全部处理（包含已有 extraction 的篇），available=3。"""
    c, factory = aclient
    pid = await _create_project(factory, "I1 Reextract All Test")

    fake_llm = FakeLLM(_STRUCTURED_JSON)
    monkeypatch.setattr("app.main.get_llm_client", lambda *a, **k: fake_llm)

    # 创建 3 篇 OCR-done 论文
    paper_ids = []
    for i in range(3):
        paper_id, _ = await _mk_paper_with_markdown(
            factory, pid,
            markdown_content=f"# Paper {i}\n\nContent {i}.",
        )
        paper_ids.append(paper_id)

    # 预先给 2 篇插入 extraction
    async with factory() as s:
        for pid_pre in paper_ids[:2]:
            await upsert_extraction(s, pid_pre, {
                "research_question": "Old question",
                "method": "Old method",
                "findings": "Old findings",
                "dataset": "Old dataset",
                "contribution": "Old contribution",
            }, model="old-model")
        await s.commit()

    # reextract=true → 3 篇全部处理
    r = await c.post(f"/projects/{pid}/papers/extract-structured", json={"limit": 10, "reextract": True})
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["processed"] == 3, f"reextract=true 时应处理全部 3 篇，实际 processed={body['processed']}"
    assert body["extracted"] == 3, f"应抽取 3 篇，实际 extracted={body['extracted']}"
    assert body["available"] == 3, f"reextract=true 时 available 应为全部 3 篇，实际 {body['available']}"


# ---------------------------------------------------------------------------
# 测试 9 (I-1): available 不受 limit 截断
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_i1_available_not_limited_by_limit(aclient, monkeypatch):
    """[I-1] available 反映总待处理数，不受 limit 截断——5 篇待抽取，limit=2，available=5，processed=2。"""
    c, factory = aclient
    pid = await _create_project(factory, "I1 Available Count Test")

    fake_llm = FakeLLM(_STRUCTURED_JSON)
    monkeypatch.setattr("app.main.get_llm_client", lambda *a, **k: fake_llm)

    # 创建 5 篇 OCR-done 论文，均无 extraction
    for i in range(5):
        await _mk_paper_with_markdown(
            factory, pid,
            markdown_content=f"# Paper {i}\n\nContent {i}.",
        )

    # limit=2，只处理 2 篇，但 available 应为 5（处理前的待抽取总数）
    r = await c.post(f"/projects/{pid}/papers/extract-structured", json={"limit": 2, "reextract": False})
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["processed"] == 2, f"limit=2 时只应处理 2 篇，实际 processed={body['processed']}"
    assert body["extracted"] == 2
    # D-fix: available 为处理后真实剩余数，5 篇中处理了 2 篇，剩余 3 篇仍无 extraction
    assert body["available"] == 3, \
        f"D-fix: limit=2 处理 2 篇后 available 应为剩余 3 篇，实际 {body['available']}"


# ---------------------------------------------------------------------------
# 测试 10 (A-fix): 前一篇 LLM 返回非 JSON 触发 failed+rollback，后一篇仍成功抽取
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_rollback_isolation_first_fails_second_succeeds(aclient, monkeypatch):
    """A-fix: 前一篇 LLM 返回非 JSON（failed + rollback），后一篇返回合法结构化 JSON → 后一篇仍 extracted=1。

    验证 re-fetch 设计（s.get 重新取新鲜对象）使 rollback 不 expire 后续篇对象，
    确保"前一篇失败 rollback → 后一篇仍能正常处理"的隔离语义。
    """
    c, factory = aclient
    pid = await _create_project(factory, "Extract Rollback Isolation Test")

    # 创建 2 篇 OCR-done 论文，均无 extraction
    for i in range(2):
        md_content = f"# Paper {i}\n\nContent {i}."
        await _mk_paper_with_markdown(factory, pid, markdown_content=md_content)

    # 第一次调用返回非 JSON（触发 failed + rollback），第二次返回合法结构化 JSON（触发 extracted）
    call_count = {"n": 0}

    class AlternatingLLM:
        model = "fake-alternating"

        async def complete(self, messages, **kwargs) -> str:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return "这不是 JSON，会导致 failed + rollback"  # 第一篇 → failed
            return _STRUCTURED_JSON  # 第二篇 → extracted

        async def stream(self, messages, **kwargs):
            yield "x"

    monkeypatch.setattr("app.main.get_llm_client", lambda *a, **k: AlternatingLLM())

    r = await c.post(f"/projects/{pid}/papers/extract-structured", json={"limit": 10})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["processed"] == 2, f"应处理 2 篇，实际 processed={body['processed']}"
    assert body["failed"] == 1, f"第一篇应 failed，实际 {body['failed']}"
    assert body["extracted"] == 1, \
        f"A-fix: rollback 后第二篇应成功 extracted=1，实际 {body['extracted']}"
