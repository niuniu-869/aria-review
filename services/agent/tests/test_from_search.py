"""P2-T3: POST /projects/{pid}/papers/from-search 入库+纳排端点测试。

覆盖：
  1. POST 2 候选(defaultStatus=candidate) → 建 2 Paper + 关联项目, imported=2, skipped=0
  2. 重复 POST 相同候选 → skipped=2, imported=0 (幂等)
  3. defaultStatus=included → inclusion_status 为 included
  4. project 不存在 → 404 PROJECT_NOT_FOUND
  5. containerTitle 写入 DB（paper.container_title 与候选一致）
  6. candidates 超过 100 条 → 422（FastAPI 校验）
  7. 字段校验：doi/source/containerTitle/url/abstract/openalexId/year 超长/越界 → 422
  8. 单条候选处理失败（模拟 add_paper 抛错）→ 不影响其他、failed+1（逐候选隔离）
  9. B: 已关联候选再次 included 入库 → inclusion_status 升级为 included
 10. 事务隔离：某条候选触发 IntegrityError → session rollback 后续候选仍成功入库
"""
from __future__ import annotations

import pytest
import pytest_asyncio
import httpx
from sqlalchemy.exc import IntegrityError

from app.db import get_session
from app.main import app, get_r_client
from app.repositories.project import create_project


# ---------------------------------------------------------------------------
# AsyncClient fixture（复用 conftest session_factory + fake_r）
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


async def _create_project(factory, name: str = "FromSearch Test") -> int:
    async with factory() as s:
        proj = await create_project(s, {"name": name})
        return proj.id


# ---------------------------------------------------------------------------
# 测试 1: 2 候选 → imported=2, skipped=0
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_from_search_basic_import(aclient):
    """POST 2 个新候选 → imported=2, skipped=0, paperIds 有 2 个。"""
    c, factory = aclient
    pid = await _create_project(factory)

    payload = {
        "candidates": [
            {
                "title": "Analyst Forecast Accuracy",
                "doi": "10.1/analyst1",
                "authors": ["Zhang Wei", "Li Ming"],
                "year": 2022,
                "abstract": "This paper studies analyst forecast accuracy.",
                "containerTitle": "Journal of Finance",
                "openalexId": "W111111",
                "source": "openalex",
            },
            {
                "title": "IPO Underpricing in Emerging Markets",
                "doi": "10.1/ipo2",
                "authors": ["Wang Fang"],
                "year": 2021,
                "abstract": "We examine IPO underpricing.",
                "containerTitle": "Review of Financial Studies",
                "openalexId": "W222222",
                "source": "openalex",
            },
        ],
        "defaultStatus": "candidate",
    }

    r = await c.post(f"/projects/{pid}/papers/from-search", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["imported"] == 2
    assert body["skipped"] == 0
    assert len(body["paperIds"]) == 2
    assert all(isinstance(i, int) for i in body["paperIds"])

    # 验证 DB 中确实有 2 篇关联
    async with factory() as s:
        from app.repositories.project import list_project_papers
        pairs = await list_project_papers(s, pid)
    assert len(pairs) == 2


# ---------------------------------------------------------------------------
# 测试 2: 幂等 — 重复 POST 相同候选 → skipped=2, imported=0
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_from_search_idempotent(aclient):
    """同一候选 POST 两次：第二次 imported=0, skipped=2。"""
    c, factory = aclient
    pid = await _create_project(factory, "Idempotent Test")

    payload = {
        "candidates": [
            {
                "title": "Analyst Forecast Accuracy",
                "doi": "10.1/idem1",
                "year": 2022,
            },
            {
                "title": "IPO Underpricing",
                "doi": "10.1/idem2",
                "year": 2021,
            },
        ],
        "defaultStatus": "candidate",
    }

    # 第一次
    r1 = await c.post(f"/projects/{pid}/papers/from-search", json=payload)
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["imported"] == 2
    assert body1["skipped"] == 0

    # 第二次（完全相同请求）
    r2 = await c.post(f"/projects/{pid}/papers/from-search", json=payload)
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["imported"] == 0
    assert body2["skipped"] == 2
    # paperIds 应与第一次相同
    assert set(body1["paperIds"]) == set(body2["paperIds"])


# ---------------------------------------------------------------------------
# 测试 3: defaultStatus=included → inclusion_status 为 included
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_from_search_included_status(aclient):
    """defaultStatus=included 时，关联的 ProjectPaper.inclusion_status 为 included。"""
    c, factory = aclient
    pid = await _create_project(factory, "Included Status Test")

    payload = {
        "candidates": [
            {
                "title": "Corporate Governance and Firm Value",
                "doi": "10.1/gov3",
                "year": 2023,
            },
        ],
        "defaultStatus": "included",
    }

    r = await c.post(f"/projects/{pid}/papers/from-search", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["imported"] == 1
    assert len(body["paperIds"]) == 1

    # 验证 DB 中 inclusion_status = included
    async with factory() as s:
        from sqlalchemy import select
        from app.models import ProjectPaper
        paper_id = body["paperIds"][0]
        q = select(ProjectPaper).where(
            ProjectPaper.project_id == pid,
            ProjectPaper.paper_id == paper_id,
        )
        pp = (await s.execute(q)).scalar_one_or_none()
        assert pp is not None
        assert pp.inclusion_status == "included"


# ---------------------------------------------------------------------------
# 测试 4: project 不存在 → 404 PROJECT_NOT_FOUND
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_from_search_project_not_found(aclient):
    """project 不存在时返回 404 PROJECT_NOT_FOUND。"""
    c, _ = aclient

    payload = {
        "candidates": [
            {"title": "Some Paper", "doi": "10.1/notfound"},
        ],
        "defaultStatus": "candidate",
    }

    r = await c.post("/projects/99999/papers/from-search", json=payload)
    assert r.status_code == 404
    assert r.json()["code"] == "PROJECT_NOT_FOUND"


# ---------------------------------------------------------------------------
# 测试 5: containerTitle 写入 DB
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_from_search_container_title_persisted(aclient):
    """候选的 containerTitle 应写入 paper.container_title 列。"""
    c, factory = aclient
    pid = await _create_project(factory, "ContainerTitle Test")

    payload = {
        "candidates": [
            {
                "title": "Machine Learning in Finance",
                "doi": "10.1/mlf5",
                "year": 2024,
                "containerTitle": "Journal of Financial Economics",
                "source": "openalex",
            },
        ],
        "defaultStatus": "candidate",
    }

    r = await c.post(f"/projects/{pid}/papers/from-search", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["imported"] == 1
    paper_id = body["paperIds"][0]

    # 直接查 DB 验证 container_title 列
    async with factory() as s:
        from sqlalchemy import select
        from app.models import Paper
        paper = (await s.execute(select(Paper).where(Paper.id == paper_id))).scalar_one()
        assert paper.container_title == "Journal of Financial Economics"


@pytest.mark.asyncio
async def test_from_search_persists_sciverse_external_ids(aclient):
    """Sciverse 候选入库后应保存 doc_id/unique_id，避免外部来源追溯丢失。"""
    c, factory = aclient
    pid = await _create_project(factory, "Sciverse ExternalId Test")

    payload = {
        "candidates": [
            {
                "title": "Sciverse Paper",
                "doi": "10.1/sciverse",
                "year": 2025,
                "source": "sciverse",
                "provider": "sciverse",
                "sciverseDocId": "doc-123",
                "sciverseUniqueId": "uid-456",
                "externalIds": [
                    {
                        "provider": "sciverse",
                        "id_type": "doc_id",
                        "external_id": "doc-123",
                    }
                ],
                "raw": {"doc_id": "doc-123", "unique_id": "uid-456"},
            },
        ],
        "defaultStatus": "candidate",
    }

    r = await c.post(f"/projects/{pid}/papers/from-search", json=payload)
    assert r.status_code == 200, r.text
    paper_id = r.json()["paperIds"][0]

    async with factory() as s:
        from sqlalchemy import select
        from app.models import PaperExternalId

        rows = list((await s.execute(
            select(PaperExternalId).where(PaperExternalId.paper_id == paper_id)
        )).scalars().all())

    pairs = {(row.provider, row.id_type, row.external_id) for row in rows}
    assert ("sciverse", "doc_id", "doc-123") in pairs
    assert ("sciverse", "unique_id", "uid-456") in pairs


@pytest.mark.asyncio
async def test_from_search_candidates_over_100_are_imported(aclient):
    """用户筛选需要大批候选：超过 100 条时不再拒绝，导入链路保持可用。"""
    c, factory = aclient
    pid = await _create_project(factory, "Large Candidate Test")

    candidates = [
        {"title": f"Paper {i}", "doi": f"10.1/maxlen{i}"}
        for i in range(101)
    ]
    payload = {"candidates": candidates, "defaultStatus": "candidate"}

    r = await c.post(f"/projects/{pid}/papers/from-search", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["imported"] == 101
    assert body["skipped"] == 0
    assert len(body["paperIds"]) == 101


@pytest.mark.asyncio
async def test_from_search_candidates_over_500_rejected(aclient):
    """candidates 上限 500（与 search clamp 一致、防超大请求体 DoS）：超过 500 应 422。"""
    c, factory = aclient
    pid = await _create_project(factory, "Over500 Test")

    candidates = [
        {"title": f"Paper {i}", "doi": f"10.1/over500-{i}"}
        for i in range(501)
    ]
    payload = {"candidates": candidates, "defaultStatus": "candidate"}

    r = await c.post(f"/projects/{pid}/papers/from-search", json=payload)
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# 测试 7: 字段级校验——超长/越界 → 422
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_from_search_field_validation_doi_too_long(aclient):
    """doi 超过 255 字符 → 422。"""
    c, factory = aclient
    pid = await _create_project(factory, "FieldValidation Test")

    payload = {
        "candidates": [{"title": "Test Paper", "doi": "x" * 256}],
        "defaultStatus": "candidate",
    }
    r = await c.post(f"/projects/{pid}/papers/from-search", json=payload)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_from_search_field_validation_source_too_long(aclient):
    """source 超过 40 字符 → 422。"""
    c, factory = aclient
    pid = await _create_project(factory, "FieldValidation Source Test")

    payload = {
        "candidates": [{"title": "Test Paper", "source": "x" * 41}],
        "defaultStatus": "candidate",
    }
    r = await c.post(f"/projects/{pid}/papers/from-search", json=payload)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_from_search_field_validation_year_out_of_range(aclient):
    """year 超出 1500-2100 范围 → 422。"""
    c, factory = aclient
    pid = await _create_project(factory, "FieldValidation Year Test")

    # year 太小
    r = await c.post(f"/projects/{pid}/papers/from-search", json={
        "candidates": [{"title": "Old Paper", "year": 1499}],
        "defaultStatus": "candidate",
    })
    assert r.status_code == 422

    # year 太大
    r2 = await c.post(f"/projects/{pid}/papers/from-search", json={
        "candidates": [{"title": "Future Paper", "year": 2101}],
        "defaultStatus": "candidate",
    })
    assert r2.status_code == 422


@pytest.mark.asyncio
async def test_from_search_field_validation_abstract_too_long(aclient):
    """abstract 超过 20000 字符 → 422。"""
    c, factory = aclient
    pid = await _create_project(factory, "FieldValidation Abstract Test")

    payload = {
        "candidates": [{"title": "Test Paper", "abstract": "x" * 20001}],
        "defaultStatus": "candidate",
    }
    r = await c.post(f"/projects/{pid}/papers/from-search", json=payload)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_from_search_field_validation_authors_max_items(aclient):
    """authors 超过 100 条 → 422。"""
    c, factory = aclient
    pid = await _create_project(factory, "FieldValidation Authors Test")

    payload = {
        "candidates": [{"title": "Test Paper", "authors": [f"Author {i}" for i in range(101)]}],
        "defaultStatus": "candidate",
    }
    r = await c.post(f"/projects/{pid}/papers/from-search", json=payload)
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# 测试 8: 逐候选隔离——单条抛错不影响其他，failed+1
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_from_search_per_candidate_isolation(aclient, monkeypatch):
    """单条候选 add_paper 抛 RuntimeError → failed+1；其他候选正常入库（imported）。"""
    c, factory = aclient
    pid = await _create_project(factory, "Isolation Test")

    from app.repositories import library as lib_repo_mod

    original_add_paper = lib_repo_mod.add_paper
    call_count = {"n": 0}

    async def patched_add_paper(s, data):
        call_count["n"] += 1
        # 第一次调用抛错，模拟 DB 500
        if call_count["n"] == 1:
            raise RuntimeError("模拟 DB 500")
        return await original_add_paper(s, data)

    monkeypatch.setattr(lib_repo_mod, "add_paper", patched_add_paper)

    payload = {
        "candidates": [
            {"title": "Fail Paper", "doi": "10.1/fail"},
            {"title": "OK Paper", "doi": "10.1/ok"},
        ],
        "defaultStatus": "candidate",
    }

    r = await c.post(f"/projects/{pid}/papers/from-search", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["failed"] == 1
    assert body["imported"] == 1
    assert len(body["paperIds"]) == 1


# ---------------------------------------------------------------------------
# 测试 9: B — 已关联候选 included 再入库 → inclusion_status 升级为 included
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_from_search_upgrade_to_included_on_existing(aclient):
    """先以 candidate 入库，再以 included 入库同一候选 → inclusion_status 升级为 included。"""
    c, factory = aclient
    pid = await _create_project(factory, "Upgrade Inclusion Test")

    cand = {
        "title": "Corporate Governance Upgrade",
        "doi": "10.1/upgrade9",
        "year": 2023,
    }

    # 第一次：candidate
    r1 = await c.post(f"/projects/{pid}/papers/from-search", json={
        "candidates": [cand],
        "defaultStatus": "candidate",
    })
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["imported"] == 1
    paper_id = body1["paperIds"][0]

    # 验证初始 inclusion_status = candidate
    async with factory() as s:
        from sqlalchemy import select
        from app.models import ProjectPaper
        q = select(ProjectPaper).where(
            ProjectPaper.project_id == pid,
            ProjectPaper.paper_id == paper_id,
        )
        pp = (await s.execute(q)).scalar_one_or_none()
        assert pp is not None
        assert pp.inclusion_status == "candidate"

    # 第二次：included（同一候选 DOI 命中 dedup）
    r2 = await c.post(f"/projects/{pid}/papers/from-search", json={
        "candidates": [cand],
        "defaultStatus": "included",
    })
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    # 候选已存在 → skipped（不重复建 paper）
    assert body2["skipped"] == 1
    assert body2["imported"] == 0

    # 验证 inclusion_status 已升级为 included
    async with factory() as s:
        pp2 = (await s.execute(q)).scalar_one_or_none()
        assert pp2 is not None
        assert pp2.inclusion_status == "included", (
            f"期望 included，实际为 {pp2.inclusion_status}"
        )


# ---------------------------------------------------------------------------
# 测试 10: 事务隔离——某条候选触发 IntegrityError → rollback 后续候选仍成功
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_from_search_integrity_error_isolation(aclient, monkeypatch):
    """第 1 条候选触发 IntegrityError（模拟真实 DBAPI 约束违反）→
    session rollback 后第 2 条正常候选仍成功入库。

    证明 rollback 的隔离效果：若无 rollback，session 进入 failed-transaction 状态，
    后续候选的 add_paper 将因 "Can't operate on closed transaction" 而级联失败；
    有 rollback 则第 2 条正常完成 imported=1，failed=1。
    """
    c, factory = aclient
    pid = await _create_project(factory, "IntegrityError Isolation Test")

    from app.repositories import library as lib_repo_mod

    original_add_paper = lib_repo_mod.add_paper
    call_count = {"n": 0}

    async def patched_add_paper(s, data):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # 模拟真实 DBAPI IntegrityError（唯一约束冲突）
            # 构造一个 SQLAlchemy IntegrityError；orig 可为 None（测试专用）
            raise IntegrityError(
                statement="INSERT INTO paper ...",
                params={},
                orig=Exception("UNIQUE constraint failed: paper.doi"),
            )
        return await original_add_paper(s, data)

    monkeypatch.setattr(lib_repo_mod, "add_paper", patched_add_paper)

    payload = {
        "candidates": [
            {"title": "Integrity Fail Paper", "doi": "10.1/integrity_fail"},
            {"title": "OK After Rollback", "doi": "10.1/ok_after_rollback"},
        ],
        "defaultStatus": "candidate",
    }

    r = await c.post(f"/projects/{pid}/papers/from-search", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()

    # ① 触发 IntegrityError 的候选计入 failed
    assert body["failed"] == 1, f"期望 failed=1，实际={body['failed']}"
    # ② rollback 后，后续正常候选仍成功入库（证明隔离有效，而非级联失败）
    assert body["imported"] == 1, (
        f"期望 imported=1（rollback 后隔离有效），实际={body['imported']}。"
        "若=0 则说明 session 仍处于 failed-transaction 状态，rollback 缺失。"
    )
    assert len(body["paperIds"]) == 1
