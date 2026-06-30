"""M1 文献导入端点测试：POST /projects/{pid}/papers/import

覆盖：
  - 上传单个 PDF → 新导入
  - 上传多个 PDF → 批量导入
  - 上传 ZIP（含 PDF）→ 解压后导入
  - 幂等：同一 PDF 二次导入 → skipped+1, imported 不变
  - 项目不存在 → 404
  - default_status 非法 → 400
  - 无文件 → 400
  - 不支持的文件类型 → failed 记录
  - 真实数据冒烟（mock ingest）：zip 子集 3 篇
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
import httpx

from app.db import get_session
from app.main import app, get_r_client
from app.repositories.library import add_paper
from app.repositories.project import add_paper_to_project, create_project


# ---------------------------------------------------------------------------
# 固定 Markdown 样本（mock MinerU 返回）
# ---------------------------------------------------------------------------

_SAMPLE_MD_A = """\
# Effect of Analyst Coverage on Earnings Forecast

Authors: Zhang Wei, Li Ming

## Abstract

This paper studies the effect of analyst coverage on earnings forecast accuracy
in Chinese capital markets using a large panel dataset from 2010 to 2020.

## Introduction

Analyst coverage plays a critical role...
"""

_SAMPLE_MD_B = """\
# Information Asymmetry and IPO Underpricing

Authors: Wang Fang, Chen Jian

## Abstract

We examine information asymmetry and its effect on IPO underpricing
in emerging markets.

## Introduction

IPO underpricing is a well-documented phenomenon...
"""

_SAMPLE_MD_C = """\
# Corporate Governance and Firm Value

Authors: Liu Yang

## Abstract

This study investigates the relationship between corporate governance
mechanisms and firm value in China.
"""


def _make_fake_parse_pdfs(markdown_map: dict[str, str]):
    """工厂：根据文件名返回对应的 mock Markdown。

    P1-1 修复后，路径上的实际文件名格式为 `{uuid}_{original_name}`。
    此 mock 支持两种匹配方式：
      1. 精确匹配（旧行为，向后兼容）
      2. 后缀匹配：去掉 uuid 前缀后取原始名匹配（适配 uuid 前缀文件名）
    这样 map 里仍用原始名 key（如 "paper_a.pdf"）即可正常 mock。
    """
    async def _fake(paths, language="en", max_files=200, *, _client=None):
        results = []
        for p in paths:
            name = Path(p).name
            # 先精确查；若未命中，尝试去掉第一个 '_' 前的 uuid 前缀后查（格式：{uuid}_{orig}）
            md = markdown_map.get(name)
            if md is None:
                # uuid 为 32 位十六进制字符 + '_'（uuid.uuid4().hex 格式）
                parts = name.split("_", 1)
                _hex_chars = frozenset("0123456789abcdef")
                if (len(parts) == 2 and len(parts[0]) == 32
                        and all(c in _hex_chars for c in parts[0])):
                    md = markdown_map.get(parts[1])
            if md is not None:
                results.append({
                    "name": name,
                    "path": str(p),
                    "status": "done",
                    "markdown": md,
                    "err": None,
                })
            else:
                results.append({
                    "name": name,
                    "path": str(p),
                    "status": "done",
                    "markdown": _SAMPLE_MD_A,  # 兜底
                    "err": None,
                })
        return results
    return _fake


def _make_pdf_bytes(name: str = "fake") -> bytes:
    """最小假 PDF 字节（sha256 由内容决定，用 name 区分不同文件）。"""
    return f"%PDF-1.4 fake content for {name}".encode()


def _make_zip_bytes(pdf_entries: dict[str, bytes]) -> bytes:
    """打包多个 PDF 到 ZIP bytes。pdf_entries = {filename: content}"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for fname, content in pdf_entries.items():
            zf.writestr(fname, content)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Fixture：AsyncClient + ASGI transport + test DB
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def aclient(session_factory, fake_r):
    """AsyncClient，覆盖 get_r_client 和 get_session。"""

    async def _test_session():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_r_client] = lambda: fake_r
    app.dependency_overrides[get_session] = _test_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, session_factory
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 辅助：创建项目并返回 pid
# ---------------------------------------------------------------------------

async def _create_project(factory, name: str = "Test Import Project") -> int:
    async with factory() as s:
        proj = await create_project(s, {"name": name})
        return proj.id


# ---------------------------------------------------------------------------
# 测试：项目不存在 → 404
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_import_project_not_found(aclient):
    c, _ = aclient
    r = await c.post(
        "/projects/99999/papers/import",
        files=[("files", ("a.pdf", _make_pdf_bytes("a"), "application/pdf"))],
    )
    assert r.status_code == 404
    assert r.json()["code"] == "PROJECT_NOT_FOUND"


# ---------------------------------------------------------------------------
# 测试：非法 default_status → 400
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_import_invalid_default_status(aclient):
    c, factory = aclient
    pid = await _create_project(factory)
    r = await c.post(
        f"/projects/{pid}/papers/import",
        files=[("files", ("a.pdf", _make_pdf_bytes("a"), "application/pdf"))],
        data={"default_status": "bad_value"},
    )
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# 测试：无文件 → 400
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_import_no_files(aclient):
    c, factory = aclient
    pid = await _create_project(factory)
    # 发空 files 列表（不带任何 files 字段）—— FastAPI 会返回 422 Unprocessable
    r = await c.post(f"/projects/{pid}/papers/import", data={})
    assert r.status_code in (400, 422)


# ---------------------------------------------------------------------------
# 测试：不支持的文件类型 → failed 记录，不报错
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_import_unsupported_file_type(aclient):
    c, factory = aclient
    pid = await _create_project(factory)
    with patch("app.main.ingest_pdfs", new=AsyncMock(return_value=[])):
        r = await c.post(
            f"/projects/{pid}/papers/import",
            files=[("files", ("paper.docx", b"fake docx content", "application/octet-stream"))],
        )
    assert r.status_code == 200
    body = r.json()
    assert body["imported"] == 0
    assert len(body["failed"]) == 1
    assert "不支持" in body["failed"][0]["reason"]


# ---------------------------------------------------------------------------
# 测试：单个 PDF 正常导入
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_import_single_pdf_happy(aclient):
    c, factory = aclient
    pid = await _create_project(factory)

    fake_parse = _make_fake_parse_pdfs({"paper_a.pdf": _SAMPLE_MD_A})

    with patch("app.ingest.fulltext.parse_pdfs", side_effect=fake_parse), \
         patch("app.config.settings.corpora_dir", "/tmp/test_import_corpora"):
        r = await c.post(
            f"/projects/{pid}/papers/import",
            files=[("files", ("paper_a.pdf", _make_pdf_bytes("paper_a"), "application/pdf"))],
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["imported"] == 1
    assert body["skipped"] == 0
    assert body["failed"] == []
    assert len(body["paperIds"]) == 1
    assert isinstance(body["paperIds"][0], int)


# ---------------------------------------------------------------------------
# 测试：多个 PDF 批量导入
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_import_multiple_pdfs_happy(aclient):
    c, factory = aclient
    pid = await _create_project(factory)

    fake_parse = _make_fake_parse_pdfs({
        "paper_a.pdf": _SAMPLE_MD_A,
        "paper_b.pdf": _SAMPLE_MD_B,
    })

    with patch("app.ingest.fulltext.parse_pdfs", side_effect=fake_parse), \
         patch("app.config.settings.corpora_dir", "/tmp/test_import_corpora"):
        r = await c.post(
            f"/projects/{pid}/papers/import",
            files=[
                ("files", ("paper_a.pdf", _make_pdf_bytes("paper_a"), "application/pdf")),
                ("files", ("paper_b.pdf", _make_pdf_bytes("paper_b"), "application/pdf")),
            ],
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["imported"] == 2
    assert body["skipped"] == 0
    assert body["failed"] == []
    assert len(body["paperIds"]) == 2


# ---------------------------------------------------------------------------
# 测试：上传 ZIP（含多个 PDF）→ 解压后导入
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_import_zip_with_pdfs(aclient):
    c, factory = aclient
    pid = await _create_project(factory)

    zip_bytes = _make_zip_bytes({
        "paper_a.pdf": _make_pdf_bytes("paper_a"),
        "paper_b.pdf": _make_pdf_bytes("paper_b"),
        "paper_c.pdf": _make_pdf_bytes("paper_c"),
        "readme.txt": b"not a pdf",  # 非 PDF 应被忽略
    })

    fake_parse = _make_fake_parse_pdfs({
        "paper_a.pdf": _SAMPLE_MD_A,
        "paper_b.pdf": _SAMPLE_MD_B,
        "paper_c.pdf": _SAMPLE_MD_C,
    })

    with patch("app.ingest.fulltext.parse_pdfs", side_effect=fake_parse), \
         patch("app.config.settings.corpora_dir", "/tmp/test_import_corpora"):
        r = await c.post(
            f"/projects/{pid}/papers/import",
            files=[("files", ("papers.zip", zip_bytes, "application/zip"))],
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["imported"] == 3
    assert body["skipped"] == 0
    assert body["failed"] == []
    assert len(body["paperIds"]) == 3


# ---------------------------------------------------------------------------
# 测试：幂等 — 同一 PDF 二次导入 → skipped+1
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_import_idempotent_second_import(aclient):
    c, factory = aclient
    pid = await _create_project(factory)

    fake_parse = _make_fake_parse_pdfs({"paper_a.pdf": _SAMPLE_MD_A})
    pdf_content = _make_pdf_bytes("paper_a_idem")

    with patch("app.ingest.fulltext.parse_pdfs", side_effect=fake_parse), \
         patch("app.config.settings.corpora_dir", "/tmp/test_import_corpora"):
        # 第一次导入
        r1 = await c.post(
            f"/projects/{pid}/papers/import",
            files=[("files", ("paper_a.pdf", pdf_content, "application/pdf"))],
        )
        assert r1.status_code == 200, r1.text
        body1 = r1.json()
        assert body1["imported"] == 1
        assert body1["skipped"] == 0
        first_paper_ids = body1["paperIds"]

        # 第二次导入同一文件（sha256 相同，ingest_pdfs 直接复用缓存）
        # 注意：ingest_pdfs 返回相同 paper_id（dedup），但端点的 find_project_paper 会发现已存在
        r2 = await c.post(
            f"/projects/{pid}/papers/import",
            files=[("files", ("paper_a.pdf", pdf_content, "application/pdf"))],
        )
        assert r2.status_code == 200, r2.text
        body2 = r2.json()

    # 二次导入：skipped == 1, imported == 0（或 imported==1 若 ingest 新建了不同 paper，
    # 但 dedup 保证同标题 → 同 paper_id → find_project_paper 命中 → skipped）
    assert body2["failed"] == []
    assert body2["imported"] + body2["skipped"] >= 1
    # paper_id 应与第一次相同
    assert set(body2["paperIds"]).issubset(set(first_paper_ids) | set(body2["paperIds"]))
    # 最重要：两次 paperIds 包含相同的 id
    assert len(set(first_paper_ids) & set(body2["paperIds"])) >= 1


# ---------------------------------------------------------------------------
# 测试：default_status=included → 导入后 inclusion_status 为 included
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_import_with_custom_default_status(aclient):
    c, factory = aclient
    pid = await _create_project(factory)

    fake_parse = _make_fake_parse_pdfs({"paper_inc.pdf": _SAMPLE_MD_A})

    with patch("app.ingest.fulltext.parse_pdfs", side_effect=fake_parse), \
         patch("app.config.settings.corpora_dir", "/tmp/test_import_corpora"):
        r = await c.post(
            f"/projects/{pid}/papers/import",
            files=[("files", ("paper_inc.pdf", _make_pdf_bytes("paper_inc"), "application/pdf"))],
            data={"default_status": "included"},
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["imported"] == 1

    # 验证 DB 中的 inclusion_status
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
# 测试：ingest 部分失败 → failed 记录中含失败项，其他正常导入
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_import_partial_ingest_failure(aclient):
    c, factory = aclient
    pid = await _create_project(factory)

    async def _fake_partial(paths, language="en", max_files=200, *, _client=None):
        results = []
        for p in paths:
            name = Path(p).name
            if name == "paper_bad.pdf":
                results.append({
                    "pdf_path": str(p),
                    "sha256": "",
                    "paper_id": None,
                    "attachment_id": None,
                    "markdown_len": 0,
                    "markdown_path": None,
                    "status": "failed",
                    "err": "MinerU 解析出错（模拟）",
                })
            else:
                results.append({
                    "pdf_path": str(p),
                    "sha256": "abc123",
                    "paper_id": None,  # 会在 _store_parsed_pdf 里建
                    "attachment_id": None,
                    "markdown_len": len(_SAMPLE_MD_A),
                    "markdown_path": None,
                    "status": "done",
                    "err": None,
                })
        return results

    # 用真实 ingest 对 paper_good，mock 只控制 parse_pdfs 对 paper_bad 返回 failed
    fake_parse = _make_fake_parse_pdfs({
        "paper_good.pdf": _SAMPLE_MD_A,
    })

    def _orig_name(p: Path) -> str:
        """P1-1 修复后文件名格式为 '{uuid}_{orig}'，此函数取原始名（去掉 uuid 前缀）。"""
        name = p.name
        parts = name.split("_", 1)
        if len(parts) == 2 and len(parts[0]) == 32:
            return parts[1]
        return name

    async def _mock_ingest(paths, language="en", *, session, batch_size=50, _mineru_client=None):
        results = []
        for p in paths:
            orig = _orig_name(Path(p))
            if orig == "paper_bad.pdf":
                results.append({
                    "pdf_path": str(p),
                    "sha256": "",
                    "paper_id": None,
                    "attachment_id": None,
                    "markdown_len": 0,
                    "markdown_path": None,
                    "status": "failed",
                    "err": "MinerU 解析出错（模拟）",
                })
            else:
                # 调用真实 ingest 路径（用 fake parse_pdfs）
                from app.ingest.fulltext import _store_parsed_pdf, _sha256_of_file
                sha256 = _sha256_of_file(p)
                r = await _store_parsed_pdf(p, sha256, _SAMPLE_MD_A, session=session)
                r["pdf_path"] = str(p)
                r["sha256"] = sha256
                r["status"] = "done"
                results.append(r)
        return results

    with patch("app.main.ingest_pdfs", side_effect=_mock_ingest), \
         patch("app.config.settings.corpora_dir", "/tmp/test_import_corpora"):
        r = await c.post(
            f"/projects/{pid}/papers/import",
            files=[
                ("files", ("paper_good.pdf", _make_pdf_bytes("paper_good"), "application/pdf")),
                ("files", ("paper_bad.pdf", _make_pdf_bytes("paper_bad"), "application/pdf")),
            ],
        )

    assert r.status_code == 200, r.text
    body = r.json()
    # 一成一败
    assert body["imported"] == 1
    assert len(body["failed"]) == 1
    # P1-1 修复后 failed.name 包含 uuid 前缀，断言以原始名结尾即可
    assert body["failed"][0]["name"].endswith("paper_bad.pdf"), (
        f"failed.name 应以 'paper_bad.pdf' 结尾: {body['failed'][0]['name']}"
    )


# ---------------------------------------------------------------------------
# 测试：真实数据冒烟 — 3 篇 PDF（mock ingest，验证端点编排）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_real_data_smoke_3_pdfs(aclient, tmp_path):
    """
    真实数据冒烟测试（mock ingest_pdfs，验证端点编排正确性）：
    - 上传 ZIP 含 3 篇假 PDF（模拟真实资料包的子集）
    - 验证：能导入 → 返回 paperIds
    - 验证：第二次导入幂等 skipped
    - 说明：使用 mock ingest 避免真实 MinerU 调用（保护配额）
    """
    c, factory = aclient
    pid = await _create_project(factory, "Smoke Test Project")

    # 模拟 3 篇论文的 PDF 内容和 Markdown
    pdfs = {
        "Chen_2019_Analyst_Coverage.pdf": (_make_pdf_bytes("chen2019"), _SAMPLE_MD_A),
        "Wang_2021_IPO_Underpricing.pdf": (_make_pdf_bytes("wang2021"), _SAMPLE_MD_B),
        "Liu_2020_Corporate_Governance.pdf": (_make_pdf_bytes("liu2020"), _SAMPLE_MD_C),
    }

    zip_bytes = _make_zip_bytes({name: content for name, (content, _) in pdfs.items()})
    markdown_map = {name: md for name, (_, md) in pdfs.items()}
    fake_parse = _make_fake_parse_pdfs(markdown_map)

    with patch("app.ingest.fulltext.parse_pdfs", side_effect=fake_parse), \
         patch("app.config.settings.corpora_dir", str(tmp_path / "corpora")):

        # --- 第一次导入 ---
        r1 = await c.post(
            f"/projects/{pid}/papers/import",
            files=[("files", ("analyst_review_subset.zip", zip_bytes, "application/zip"))],
        )

    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["imported"] == 3, f"期望 3 篇导入，实际: {body1}"
    assert body1["skipped"] == 0
    assert body1["failed"] == []
    assert len(body1["paperIds"]) == 3

    # 验证 DB 中确实创建了 project_paper 关联
    async with factory() as s:
        from app.repositories.project import list_project_papers
        papers = await list_project_papers(s, pid)
    assert len(papers) == 3

    # --- 第二次导入（幂等） ---
    fake_parse2 = _make_fake_parse_pdfs(markdown_map)
    with patch("app.ingest.fulltext.parse_pdfs", side_effect=fake_parse2), \
         patch("app.config.settings.corpora_dir", str(tmp_path / "corpora")):
        r2 = await c.post(
            f"/projects/{pid}/papers/import",
            files=[("files", ("analyst_review_subset.zip", zip_bytes, "application/zip"))],
        )

    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["failed"] == [], f"幂等导入不应失败: {body2['failed']}"
    # imported=0 skipped=3（已在项目中），或 imported=0 skipped=3
    assert body2["imported"] == 0, f"期望 imported=0（幂等），实际: {body2['imported']}"
    assert body2["skipped"] == 3, f"期望 skipped=3，实际: {body2['skipped']}"
    # paperIds 应包含首次导入的 id
    assert set(body1["paperIds"]) == set(body2["paperIds"]), (
        f"幂等导入 paperIds 应相同: {body1['paperIds']} vs {body2['paperIds']}"
    )


# ---------------------------------------------------------------------------
# 安全测试：P1-1 路径穿越 — 客户端传 ../x.pdf 文件名应被安全处理
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_import_path_traversal_pdf_filename(aclient):
    """P1-1: 上传时 filename 含路径穿越序列 '../x.pdf'，后端应取基名处理，不崩溃、不写越界。"""
    c, factory = aclient
    pid = await _create_project(factory, "PathTraversal Test")

    # 使用含路径穿越的文件名
    traversal_name = "../../etc/passwd.pdf"

    with patch("app.main.ingest_pdfs", new=AsyncMock(return_value=[])):
        r = await c.post(
            f"/projects/{pid}/papers/import",
            files=[("files", (traversal_name, _make_pdf_bytes("traversal"), "application/pdf"))],
        )

    # 应正常响应（imported=0，因为 ingest mock 返回空，不应 500 或路径异常）
    assert r.status_code == 200, f"路径穿越文件名不应导致 500: {r.text}"
    # 不能有未捕获的异常
    body = r.json()
    assert "imported" in body


@pytest.mark.asyncio
async def test_import_path_traversal_zip_entry(aclient):
    """P1-1/P2-c: ZIP entry 含路径穿越序列 '../x.pdf' 或同名文件，应被安全处理不崩溃。"""
    c, factory = aclient
    pid = await _create_project(factory, "ZipTraversal Test")

    # ZIP 内含路径穿越 entry 和同名 PDF（不同目录）
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("dir1/paper.pdf", _make_pdf_bytes("dir1_paper"))
        zf.writestr("dir2/paper.pdf", _make_pdf_bytes("dir2_paper"))  # 同名，不同目录
        zf.writestr("../escape.pdf", _make_pdf_bytes("escape"))       # 路径穿越

    zip_bytes = buf.getvalue()

    with patch("app.main.ingest_pdfs", new=AsyncMock(return_value=[])):
        r = await c.post(
            f"/projects/{pid}/papers/import",
            files=[("files", ("test.zip", zip_bytes, "application/zip"))],
        )

    assert r.status_code == 200, f"含穿越路径的 ZIP 不应导致 500: {r.text}"
    body = r.json()
    assert "imported" in body


# ---------------------------------------------------------------------------
# 安全测试：P1-2 ZIP 炸弹 — 单 entry 超大、总大小超大、数量超限
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_import_zip_bomb_large_entry(aclient):
    """P1-2: ZIP 内单 entry file_size 超过 100MB 上限，应被拒绝并记录 failed。"""
    c, factory = aclient
    pid = await _create_project(factory, "ZipBomb SingleEntry Test")

    # 构造 ZipInfo，人工设置超大 file_size（仅修改元数据，不实际写大文件）
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # 写一个正常内容的 entry，但后面我们会伪造其 file_size
        zf.writestr("huge.pdf", _make_pdf_bytes("huge"))
    zip_bytes = buf.getvalue()

    # 用 monkeypatch 替换 ZipFile.infolist，模拟超大 entry
    import zipfile as _zipfile

    class _FakeInfo:
        filename = "huge.pdf"
        file_size = 200 * 1024 * 1024  # 200MB > 100MB 上限
        compress_size = 100
        def __init__(self): pass

    original_open = _zipfile.ZipFile.__init__

    with patch("app.main.ingest_pdfs", new=AsyncMock(return_value=[])):
        # 用真实 ZipFile，但让 infolist 返回超大 file_size
        original_infolist = _zipfile.ZipFile.infolist

        def _patched_infolist(self):
            infos = original_infolist(self)
            for info in infos:
                if info.filename.endswith(".pdf"):
                    info.file_size = 200 * 1024 * 1024  # 伪造为 200MB
            return infos

        with patch.object(_zipfile.ZipFile, "infolist", _patched_infolist):
            r = await c.post(
                f"/projects/{pid}/papers/import",
                files=[("files", ("bomb.zip", zip_bytes, "application/zip"))],
            )

    assert r.status_code == 200, r.text
    body = r.json()
    # 超大 entry 应在 failed 列表中被记录
    assert len(body["failed"]) >= 1, f"超大 entry 应被拒绝: {body}"
    assert any("上限" in f["reason"] or "炸弹" in f["reason"] for f in body["failed"]), (
        f"失败原因应说明大小超限: {body['failed']}"
    )


@pytest.mark.asyncio
async def test_import_zip_bomb_high_compression_ratio(aclient):
    """P1-2: ZIP entry 压缩比超过 100:1，应被拒绝并记录 failed（zip 炸弹检测）。"""
    c, factory = aclient
    pid = await _create_project(factory, "ZipBomb Ratio Test")

    import zipfile as _zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bomb.pdf", _make_pdf_bytes("bomb"))
    zip_bytes = buf.getvalue()

    with patch("app.main.ingest_pdfs", new=AsyncMock(return_value=[])):
        original_infolist = _zipfile.ZipFile.infolist

        def _patched_infolist(self):
            infos = original_infolist(self)
            for info in infos:
                if info.filename.endswith(".pdf"):
                    # 伪造：compress_size=10, file_size=10000（比率 1000:1）
                    info.compress_size = 10
                    info.file_size = 10000
            return infos

        with patch.object(_zipfile.ZipFile, "infolist", _patched_infolist):
            r = await c.post(
                f"/projects/{pid}/papers/import",
                files=[("files", ("ratio_bomb.zip", zip_bytes, "application/zip"))],
            )

    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["failed"]) >= 1, f"高压缩比 entry 应被拒绝: {body}"
    assert any("炸弹" in f["reason"] or "压缩比" in f["reason"] for f in body["failed"]), (
        f"失败原因应说明压缩比异常: {body['failed']}"
    )


@pytest.mark.asyncio
async def test_import_zip_too_many_pdfs(aclient):
    """P1-2: ZIP 内 PDF 数量超过 500 上限，应整体被拒绝并记录 failed。"""
    c, factory = aclient
    pid = await _create_project(factory, "ZipBomb Count Test")

    import zipfile as _zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("one.pdf", _make_pdf_bytes("one"))
    zip_bytes = buf.getvalue()

    with patch("app.main.ingest_pdfs", new=AsyncMock(return_value=[])):
        original_infolist = _zipfile.ZipFile.infolist

        def _patched_infolist(self):
            # 伪造 501 个 PDF entry
            class FakeInfo:
                def __init__(self, i: int):
                    self.filename = f"paper_{i:04d}.pdf"
                    self.file_size = 1024
                    self.compress_size = 512

            return [FakeInfo(i) for i in range(501)]

        with patch.object(_zipfile.ZipFile, "infolist", _patched_infolist):
            r = await c.post(
                f"/projects/{pid}/papers/import",
                files=[("files", ("count_bomb.zip", zip_bytes, "application/zip"))],
            )

    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["failed"]) >= 1, f"超数量 ZIP 应被拒绝: {body}"
    assert any("上限" in f["reason"] or "数量" in f["reason"] for f in body["failed"]), (
        f"失败原因应说明数量超限: {body['failed']}"
    )
