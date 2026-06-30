"""P3-5 — 离线自包含 demo 内置样例模式测试。

覆盖 `scripts/run_agent_e2e.py` 新增的 `--offline-fixtures builtin` 路径核心逻辑：
fresh 环境（无任何缓存 markdown、无 MinerU/LLM key）下，seed 样例 markdown 进语料缓存，
ingest 走缓存命中建 Paper/Attachment，最终 load_project_corpus 得到含 content_sha256 的
records，且这些 sha256 与 seed 文件内容哈希严格对得上（verify_runlog 溯源校验的真集合）。

为何不在此重跑完整 agent run + verify：完整离线 stub run + RunLog verify 已由
tests/test_agent_e2e_smoke.py 覆盖（同一离线 stub LLM 路径）。本测试聚焦 fixtures 模式
**新增**的 seed + 缓存命中 + 溯源集合一致性，快且确定，不重复造轮子。
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from app.ingest.fulltext import ingest_pdfs  # noqa: E402
from app.repositories.project import (  # noqa: E402
    add_paper_to_project,
    create_project,
    set_inclusion,
)
from app.review.load import load_project_corpus  # noqa: E402

# scripts/ 已加入 sys.path
from run_agent_e2e import BUILTIN_OFFLINE_MARKDOWNS, seed_offline_fixtures  # noqa: E402

_FIXTURES_DIR = Path("builtin")


def test_builtin_demo_fixtures_present():
    """内置 demo 样例应含 ≥2 篇 markdown。"""
    assert len(BUILTIN_OFFLINE_MARKDOWNS) >= 2
    # 每篇应含一级标题（供元数据抽取 title），不可空
    for name, text in BUILTIN_OFFLINE_MARKDOWNS.items():
        assert text.strip(), f"样例 markdown 为空: {name}"
        assert text.lstrip().startswith("#"), f"样例 markdown 缺一级标题: {name}"


def test_seed_offline_fixtures_writes_cache(tmp_path):
    """seed 应把每篇内置 markdown 按其**文件内容 sha256** 写入 {CORPORA}/fulltext/。"""
    fresh_corpora = tmp_path / "corpora"
    with patch("app.config.settings.corpora_dir", str(fresh_corpora)):
        md_files = seed_offline_fixtures(_FIXTURES_DIR)

    assert md_files, "seed 未返回任何样例 markdown"
    cache_dir = fresh_corpora / "fulltext"
    for md in md_files:
        sha = hashlib.sha256(md.read_bytes()).hexdigest()
        cached = cache_dir / f"{sha}.md"
        assert cached.exists(), f"缓存缺失: {cached}"
        # 缓存内容应与源文件逐字节一致（sha256 自洽）
        assert cached.read_bytes() == md.read_bytes()


def test_seed_offline_fixtures_missing_dir(tmp_path):
    """目录不存在 → 抛 FileNotFoundError（脚本据此报 BLOCKED 退出）。"""
    with patch("app.config.settings.corpora_dir", str(tmp_path / "corpora")):
        with pytest.raises(FileNotFoundError):
            seed_offline_fixtures(tmp_path / "no_such_dir")


@pytest.mark.asyncio
async def test_offline_fixtures_ingest_cache_hit_and_traceable(session, tmp_path):
    """fresh corpora（无缓存）→ seed → ingest 走缓存命中 → 语料溯源集合与 seed 哈希一致。

    这是 --offline-fixtures 模式的核心保证：无 MinerU 也能产出含可溯源证据的入选语料，
    content_sha256 = Attachment.sha256 = seed 内容哈希，verify_runlog 溯源校验可对上。
    """
    fresh_corpora = tmp_path / "corpora"  # fresh：无任何已缓存 markdown
    with patch("app.config.settings.corpora_dir", str(fresh_corpora)):
        # 1. seed 样例 markdown 进缓存
        md_files = seed_offline_fixtures(_FIXTURES_DIR)
        seed_hashes = {
            hashlib.sha256(md.read_bytes()).hexdigest() for md in md_files
        }

        # 2. ingest 这些 md（应全部走缓存命中分支，status=="cached"，绝不触 MinerU）
        results = await ingest_pdfs(paths=md_files, language="en", session=session)
        assert len(results) == len(md_files)
        assert all(r["status"] == "cached" for r in results), (
            f"应全部缓存命中，实为: {[r['status'] for r in results]}"
        )
        valid = [r for r in results if r.get("paper_id") is not None]
        assert len(valid) == len(md_files)

        # 3. 建 project + included
        project = await create_project(session, {
            "name": "offline-fixtures-test",
            "research_question": "demo",
            "description": "P3-5 offline fixtures 测试",
        })
        for order, r in enumerate(valid):
            pp = await add_paper_to_project(
                session, project_id=project.id, paper_id=r["paper_id"],
                added_by="agent", order=order,
            )
            await set_inclusion(session, pp.id, "included")

        # 4. load_project_corpus：records 的 content_sha256 集合 == seed 文件哈希集合
        paper_markdowns, records, skipped = await load_project_corpus(session, project.id)

    assert len(records) == len(md_files), f"records 数不符，skipped={skipped}"
    corpus_hashes = {r["content_sha256"] for r in records if r.get("content_sha256")}
    assert corpus_hashes == seed_hashes, (
        "语料 content_sha256 集合应与 seed 文件内容哈希严格一致（溯源校验真集合）"
    )
    # 每篇都应有非空 markdown（无 MinerU 也喂了真实正文）
    assert all(pm["markdown"].strip() for pm in paper_markdowns)
