#!/usr/bin/env python
"""SLR 全文综述端到端运行脚本 — 阶段 5-3a

用法示例：
  # 最简：使用默认 ZIP 路径、默认 12 篇、默认主题
  services/agent/.venv/bin/python scripts/run_slr_e2e.py

  # 指定 ZIP 包和输出路径
  services/agent/.venv/bin/python scripts/run_slr_e2e.py \\
      --zip /data/slr_papers.zip \\
      --out /tmp/review_output.md

  # 完整参数
  services/agent/.venv/bin/python scripts/run_slr_e2e.py \\
      --zip /data/analyst_papers.zip \\
      --max-papers 12 \\
      --exclude Baker_2022 Callaway_2021 Goodman_Bacon Sun_Abraham \\
      --topic "分析师跟踪/盈余预测的影响因素与经济后果（中国资本市场）" \\
      --out /tmp/slr_review.md \\
      --language en \\
      --concurrency 4

流程：
  1. 解压 ZIP 到临时工作目录（或 --work-dir 指定目录）
  2. 过滤 --exclude 文件名关键词
  3. 按 --max-papers 截取 PDF 子集
  4. 逐篇 ingest_pdf（幂等：已有 markdown 跳过，便于断点续跑）
  5. 建 Project + 全部 included（幂等）
  6. 取各 paper 的 markdown + 题录
  7. run_review（map/reduce）
  8. 写综述 md 到 --out
  9. 打印 stats（含耗时、各篇状态、引用校验结果）

健壮性：
  - MinerU 单篇失败/超时 → 记录失败清单，继续其他篇
  - 全程打印进度，便于后台监控
  - max-papers 默认 12，保护 MinerU 日配额（约 1000 页）

注意：本脚本写好供手动触发，真实 MinerU / 真实 LLM 调用由用户控制。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
import zipfile
from pathlib import Path

# ---------------------------------------------------------------------------
# 把 services/agent 加入 sys.path（脚本直接运行时需要）
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_SERVICE_DIR = _SCRIPT_DIR.parent
if str(_SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICE_DIR))

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("slr_e2e")


# ---------------------------------------------------------------------------
# 默认常量
# ---------------------------------------------------------------------------
DEFAULT_ZIP = "/data/slr_papers.zip"
DEFAULT_MAX_PAPERS = 100
DEFAULT_EXCLUDE_KEYWORDS = [
    "Baker_2022",
    "Callaway_2021",
    "Goodman_Bacon",
    "Sun_Abraham",
]
DEFAULT_TOPIC = "分析师跟踪/盈余预测的影响因素与经济后果（中国资本市场）"
DEFAULT_OUT = "/tmp/slr_review.md"
DEFAULT_WORK_DIR = "/tmp/slr_e2e_workdir"


# ---------------------------------------------------------------------------
# 命令行参数
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_slr_e2e.py",
        description="SLR 全文综述端到端脚本（BiblioCN 阶段 5-3a）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--zip",
        default=DEFAULT_ZIP,
        help=f"PDF 包 zip 文件路径（默认: {DEFAULT_ZIP}）",
    )
    parser.add_argument(
        "--max-papers",
        type=int,
        default=DEFAULT_MAX_PAPERS,
        help=f"最多摄取论文篇数，保护 MinerU 日配额（默认: {DEFAULT_MAX_PAPERS}）",
    )
    parser.add_argument(
        "--exclude",
        nargs="*",
        default=DEFAULT_EXCLUDE_KEYWORDS,
        help=(
            "排除文件名含这些关键词的 PDF（默认排除 DID 方法论文: "
            + " ".join(DEFAULT_EXCLUDE_KEYWORDS)
            + "）"
        ),
    )
    parser.add_argument(
        "--topic",
        default=DEFAULT_TOPIC,
        help=f"综述研究主题（默认: {DEFAULT_TOPIC!r}）",
    )
    parser.add_argument(
        "--out",
        default=DEFAULT_OUT,
        help=f"综述 Markdown 输出路径（默认: {DEFAULT_OUT}）",
    )
    parser.add_argument(
        "--work-dir",
        default=DEFAULT_WORK_DIR,
        help=f"PDF 解压工作目录（默认: {DEFAULT_WORK_DIR}）",
    )
    parser.add_argument(
        "--language",
        default="en",
        choices=["en", "ch"],
        help="MinerU 语言参数（默认: en）",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="map 阶段 LLM 并发数（默认: 4）",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def extract_zip_to_workdir(zip_path: Path, work_dir: Path) -> list[Path]:
    """解压 ZIP 中的 PDF 到工作目录，返回所有 PDF 路径列表。"""
    work_dir.mkdir(parents=True, exist_ok=True)
    pdf_paths: list[Path] = []

    logger.info(f"[解压] {zip_path} → {work_dir}")
    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            if name.lower().endswith(".pdf"):
                # 只提取 PDF，展平到 work_dir（丢弃子目录结构）
                filename = Path(name).name
                out_path = work_dir / filename
                if not out_path.exists():
                    zf.extract(name, work_dir)
                    # 若解压到了子目录，移到 work_dir 根
                    extracted = work_dir / name
                    if extracted != out_path and extracted.exists():
                        extracted.rename(out_path)
                pdf_paths.append(out_path)

    logger.info(f"[解压] 共 {len(pdf_paths)} 个 PDF")
    return pdf_paths


def filter_pdfs(
    pdf_paths: list[Path],
    exclude_keywords: list[str],
    max_papers: int,
) -> list[Path]:
    """过滤 + 截取 PDF 列表。"""
    filtered: list[Path] = []
    excluded: list[str] = []

    for p in pdf_paths:
        name = p.name
        if any(kw.lower() in name.lower() for kw in exclude_keywords):
            excluded.append(name)
        else:
            filtered.append(p)

    if excluded:
        logger.info(f"[过滤] 排除 {len(excluded)} 篇（含关键词）: {excluded}")

    selected = filtered[:max_papers]
    logger.info(f"[过滤] 选取 {len(selected)}/{len(filtered)} 篇（max={max_papers}）")
    return selected


def _find_markdown_for_sha256(sha256: str, corpora_dir: str) -> Path | None:
    """按 sha256 查找已有 Markdown 文件（幂等判断）。"""
    md_path = Path(corpora_dir) / "fulltext" / f"{sha256}.md"
    return md_path if md_path.exists() else None


# ---------------------------------------------------------------------------
# 核心异步流程
# ---------------------------------------------------------------------------

async def run_pipeline(args: argparse.Namespace) -> int:
    """主管线，返回退出码（0=成功，1=失败）。"""

    # ------------------------------------------------------------------
    # 0. 导入依赖（延迟到运行时，避免 import 时触发配置问题）
    # ------------------------------------------------------------------
    from app.config import settings
    from app.db import SessionLocal
    from app.ingest.fulltext import ingest_pdfs, _sha256_of_file
    from app.repositories.project import (
        create_project,
        add_paper_to_project,
        set_inclusion,
        list_project_papers,
    )
    from app.repositories.library import get_by_id
    from app.review.orchestrate import run_review

    zip_path = Path(args.zip)
    work_dir = Path(args.work_dir)
    out_path = Path(args.out)
    topic = args.topic
    language = args.language
    concurrency = args.concurrency
    max_papers = args.max_papers
    exclude_keywords = args.exclude or []

    t_start = time.monotonic()

    # ------------------------------------------------------------------
    # 1. 解压 ZIP
    # ------------------------------------------------------------------
    if not zip_path.exists():
        logger.error(f"ZIP 文件不存在: {zip_path}")
        print(f"\n[BLOCKED] ZIP 文件不存在: {zip_path}", flush=True)
        return 1

    all_pdfs = extract_zip_to_workdir(zip_path, work_dir)
    selected_pdfs = filter_pdfs(all_pdfs, exclude_keywords, max_papers)

    if not selected_pdfs:
        logger.error("过滤后无可用 PDF，退出。")
        print("\n[BLOCKED] 过滤后无可用 PDF", flush=True)
        return 1

    # ------------------------------------------------------------------
    # 2. 批量 ingest（一次 MinerU 批，服务端并行；已有 Markdown 的自动跳过）
    # ------------------------------------------------------------------
    print(f"\n{'='*60}", flush=True)
    print(f"[阶段 1/3] 批量摄取 PDF ({len(selected_pdfs)} 篇)", flush=True)
    print(f"{'='*60}", flush=True)

    ingest_results: list[dict] = []
    failed_ingest: list[str] = []

    t_ingest_start = time.monotonic()
    async with SessionLocal() as session:
        raw_results = await ingest_pdfs(
            paths=selected_pdfs,
            language=language,
            session=session,
        )

    t_ingest_elapsed = time.monotonic() - t_ingest_start

    # 规范化结果格式（与旧逐篇格式对齐，下游代码不变）
    for r in raw_results:
        status = r.get("status", "failed")
        if status in ("done", "cached"):
            display_status = "skipped（已缓存）" if status == "cached" else "done"
            icon = "→" if status == "cached" else "✓"
            print(
                f"  {icon} {Path(r['pdf_path']).name}: {display_status}"
                f" paper_id={r['paper_id']} md={r['markdown_len']} chars",
                flush=True,
            )
            ingest_results.append({
                "pdf_path": r["pdf_path"],
                "sha256": r.get("sha256", ""),
                "markdown_path": r.get("markdown_path"),
                "status": "done" if status == "done" else "skipped",
                "paper_id": r.get("paper_id"),
                "err": None,
            })
        else:
            err = r.get("err", "unknown")
            print(f"  ✗ {Path(r['pdf_path']).name}: FAILED — {err}", flush=True)
            failed_ingest.append(f"{Path(r['pdf_path']).name}: {err}")
            ingest_results.append({
                "pdf_path": r["pdf_path"],
                "sha256": r.get("sha256", ""),
                "markdown_path": None,
                "status": "failed",
                "paper_id": None,
                "err": err,
            })

    print(
        f"\n  批量摄取完成：{t_ingest_elapsed:.1f}s，"
        f"成功 {sum(1 for r in ingest_results if r['status'] in ('done','skipped'))} 篇，"
        f"失败 {len(failed_ingest)} 篇",
        flush=True,
    )

    if failed_ingest:
        print(f"\n[警告] {len(failed_ingest)} 篇摄取失败（将跳过，继续综述）：", flush=True)
        for f in failed_ingest:
            print(f"  - {f}", flush=True)

    # 过滤出有效的 ingest 结果
    valid_ingests = [r for r in ingest_results if r["status"] in ("done", "skipped")
                     and r["markdown_path"] is not None]

    if not valid_ingests:
        print("\n[BLOCKED] 所有 PDF 摄取均失败，无法生成综述。", flush=True)
        return 1

    # ------------------------------------------------------------------
    # 3. 建 Project + 关联论文（幂等）
    # ------------------------------------------------------------------
    print(f"\n{'='*60}", flush=True)
    print(f"[阶段 2/3] 建 Project + 关联 {len(valid_ingests)} 篇论文", flush=True)
    print(f"{'='*60}", flush=True)

    async with SessionLocal() as session:
        project = await create_project(session, {
            "name": f"SLR: {topic[:40]}",
            "description": f"阶段5-3a e2e 自动建立",
        })
        logger.info(f"Project 建好: id={project.id} name={project.name!r}")
        print(f"  Project id={project.id}", flush=True)

        # 取各篇 paper_id（ingest_pdfs 已直接返回，包括 cached 情形）
        paper_entries: list[dict] = []
        for r in valid_ingests:
            paper_id = r.get("paper_id")
            markdown_path = r["markdown_path"]

            if paper_id is None:
                logger.warning(f"无法获取 paper_id，跳过: {r['pdf_path']}")
                continue

            paper_entries.append({
                "paper_id": paper_id,
                "markdown_path": markdown_path,
            })

        # 关联论文 + 设置 included
        for order, pe in enumerate(paper_entries):
            pp = await add_paper_to_project(
                session,
                project_id=project.id,
                paper_id=pe["paper_id"],
                added_by="slr_e2e",
                order=order,
            )
            await set_inclusion(session, pp.id, "included")

        print(f"  关联 {len(paper_entries)} 篇论文（included）", flush=True)

        # 拉题录 + 读 Markdown
        paper_markdowns: list[dict] = []
        records: list[dict] = []

        for idx, pe in enumerate(paper_entries, start=1):
            paper = await get_by_id(session, pe["paper_id"])
            if paper is None:
                continue

            # 读 Markdown
            md_path = Path(pe["markdown_path"])
            try:
                markdown_text = md_path.read_text(encoding="utf-8")
            except Exception as exc:
                logger.warning(f"读 Markdown 失败: {md_path}: {exc}")
                markdown_text = ""

            # 取作者字符串
            creators = paper.creators or []
            if creators and isinstance(creators[0], dict):
                authors_str = "; ".join(
                    c.get("literal") or f"{c.get('family', '')} {c.get('given', '')}".strip()
                    for c in creators
                )
            else:
                authors_str = str(creators)

            paper_markdowns.append({
                "meta": {
                    "paper_id": str(paper.id),
                    "title": paper.title or "",
                    "authors": authors_str,
                    "year": paper.year,
                },
                "markdown": markdown_text,
            })

            records.append({
                "idx": idx,
                "title": paper.title or "",
                "authors": authors_str,
                "year": str(paper.year or ""),
                "doi": paper.doi or "",
            })

    print(f"  有效论文 {len(paper_markdowns)} 篇，题录 {len(records)} 条", flush=True)

    if not paper_markdowns:
        print("\n[BLOCKED] 没有可处理的论文（markdown 均为空），退出。", flush=True)
        return 1

    # ------------------------------------------------------------------
    # 4. run_review（map + reduce）
    # ------------------------------------------------------------------
    print(f"\n{'='*60}", flush=True)
    print(f"[阶段 3/3] 综述生成（map={concurrency}并发 → reduce流式）", flush=True)
    print(f"主题：{topic}", flush=True)
    print(f"{'='*60}", flush=True)

    result = await run_review(
        topic=topic,
        paper_markdowns=paper_markdowns,
        records=records,
        concurrency=concurrency,
    )

    # ------------------------------------------------------------------
    # 5. 写出综述 md
    # ------------------------------------------------------------------
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(result["review_md"], encoding="utf-8")
    print(f"\n综述已写到: {out_path}（{len(result['review_md'])} 字符）", flush=True)

    # ------------------------------------------------------------------
    # 6. 打印 stats
    # ------------------------------------------------------------------
    stats = result["stats"]
    t_total = time.monotonic() - t_start

    print(f"\n{'='*60}", flush=True)
    print("【运行统计】", flush=True)
    print(f"{'='*60}", flush=True)
    print(f"  输入论文总数    : {stats['total_papers']}", flush=True)
    print(f"  摄取失败        : {len(failed_ingest)}", flush=True)
    print(f"  成功摘要        : {stats['success_summaries']}", flush=True)
    print(f"  失败摘要（占位）: {stats['error_summaries']}", flush=True)
    print(f"  综述字数        : {stats['review_chars']}", flush=True)
    print(f"  有效引用数      : {stats['valid_citations']}", flush=True)
    print(f"  伪造引用数      : {stats['fabricated_citations']}", flush=True)
    print(f"  map 耗时        : {stats['elapsed_map_s']:.1f}s", flush=True)
    print(f"  reduce 耗时     : {stats['elapsed_reduce_s']:.1f}s", flush=True)
    print(f"  脚本总耗时      : {t_total:.1f}s", flush=True)

    # 各篇摄取状态
    print(f"\n【各篇摄取状态】", flush=True)
    for r in ingest_results:
        status_icon = {"done": "✓", "skipped": "→", "failed": "✗"}.get(r["status"], "?")
        print(f"  {status_icon} {Path(r['pdf_path']).name}: {r['status']}"
              + (f" (err: {r['err'][:60]})" if r["err"] else ""), flush=True)

    # 摄取失败清单
    if failed_ingest:
        print(f"\n【摄取失败清单（{len(failed_ingest)} 篇）】", flush=True)
        for f in failed_ingest:
            print(f"  - {f}", flush=True)

    # 引用校验摘要
    vs = result.get("validation_summary", {})
    if vs:
        print(f"\n【引用校验摘要】", flush=True)
        print(f"  总段落数     : {vs.get('total_segments', 0)}", flush=True)
        print(f"  有效引用     : {vs.get('valid_citations', 0)}", flush=True)
        print(f"  伪造引用     : {vs.get('fabricated_citations', 0)}", flush=True)
        fabricated_spans = vs.get("fabricated_spans", [])
        if fabricated_spans:
            print(f"  伪造引用样例 :", flush=True)
            for span in fabricated_spans[:5]:
                print(f"    - {span}", flush=True)

    print(f"\n[DONE] 综述已生成: {out_path}", flush=True)
    return 0


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    exit_code = asyncio.run(run_pipeline(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
