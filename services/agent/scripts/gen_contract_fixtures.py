#!/usr/bin/env python
"""生成前后端共享的契约样例。

产物落在 packages/contracts/fixtures/*.json，作为前端 dev/e2e 与后端契约测试的
单一真源，随仓库提交。后端 schema 变更时重跑本脚本，前端自动消费最新 JSON。

用法：
  cd services/agent && .venv/bin/python scripts/gen_contract_fixtures.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_SERVICE_DIR = _SCRIPT_DIR.parent
for p in (_SERVICE_DIR, _SERVICE_DIR / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

_OUT_DIR = _SERVICE_DIR.parent.parent / "packages" / "contracts" / "fixtures"
_PDF_SHA256 = "0" * 64


def _write(name: str, payload: dict[str, Any]) -> None:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    (_OUT_DIR / name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _sample() -> tuple[str, list[dict[str, Any]]]:
    from helpers_contract import contract_content_list, contract_full_markdown

    return contract_full_markdown(), contract_content_list()


def gen_markdown() -> dict[str, Any]:
    full_md, _ = _sample()
    payload = {
        "markdown": full_md,
        "length": len(full_md),
        "truncated": False,
        "sha256": hashlib.sha256(full_md.encode("utf-8")).hexdigest(),
    }
    _write("sample_markdown.json", payload)
    print(f"[a] sample_markdown.json 写出: length={payload['length']}", flush=True)
    return payload


def gen_structure() -> dict[str, Any]:
    from app.schemas import StructureResponse
    from app.structure.blocks import content_list_to_blocks
    from app.structure.page_map import build_block_line_ranges, build_line_page_map
    from app.structure.tables import content_list_to_tables

    full_md, content_list = _sample()
    page_map = build_line_page_map(full_md, content_list)
    ranges = build_block_line_ranges(full_md, content_list)
    payload = StructureResponse(
        paper_id=10,
        attachment_id=50,
        page_count=max((int(b.get("page_idx", 0)) + 1 for b in content_list), default=1),
        blocks=content_list_to_blocks(content_list, page_map, ranges),
        tables=content_list_to_tables(content_list, page_map),
        has_bbox=any(b.get("bbox") for b in content_list),
        markdown_sha256=hashlib.sha256(full_md.encode("utf-8")).hexdigest(),
        schema_version=1,
        source_pdf_sha256=_PDF_SHA256,
        bbox_coord_space="mineru_1000",
    ).model_dump()
    _write("sample_structure.json", payload)
    print(f"[b] sample_structure.json 写出: blocks={len(payload['blocks'])}", flush=True)
    return payload


def gen_review() -> dict[str, Any]:
    from helpers_contract import contract_review_with_provenance

    payload = contract_review_with_provenance()
    _write("sample_review_with_provenance.json", payload)
    print(f"[c] sample_review_with_provenance.json 写出: provenance={len(payload['provenance_map'])}", flush=True)
    return payload


def _research_payload() -> dict[str, Any]:
    thresholds = {"reverse_hit_high": 25, "reverse_hit_low": 3}
    v2 = {
        "gap_id": "g2", "verdict": "valuable", "score": 0.86, "thresholds": thresholds,
        "rationale": "反向检索仅 2 篇强相关（≤ 阈值 3），且 conceptual 网络中两核心概念存在共现断层（structural_hole=true）→ 判定为真空白、有研究价值。",
        "decided_by": "deterministic",
    }
    v4 = {
        "gap_id": "g4", "verdict": "valuable", "score": 0.79, "thresholds": thresholds,
        "rationale": "反向检索 1 篇强相关（≤ 阈值 3），且 conceptual 网络存在共现断层 → 真空白；已经人工 accept 定稿。",
        "decided_by": "deterministic",
    }
    v3 = {
        "gap_id": "g3", "verdict": "likely_filled", "score": 0.22, "thresholds": thresholds,
        "rationale": "反向检索命中 41 篇强相关（≥ 阈值 25）→ 该方向已有大量研究，疑为检索不全造成的伪空白，价值存疑。",
        "decided_by": "deterministic",
    }
    v5 = {
        "gap_id": "g5", "verdict": "inconclusive", "score": None, "thresholds": thresholds,
        "rationale": "反向检索命中 11 篇（介于阈值 3–25 之间），且未检出明确共现断层 → 证据不足以判定，建议人工复核。",
        "decided_by": "deterministic",
    }
    e2 = {
        "gap_id": "g2",
        "reverse_search": {"query": "MD&A 语气 语义嵌入 跨行业 对照", "provider": "sciverse", "hit_count": 2, "top_hits": [
            {"title": "Embedding-based tone measurement in annual reports", "year": 2023, "doi": "10.1016/j.jacc.2023.0142", "relevance": 0.41},
            {"title": "语义向量与年报语气的单行业证据", "year": None, "doi": None, "relevance": 0.33},
        ]},
        "biblio_structure": {"metric": "cooccurrence_gap", "value": 0.12, "interpretation": "「语义嵌入语气」与「跨行业对照」两概念在共现网络中几乎不相邻（共现强度 0.12，低于断层阈值），存在结构洞。", "source_view": "conceptual"},
        "gathered_by": "subagent", "skipped": [],
    }
    e3 = {
        "gap_id": "g3",
        "reverse_search": {"query": "语气操纵 真实盈余管理 替代关系 理论框架", "provider": "openalex", "hit_count": 41, "top_hits": [
            {"title": "Tone management as a substitute for real earnings management", "year": 2021, "doi": "10.2308/accr-52910", "relevance": 0.74},
            {"title": "Narrative manipulation and earnings quality: a review", "year": 2022, "doi": "10.1111/1475-679X.12410", "relevance": 0.69},
            {"title": "语气操纵与盈余管理替代的经验证据", "year": 2020, "doi": None, "relevance": 0.61},
        ]},
        "biblio_structure": {"metric": "low_coupling", "value": 0.58, "interpretation": "两概念耦合度中等（0.58），非显著断层；结合高命中，结构未支持「真空白」。", "source_view": "conceptual"},
        "gathered_by": "subagent", "skipped": [],
    }
    e5 = {
        "gap_id": "g5",
        "reverse_search": {"query": "文本语气异常 盈余质量 预警 中小板", "provider": "openalex", "hit_count": 11, "top_hits": [
            {"title": "Abnormal tone and earnings quality signals", "year": 2022, "doi": "10.1016/j.jcorpfin.2022.102233", "relevance": 0.52},
            {"title": "中小板公司文本语气与盈余质量", "year": None, "doi": None, "relevance": 0.4},
        ]},
        "biblio_structure": {"metric": "low_coupling", "value": 0.44, "interpretation": "耦合度 0.44，未检出明确共现断层；结构证据不足以单独支持判定。", "source_view": None},
        "gathered_by": "subagent", "skipped": [{"reason": "OpenAlex 近 5 年过滤后候选不足，已跳过补充检索"}],
    }
    e4 = {
        "gap_id": "g4",
        "reverse_search": {"query": "文本语气异常 盈余质量 预警 中小板 有效性", "provider": "sciverse", "hit_count": 1, "top_hits": [
            {"title": "Textual tone anomaly as an earnings-quality early warning", "year": 2024, "doi": "10.1016/j.bar.2024.101355", "relevance": 0.38},
        ]},
        "biblio_structure": {"metric": "cooccurrence_gap", "value": 0.09, "interpretation": "「文本语气异常」与「中小板盈余质量」两概念在共现网络中近乎不相邻（0.09），存在显著结构洞。", "source_view": "conceptual"},
        "gathered_by": "subagent", "skipped": [],
    }
    g1 = {"gap_id": "g1", "theme": "MD&A 文本特征与信息含量", "statement": "MD&A 文本可读性与分析师预测分歧的关系，在高科技行业情境下尚未被系统检验。", "lens": "concept", "supporting_papers": [{"paper_id": 12, "anchor_id": "p12_b3__occ1", "quote": "可读性较低的 MD&A 与更大的分析师预测分歧相关。"}], "counter_evidence": [], "confidence": 0.62, "status": "draft", "value_verdict": None}
    g2 = {"gap_id": "g2", "theme": "MD&A 文本特征与信息含量", "statement": "基于深度语义嵌入度量 MD&A 语气的方法，尚未与传统词典法做跨行业对照。", "lens": "method", "supporting_papers": [{"paper_id": 7, "anchor_id": "p7_b9__occ1", "quote": "现有研究多依赖 LM 词典统计语气。"}, {"paper_id": 23, "anchor_id": "p23_b2__occ1", "quote": "嵌入式语义度量在单行业样本上表现更优。"}], "counter_evidence": [{"paper_id": 31, "anchor_id": "p31_b5__occ1", "note": "个别研究已尝试嵌入法，但样本受限于单一行业。"}], "confidence": 0.71, "status": "verified", "value_verdict": v2}
    g3 = {"gap_id": "g3", "theme": "盈余管理识别与文本语气", "statement": "管理层语气操纵与真实盈余管理之间的替代关系，缺乏统一的理论框架。", "lens": "theory", "supporting_papers": [{"paper_id": 4, "anchor_id": "p4_b1__occ1", "quote": "语气操纵可能替代应计盈余管理。"}], "counter_evidence": [], "confidence": 0.55, "status": "verified", "value_verdict": v3}
    g4 = {"gap_id": "g4", "theme": "盈余管理识别与文本语气", "statement": "文本语气异常作为盈余质量预警指标，在中小板样本中的有效性尚未被评估。", "lens": "concept", "supporting_papers": [{"paper_id": 9, "anchor_id": "p9_b7__occ1", "quote": "语气异常与后续盈余下调存在相关性。"}], "counter_evidence": [], "confidence": 0.68, "status": "accepted", "value_verdict": v4}
    g5 = {"gap_id": "g5", "theme": "盈余管理识别与文本语气", "statement": "用文本语气异常构建盈余质量预警模型的方法，在中小板样本上的稳健性未被验证。", "lens": "method", "supporting_papers": [{"paper_id": 9, "anchor_id": "p9_b11__occ1", "quote": "现有预警模型多基于财务比率，少有纳入文本语气。"}], "counter_evidence": [], "confidence": 0.48, "status": "draft", "value_verdict": None}
    all_gaps = [g1, g2, g3, g4, g5]
    r2, r3, r4, r5 = (
        {"gap_id": "g2", "verdict": v2, "evidence": e2},
        {"gap_id": "g3", "verdict": v3, "evidence": e3},
        {"gap_id": "g4", "verdict": v4, "evidence": e4},
        {"gap_id": "g5", "verdict": v5, "evidence": e5},
    )
    feasibility_verdict = {
        "gap_id": "g2", "verdict": "buildable", "data_status": "available",
        "method_status": "supported", "resource_status": "modest",
        "rationale": "数据有明确可访问证据、方法组件基座 ≥2 条且非重资源 → 方向可做（buildable）。",
        "decided_by": "deterministic",
        "signals": {
            "data_status": "available", "method_status": "supported", "resource_status": "modest",
            "dataset_count": 1, "open_dataset_count": 1, "dedup_building_blocks": 2,
            "method_query_suspected": False, "negative_kinds": [],
        },
    }
    feasibility_pack = {
        "gap_id": "g2",
        "data_availability": {
            "query": "上市公司年报 MD&A 开放数据", "provider": "openalex",
            "datasets": [{
                "name": "巨潮资讯年报语料", "source": "巨潮资讯公开披露",
                "url": "https://www.cninfo.com.cn/", "access": "open", "kind": "annual_reports",
            }],
        },
        "method_base": {
            "query": "文本嵌入 语气测量",
            "building_blocks": [
                {"kind": "measurement", "name": "Sentence-BERT embeddings", "doi": "10.18653/v1/D19-1410", "has_code": True},
                {"kind": "baseline", "name": "Loughran-McDonald dictionary", "doi": "10.1111/j.1540-6261.2010.01625.x", "has_code": True},
            ],
        },
        "resource_scale": {
            "scale_flag": "modest", "typical_sample_size": "10k-50k reports",
            "typical_compute": "single GPU or CPU batch inference", "note": "可按行业分批处理。",
        },
        "negative_evidence": [],
        "notes": ["开放年报可直接下载，方法组件均有公开实现。"],
        "skipped": [],
    }
    feasibility_result = {"gap_id": "g2", "verdict": feasibility_verdict, "pack": feasibility_pack}
    rid = "run_gap_001"
    return {
        "FIXTURE_PID": 5, "FIXTURE_CID": "rc_mda_001", "FIXTURE_RUN_ID": rid,
        "FIXTURE_VERIFY_RUN_ID": "run_verify_001", "FIXTURE_FEASIBILITY_RUN_ID": "run_feasibility_001",
        "THRESHOLDS": thresholds,
        "verdictValuableG2": v2, "verdictValuableG4": v4, "verdictLikelyFilledG3": v3, "verdictInconclusiveG5": v5,
        "evidenceG2": e2, "evidenceG3": e3, "evidenceG5": e5, "evidenceG4": e4,
        "verdictResultG2": r2, "verdictResultG3": r3, "verdictResultG4": r4, "verdictResultG5": r5,
        "ALL_VERDICT_RESULTS": [r2, r3, r4, r5],
        "feasibilityVerdictG2": feasibility_verdict, "feasibilityPackG2": feasibility_pack,
        "feasibilityResultG2": feasibility_result,
        "gapDraftConcept": g1, "gapVerifiedMethod": g2, "gapVerifiedTheory": g3, "gapAcceptedConcept": g4, "gapDraftMethod": g5,
        "ALL_GAPS": all_gaps,
        "scratchpadState": {"run_id": rid, "run_status": "done", "entries": all_gaps, "updated_at": "2026-06-16T03:14:07Z"},
        "discoverAccepted": {"run_id": rid}, "verifyAccepted": {"verify_run_id": "run_verify_001"},
        "feasibilityAccepted": {"feasibility_run_id": "run_feasibility_001"},
        "SCRATCHPAD_TICKS": [
            {"run_id": rid, "run_status": "running", "entries": [g1], "updated_at": "2026-06-16T03:14:01Z"},
            {"run_id": rid, "run_status": "running", "entries": [g1, g5], "updated_at": "2026-06-16T03:14:03Z"},
            {"run_id": rid, "run_status": "running", "entries": [g1, g2, g5], "updated_at": "2026-06-16T03:14:05Z"},
            {"run_id": rid, "run_status": "done", "entries": all_gaps, "updated_at": "2026-06-16T03:14:07Z"},
        ],
    }


def gen_research() -> dict[str, Any]:
    from app.agent.scratchpad import GapCandidate

    payload = _research_payload()
    for gap in payload["ALL_GAPS"]:
        GapCandidate.from_dict(gap)
    _write("research_gap.json", payload)
    print(f"[d] research_gap.json 写出: gaps={len(payload['ALL_GAPS'])}", flush=True)
    return payload


def gen_project() -> dict[str, Any]:
    from app.schemas import ProjectDetail

    payload = ProjectDetail(
        id=5,
        name="契约样例项目",
        researchQuestion="graph neural networks for bibliometric network analysis",
        description="用于前后端契约联调的项目详情 fixture",
        paperCount=12,
        includedCount=3,
        readableFulltextCount=2,
        activeCorpus=None,
        latestCorpus={
            "corpusId": 77,
            "rCorpusId": None,
            "status": "failed",
            "documentCount": 3,
            "contentHash": "sha256:contract-fixture",
            "errorReason": "mock R parse failure",
            "createdAt": "2026-06-16T03:15:00Z",
        },
    ).model_dump()
    _write("project_detail.json", payload)
    print("[e] project_detail.json 写出: latestCorpus=failed readableFulltextCount=2", flush=True)
    return payload


def gen_from_search() -> dict[str, Any]:
    from app.schemas import FromSearchFailedItem, FromSearchResult

    payload = FromSearchResult(
        imported=1,
        skipped=1,
        failed=[
            FromSearchFailedItem(candidateId="integrity-1", title="Integrity Fail Paper", reason="数据库冲突: UNIQUE constraint failed: paper.doi"),
            FromSearchFailedItem(candidateId=None, title="Untitled broken candidate", reason="候选缺少可入库的外部标识，已隔离失败"),
        ],
        failedCount=2,
        paperIds=[101, 102],
    ).model_dump()
    _write("from_search_result.json", payload)
    print("[f] from_search_result.json 写出: failedCount=2", flush=True)
    return payload


def main() -> None:
    gen_markdown()
    gen_structure()
    gen_review()
    gen_research()
    gen_project()
    gen_from_search()
    print("\n[DONE] 共享契约 fixtures 均已落盘。", flush=True)


if __name__ == "__main__":
    main()
