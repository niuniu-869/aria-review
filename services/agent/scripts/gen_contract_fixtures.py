#!/usr/bin/env python
"""生成本地联调用的契约样例。

产出两份落盘 JSON，供 SourceViewer（点击引用 → 跳转原文）针对真实溯源管线
输出做本地验证。产物已被 .gitignore 忽略，不随开源仓库提交：

  (a) fixtures/contract/sample_structure.json
      —— 确定性（无 LLM）：把内置合成 sample_content_list + sample_full.md 经
      build_line_page_map / build_block_line_ranges / content_list_to_blocks /
      content_list_to_tables 转成 StructureResponse 并落盘（schema 忠实，pydantic v2）。

  (b) fixtures/contract/sample_review_with_provenance.json
      —— 真实 LLM（DeepSeek）：用样例论文构造 3 篇语料喂 run_review，落盘
      review_md + provenance_map + validation_summary + stats。review_md 里每个
      已定位引用被包成 [[anchor:<id>]][n][[/anchor]]，<id> 是 provenance_map 的 key
      —— 这就是前端「点 anchor → 查 provenance_map → 跳原文 block/page」依赖的链路。

run_review 直接吃内存参数（paper_markdowns + records），不需要 DB。app/config.py 在
import 时 load_dotenv(services/agent/.env) 自动注入 DEEPSEEK_API_KEY，故脚本直跑即真实
端到端（无须 export key）。

用法：
  cd services/agent && .venv/bin/python scripts/gen_contract_fixtures.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 把 services/agent 加入 sys.path（镜像 scripts/run_agent_e2e.py 的做法）
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_SERVICE_DIR = _SCRIPT_DIR.parent
if str(_SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICE_DIR))

_TESTS_DIR = _SERVICE_DIR / "tests"
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

_CONTRACT_DIR = _SERVICE_DIR / "fixtures" / "contract"
_OUT_STRUCTURE = _CONTRACT_DIR / "sample_structure.json"
_OUT_REVIEW = _CONTRACT_DIR / "sample_review_with_provenance.json"

# 64-hex 占位 PDF sha256（fixtures 无真实 PDF，但 schema 字段需 64-hex 形状）
_PLACEHOLDER_PDF_SHA256 = "0" * 64


def _load_sample() -> tuple[str, list[dict]]:
    """读内置合成 full.md + content_list。"""
    from helpers_contract import contract_content_list, contract_full_markdown

    return contract_full_markdown(), contract_content_list()


def gen_structure_fixture() -> dict:
    """(a) 确定性产出 sample_structure.json，返回落盘的 dict。"""
    from app.schemas import StructureResponse
    from app.structure.blocks import content_list_to_blocks
    from app.structure.page_map import (
        build_block_line_ranges,
        build_line_page_map,
    )
    from app.structure.tables import content_list_to_tables

    _CONTRACT_DIR.mkdir(parents=True, exist_ok=True)
    full_md, cl = _load_sample()

    page_map = build_line_page_map(full_md, cl)
    blr = build_block_line_ranges(full_md, cl)
    blocks = content_list_to_blocks(cl, page_map, blr)
    tables = content_list_to_tables(cl, page_map)

    page_count = max((int(b.get("page_idx", 0)) + 1 for b in cl), default=1)
    has_bbox = any(b.get("bbox") for b in cl)
    markdown_sha256 = hashlib.sha256(full_md.encode()).hexdigest()

    # paper_id/attachment_id 与 review fixture 的第 1 篇（records[0]）对齐：(10, 50)，
    # 使前端「点 review 里 paper 10 的 anchor → 按 attachment_id=50 加载本 structure」
    # 联调链路两份 fixture 跨文件一致（codex B6 P2）。
    resp = StructureResponse(
        paper_id=10,
        attachment_id=50,
        page_count=page_count,
        blocks=blocks,
        tables=tables,
        has_bbox=has_bbox,
        markdown_sha256=markdown_sha256,
        schema_version=1,
        source_pdf_sha256=_PLACEHOLDER_PDF_SHA256,
        bbox_coord_space=("mineru_1000" if has_bbox else None),
    )

    payload = resp.model_dump()
    _OUT_STRUCTURE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"[a] sample_structure.json 写出: blocks={len(payload['blocks'])} "
        f"tables={len(payload['tables'])} page_count={payload['page_count']} "
        f"has_bbox={payload['has_bbox']} → {_OUT_STRUCTURE}",
        flush=True,
    )
    return payload


async def gen_review_fixture() -> dict:
    """(b) 真实 LLM 产出 sample_review_with_provenance.json，返回落盘的 dict。"""
    from app.harness.llm import LLMRouter
    from app.review.orchestrate import run_review

    # 显式要求真实 LLM：本 fixture 必须由真实 DeepSeek 产出（无 key 时 run_review 会走 FakeLLM,
    # 其占位 source_quote 不在样例文档中→空 provenance 保护拦截；此处提前显式校验,
    # 使"真实 LLM"不靠运行环境隐含成立 codex B6 P3）。
    if not LLMRouter.from_config().has_any_key():
        print(
            "[ERROR] 未检测到 LLM key（DEEPSEEK_API_KEY 未配置）。本 fixture 必须由真实 LLM 产出，"
            "请在 services/agent/.env 配置真实 key 后重跑。",
            flush=True,
        )
        sys.exit(5)

    _CONTRACT_DIR.mkdir(parents=True, exist_ok=True)
    full_md, cl = _load_sample()

    # 构造 3 篇语料：同一份样例 full.md / content_list，但 paper_id/idx/title 各异，
    # 让综述模型产出真正的多篇 [n] 引用综述（单篇综述会被模型拒绝/退化）。
    paper_markdowns = [
        {
            "meta": {
                "paper_id": str(10 + i),
                "title": (
                    "Deep Learning Approaches for Bibliometric Network "
                    f"Analysis (variant {i})"
                ),
                "authors": "Doe",
                "year": 2024,
            },
            "markdown": full_md,
            "content_list": cl,
        }
        for i in range(3)
    ]
    records = [
        {
            "idx": i + 1,
            "paper_id": 10 + i,
            "attachment_id": 50 + i,
            "title": paper_markdowns[i]["meta"]["title"],
            "content_sha256": "x",
        }
        for i in range(3)
    ]

    print("[b] 调 run_review（真实 DeepSeek）…", flush=True)
    result = await run_review(
        "graph neural networks for bibliometric network analysis",
        paper_markdowns,
        records,
    )

    # 真实端到端断言：不满足则报错退出，绝不落盘空/无效 fixture（model variance 由调用方重跑）
    if result.get("error") is not None:
        print(f"[ERROR] run_review 返回 error: {result['error']}", flush=True)
        sys.exit(2)
    review_md = result.get("review_md") or ""
    provenance_map = result.get("provenance_map") or {}
    if not provenance_map:
        print(
            "[ERROR] provenance_map 为空（综述未产出任何已定位证据锚点）。"
            "可能 model variance —— 请重跑一次；若持续为空请上报，不要落盘无锚 fixture。",
            flush=True,
        )
        sys.exit(3)
    if "[[anchor:" not in review_md:
        print(
            "[ERROR] review_md 不含 [[anchor: —— 综述未注入 occurrence anchor。"
            "可能 model variance —— 请重跑一次；持续无锚请上报。",
            flush=True,
        )
        sys.exit(4)

    payload = {
        "review_md": review_md,
        "provenance_map": provenance_map,
        "validation_summary": result.get("validation_summary"),
        "stats": result.get("stats"),
    }
    _OUT_REVIEW.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    anchor_ids = re.findall(r"\[\[anchor:([^\]]+)\]\]", review_md)
    linked = [a for a in anchor_ids if a in provenance_map]
    print(
        f"[b] sample_review_with_provenance.json 写出: "
        f"review_md={len(review_md)} 字符, provenance_map={len(provenance_map)} 条, "
        f"review_md 内 anchor 出现 {len(anchor_ids)} 次（{len(linked)} 个映射到 "
        f"provenance_map）→ {_OUT_REVIEW}",
        flush=True,
    )
    if anchor_ids:
        ex_id = linked[0] if linked else anchor_ids[0]
        ex = provenance_map.get(ex_id)
        print(f"[b] 示例 anchor id={ex_id!r} → provenance entry: "
              f"{json.dumps(ex, ensure_ascii=False)[:300]}", flush=True)
    return payload


async def main() -> None:
    gen_structure_fixture()
    await gen_review_fixture()
    print("\n[DONE] 两份 fixtures 均已落盘。", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
