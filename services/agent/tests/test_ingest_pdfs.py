"""批量摄取 ingest_pdfs 单元测试。

覆盖:
  - 批量调用（分块）：n > batch_size 时分多批提交 parse_pdfs
  - 缓存跳过：sha256.md 已存在的路径不进 MinerU
  - 失败隔离：单篇 MinerU 失败不影响其他篇
  - Paper + Attachment 建好（通过 _store_parsed_pdf 复用逻辑）
  - 整批 MinerU 调用失败 → 每篇记 failed（不崩溃）
  - 空列表输入 → 空列表输出
  - PDF 不存在 → 该篇 status=failed
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.ingest.fulltext import ingest_pdfs
from app.models import Attachment, Paper


# ---------------------------------------------------------------------------
# 测试工具
# ---------------------------------------------------------------------------

_SAMPLE_MD = """\
# Test Paper: Batch Ingest

Authors: Alice Test

## Abstract

This is a test paper for batch ingest validation.

## Introduction

...
"""


def _make_parse_result(path: Path, status: str = "done", markdown: str = _SAMPLE_MD) -> dict:
    """构造 parse_pdfs 返回的单篇结果。"""
    return {
        "name": path.name,
        "path": str(path),
        "status": status,
        "markdown": markdown if status == "done" else None,
        "err": None if status == "done" else "MinerU 模拟失败",
    }


def _fake_parse_pdfs_factory(results_map: dict | None = None, raise_exc=False):
    """生成 parse_pdfs 的 mock：按路径名映射结果，或全部成功。

    Args:
        results_map: {filename: "done"|"failed"} 或 None（全部 done）
        raise_exc:   若 True，直接 raise RuntimeError（整批失败）
    """
    async def _fake(paths, language="en", max_files=200, *, _client=None):
        if raise_exc:
            raise RuntimeError("MinerU 整批失败（模拟）")
        results = []
        for p in paths:
            p = Path(p)
            if results_map and p.name in results_map:
                status = results_map[p.name]
            else:
                status = "done"
            results.append(_make_parse_result(p, status=status))
        return results

    return _fake


# ---------------------------------------------------------------------------
# 测试：空列表
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_pdfs_empty(session, tmp_path):
    """空列表 → 空列表输出。"""
    results = await ingest_pdfs([], session=session)
    assert results == []


# ---------------------------------------------------------------------------
# 测试：单批全部成功
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_pdfs_single_batch_all_done(session, tmp_path):
    """3 篇 PDF（<batch_size）→ 一次 parse_pdfs → 全部 done。"""
    pdfs = []
    for i in range(3):
        p = tmp_path / f"paper_{i}.pdf"
        p.write_bytes(f"%PDF-1.4 fake {i}".encode())
        pdfs.append(p)

    corpora_dir = tmp_path / "corpora"
    call_count = 0

    async def _fake(paths, language="en", max_files=200, *, _client=None):
        nonlocal call_count
        call_count += 1
        return [_make_parse_result(Path(p)) for p in paths]

    with patch("app.ingest.fulltext.parse_pdfs", side_effect=_fake), \
         patch("app.config.settings.corpora_dir", str(corpora_dir)):
        results = await ingest_pdfs(pdfs, session=session)

    assert call_count == 1  # 只提交一次
    assert len(results) == 3
    for r in results:
        assert r["status"] == "done", f"期望 done，实际: {r}"
        assert r["paper_id"] is not None
        assert r["attachment_id"] is not None
        assert r["markdown_len"] > 0


# ---------------------------------------------------------------------------
# 测试：多批（batch_size 分块）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_pdfs_multi_batch(session, tmp_path):
    """7 篇 PDF + batch_size=3 → ceil(7/3)=3 次 parse_pdfs。"""
    pdfs = []
    for i in range(7):
        p = tmp_path / f"paper_{i}.pdf"
        p.write_bytes(f"%PDF {i}".encode())
        pdfs.append(p)

    corpora_dir = tmp_path / "corpora"
    call_batches: list[list[str]] = []

    async def _fake(paths, language="en", max_files=200, *, _client=None):
        call_batches.append([str(p) for p in paths])
        return [_make_parse_result(Path(p)) for p in paths]

    with patch("app.ingest.fulltext.parse_pdfs", side_effect=_fake), \
         patch("app.config.settings.corpora_dir", str(corpora_dir)):
        results = await ingest_pdfs(pdfs, session=session, batch_size=3)

    assert len(call_batches) == 3  # ceil(7/3) = 3 次
    # 第一批 3 篇，第二批 3 篇，第三批 1 篇
    assert len(call_batches[0]) == 3
    assert len(call_batches[1]) == 3
    assert len(call_batches[2]) == 1

    assert len(results) == 7
    done_count = sum(1 for r in results if r["status"] == "done")
    assert done_count == 7


# ---------------------------------------------------------------------------
# 测试：缓存跳过（sha256.md 已存在）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_pdfs_cache_skip(session, tmp_path):
    """已缓存的 PDF → 跳过 MinerU，直接读盘恢复，Paper/Attachment 建好。"""
    pdfs = []
    for i in range(3):
        p = tmp_path / f"cached_{i}.pdf"
        p.write_bytes(f"%PDF-1.4 cached {i}".encode())
        pdfs.append(p)

    corpora_dir = tmp_path / "corpora"

    # 预先建好 sha256.md
    from app.ingest.fulltext import _sha256_of_file, _save_markdown
    with patch("app.config.settings.corpora_dir", str(corpora_dir)):
        sha256s = []
        for p in pdfs:
            sha256 = _sha256_of_file(p)
            _save_markdown(sha256, _SAMPLE_MD)
            sha256s.append(sha256)

    parse_call_count = 0

    async def _fake_parse(paths, language="en", max_files=200, *, _client=None):
        nonlocal parse_call_count
        parse_call_count += 1
        return [_make_parse_result(Path(p)) for p in paths]

    with patch("app.ingest.fulltext.parse_pdfs", side_effect=_fake_parse), \
         patch("app.config.settings.corpora_dir", str(corpora_dir)):
        results = await ingest_pdfs(pdfs, session=session)

    # 全部缓存 → parse_pdfs 不应被调用
    assert parse_call_count == 0, f"缓存命中时不应调用 MinerU，实际调用 {parse_call_count} 次"
    assert len(results) == 3
    for r in results:
        assert r["status"] == "cached", f"期望 cached，实际: {r['status']}"
        assert r["paper_id"] is not None
        assert r["attachment_id"] is not None


# ---------------------------------------------------------------------------
# 测试：部分缓存（混合）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_pdfs_partial_cache(session, tmp_path):
    """2 篇缓存 + 2 篇新 → parse_pdfs 只收到 2 篇新的。"""
    pdfs = []
    for i in range(4):
        p = tmp_path / f"mix_{i}.pdf"
        p.write_bytes(f"%PDF mix {i}".encode())
        pdfs.append(p)

    corpora_dir = tmp_path / "corpora"

    # 预先缓存前 2 篇
    from app.ingest.fulltext import _sha256_of_file, _save_markdown
    with patch("app.config.settings.corpora_dir", str(corpora_dir)):
        for p in pdfs[:2]:
            sha256 = _sha256_of_file(p)
            _save_markdown(sha256, _SAMPLE_MD)

    submitted_paths: list[list[str]] = []

    async def _fake_parse(paths, language="en", max_files=200, *, _client=None):
        submitted_paths.append([str(p) for p in paths])
        return [_make_parse_result(Path(p)) for p in paths]

    with patch("app.ingest.fulltext.parse_pdfs", side_effect=_fake_parse), \
         patch("app.config.settings.corpora_dir", str(corpora_dir)):
        results = await ingest_pdfs(pdfs, session=session)

    # parse_pdfs 只收到 2 篇新的
    assert len(submitted_paths) == 1
    assert len(submitted_paths[0]) == 2
    # 文件名应为 mix_2, mix_3
    submitted_names = {Path(p).name for p in submitted_paths[0]}
    assert "mix_2.pdf" in submitted_names
    assert "mix_3.pdf" in submitted_names

    assert len(results) == 4
    cached_count = sum(1 for r in results if r["status"] == "cached")
    done_count = sum(1 for r in results if r["status"] == "done")
    assert cached_count == 2
    assert done_count == 2


# ---------------------------------------------------------------------------
# 测试：单篇失败隔离（不影响其他篇）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_pdfs_single_failure_isolation(session, tmp_path):
    """3 篇中 1 篇 MinerU 失败 → 其他 2 篇仍成功（失败隔离）。"""
    pdfs = []
    for i in range(3):
        p = tmp_path / f"iso_{i}.pdf"
        p.write_bytes(f"%PDF iso {i}".encode())
        pdfs.append(p)

    corpora_dir = tmp_path / "corpora"
    # 第 1 篇（iso_1.pdf）模拟 MinerU 失败
    fail_map = {"iso_1.pdf": "failed"}

    with patch("app.ingest.fulltext.parse_pdfs",
               side_effect=_fake_parse_pdfs_factory(results_map=fail_map)), \
         patch("app.config.settings.corpora_dir", str(corpora_dir)):
        results = await ingest_pdfs(pdfs, session=session)

    assert len(results) == 3
    statuses = {Path(r["pdf_path"]).name: r["status"] for r in results}
    assert statuses["iso_0.pdf"] == "done"
    assert statuses["iso_1.pdf"] == "failed"
    assert statuses["iso_2.pdf"] == "done"

    # 确保失败篇没有建 Paper/Attachment
    failed_r = next(r for r in results if Path(r["pdf_path"]).name == "iso_1.pdf")
    assert failed_r["paper_id"] is None
    assert failed_r["attachment_id"] is None


# ---------------------------------------------------------------------------
# 测试：整批 MinerU 失败（整批异常）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_pdfs_batch_exception_isolation(session, tmp_path):
    """parse_pdfs 抛异常 → 整批记 failed，不崩溃。"""
    pdfs = []
    for i in range(3):
        p = tmp_path / f"exc_{i}.pdf"
        p.write_bytes(f"%PDF exc {i}".encode())
        pdfs.append(p)

    corpora_dir = tmp_path / "corpora"

    with patch("app.ingest.fulltext.parse_pdfs",
               side_effect=_fake_parse_pdfs_factory(raise_exc=True)), \
         patch("app.config.settings.corpora_dir", str(corpora_dir)):
        results = await ingest_pdfs(pdfs, session=session)

    assert len(results) == 3
    for r in results:
        assert r["status"] == "failed"
        assert "MinerU 批次调用失败" in (r["err"] or "")


# ---------------------------------------------------------------------------
# 测试：PDF 不存在 → status=failed（不抛异常）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_pdfs_missing_pdf(session, tmp_path):
    """不存在的 PDF 路径 → status=failed，不崩溃。"""
    missing = tmp_path / "does_not_exist.pdf"
    real_pdf = tmp_path / "real.pdf"
    real_pdf.write_bytes(b"%PDF real")

    corpora_dir = tmp_path / "corpora"

    async def _fake(paths, language="en", max_files=200, *, _client=None):
        return [_make_parse_result(Path(p)) for p in paths]

    with patch("app.ingest.fulltext.parse_pdfs", side_effect=_fake), \
         patch("app.config.settings.corpora_dir", str(corpora_dir)):
        results = await ingest_pdfs([missing, real_pdf], session=session)

    assert len(results) == 2
    result_map = {Path(r["pdf_path"]).name: r for r in results}
    assert result_map["does_not_exist.pdf"]["status"] == "failed"
    assert "不存在" in (result_map["does_not_exist.pdf"]["err"] or "")
    assert result_map["real.pdf"]["status"] == "done"


# ---------------------------------------------------------------------------
# 测试：Paper + Attachment 建好（DB 验证）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_pdfs_db_records_created(session, tmp_path):
    """2 篇 PDF → 2 个 Paper + 2 个 Attachment 建好（DB 验证）。"""
    pdfs = []
    for i in range(2):
        p = tmp_path / f"dbtest_{i}.pdf"
        p.write_bytes(f"%PDF dbtest {i}".encode())
        pdfs.append(p)

    corpora_dir = tmp_path / "corpora"

    async def _fake(paths, language="en", max_files=200, *, _client=None):
        # 每篇用文件名派生的唯一标题, 否则标题哈希去重会把两篇并成一篇
        return [
            _make_parse_result(
                Path(p),
                markdown=f"# DB Test {Path(p).stem}\n\nAuthors: Alice Test\n\n## Abstract\n\nUnique paper {Path(p).stem}.\n",
            )
            for p in paths
        ]

    with patch("app.ingest.fulltext.parse_pdfs", side_effect=_fake), \
         patch("app.config.settings.corpora_dir", str(corpora_dir)):
        results = await ingest_pdfs(pdfs, session=session)

    assert all(r["status"] == "done" for r in results)

    # 验证 DB 中的记录
    paper_ids = [r["paper_id"] for r in results]
    att_ids = [r["attachment_id"] for r in results]

    papers = (await session.execute(select(Paper).where(Paper.id.in_(paper_ids)))).scalars().all()
    atts = (await session.execute(select(Attachment).where(Attachment.id.in_(att_ids)))).scalars().all()

    assert len(papers) == 2
    assert len(atts) == 2
    for att in atts:
        assert att.mineru_status == "done"
        assert att.markdown_path is not None
        assert "fulltext" in att.markdown_path
