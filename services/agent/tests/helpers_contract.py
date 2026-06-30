"""Shared synthetic contract samples for structure/provenance tests.

The samples intentionally live as code, not committed fixture/output files. That keeps
the public tree free of standalone demo/test data while preserving deterministic tests.
"""
from __future__ import annotations

import copy
import hashlib
from typing import Any


SAMPLE_FULL_MD = """# Deep Learning Approaches for Bibliometric Network Analysis

Jane Doe, Richard Roe, and Mary Major

## Abstract

This study investigates the application of graph neural networks to large-scale bibliometric citation networks. We propose a scalable embedding method that captures both structural and semantic signals from scholarly corpora, and we evaluate it across three benchmark datasets covering the period from 2010 to 2022.

## 1 Introduction

Bibliometric analysis has become a cornerstone of research evaluation, enabling scholars to map the intellectual structure of a field. Traditional approaches rely on co-citation and bibliographic coupling, but they struggle to scale to corpora containing millions of documents. Recent advances in representation learning offer a promising alternative.

In this paper we develop a unified framework that combines citation topology with textual abstracts. Our contributions are threefold: a new sampling strategy, a hybrid loss function, and an empirical comparison against four established baselines.

## 2 Methods

We model each corpus as a directed graph in which nodes represent documents and edges represent citations. Node features are derived from TF-IDF vectors of the abstract text, while edge weights encode the recency of each citation. The encoder is a two-layer attention network trained with a contrastive objective.

The following table summarizes the three benchmark datasets used in our experiments and their key descriptive statistics.

<table><tr><td>Benchmark Dataset Summary</td><td>Documents</td><td colspan="2">Coverage</td></tr><tr><td>BiblioNet</td><td>120000</td><td>2010</td><td>2018</td></tr><tr><td>CiteSeerX</td><td>340000</td><td>2012</td><td>2022</td></tr></table>

## 3 Results

Across all three datasets our method improves link-prediction accuracy by an average of 7.4 percentage points over the strongest baseline. The gains are most pronounced on the sparsest graph, where structural signals alone are insufficient and the textual channel contributes substantially.

## 4 Conclusion

We have presented a scalable embedding approach for bibliometric networks that jointly exploits citation structure and document semantics. Future work will explore temporal dynamics and cross-lingual corpora.
"""

SAMPLE_CONTENT_LIST: list[dict[str, Any]] = [
    {"type": "text", "text": "Deep Learning Approaches for Bibliometric Network Analysis", "text_level": 1, "page_idx": 0, "bbox": [72.0, 90.0, 523.0, 120.0]},
    {"type": "text", "text": "Jane Doe, Richard Roe, and Mary Major", "text_level": None, "page_idx": 0, "bbox": [72.0, 130.0, 400.0, 148.0]},
    {"type": "text", "text": "Abstract", "text_level": 2, "page_idx": 0, "bbox": [72.0, 160.0, 200.0, 178.0]},
    {"type": "text", "text": "This study investigates the application of graph neural networks to large-scale bibliometric citation networks. We propose a scalable embedding method that captures both structural and semantic signals from scholarly corpora, and we evaluate it across three benchmark datasets covering the period from 2010 to 2022.", "text_level": None, "page_idx": 0, "bbox": [72.0, 185.0, 523.0, 250.0]},
    {"type": "text", "text": "1 Introduction", "text_level": 2, "page_idx": 0, "bbox": [72.0, 290.0, 240.0, 308.0]},
    {"type": "text", "text": "Bibliometric analysis has become a cornerstone of research evaluation, enabling scholars to map the intellectual structure of a field. Traditional approaches rely on co-citation and bibliographic coupling, but they struggle to scale to corpora containing millions of documents. Recent advances in representation learning offer a promising alternative.", "text_level": None, "page_idx": 0, "bbox": [72.0, 320.0, 523.0, 400.0]},
    {"type": "text", "text": "In this paper we develop a unified framework that combines citation topology with textual abstracts. Our contributions are threefold: a new sampling strategy, a hybrid loss function, and an empirical comparison against four established baselines.", "text_level": None, "page_idx": 0, "bbox": [72.0, 420.0, 523.0, 480.0]},
    {"type": "text", "text": "2 Methods", "text_level": 2, "page_idx": 0, "bbox": [72.0, 520.0, 220.0, 538.0]},
    {"type": "text", "text": "We model each corpus as a directed graph in which nodes represent documents and edges represent citations. Node features are derived from TF-IDF vectors of the abstract text, while edge weights encode the recency of each citation. The encoder is a two-layer attention network trained with a contrastive objective.", "text_level": None, "page_idx": 1, "bbox": [72.0, 90.0, 523.0, 170.0]},
    {"type": "text", "text": "The following table summarizes the three benchmark datasets used in our experiments and their key descriptive statistics.", "text_level": None, "page_idx": 1, "bbox": None},
    {"type": "table", "table_body": "<table><tr><td>Benchmark Dataset Summary</td><td>Documents</td><td colspan=\"2\">Coverage</td></tr><tr><td>BiblioNet</td><td>120000</td><td>2010</td><td>2018</td></tr><tr><td>CiteSeerX</td><td>340000</td><td>2012</td><td>2022</td></tr></table>", "text_level": None, "page_idx": 1, "bbox": [72.0, 200.0, 523.0, 300.0]},
    {"type": "text", "text": "3 Results", "text_level": 2, "page_idx": 1, "bbox": [72.0, 318.0, 220.0, 336.0]},
    {"type": "text", "text": "Across all three datasets our method improves link-prediction accuracy by an average of 7.4 percentage points over the strongest baseline. The gains are most pronounced on the sparsest graph, where structural signals alone are insufficient and the textual channel contributes substantially.", "text_level": None, "page_idx": 1, "bbox": [72.0, 340.0, 523.0, 420.0]},
    {"type": "text", "text": "4 Conclusion", "text_level": 2, "page_idx": 1, "bbox": [72.0, 438.0, 220.0, 456.0]},
    {"type": "text", "text": "We have presented a scalable embedding approach for bibliometric networks that jointly exploits citation structure and document semantics. Future work will explore temporal dynamics and cross-lingual corpora.", "text_level": None, "page_idx": 1, "bbox": [72.0, 460.0, 523.0, 540.0]},
    {"type": "page_number", "text": "1", "page_idx": 0, "bbox": [300.0, 760.0, 312.0, 772.0]},
]


def contract_full_markdown() -> str:
    return SAMPLE_FULL_MD


def contract_content_list() -> list[dict[str, Any]]:
    return copy.deepcopy(SAMPLE_CONTENT_LIST)


def contract_markdown_sha256() -> str:
    return hashlib.sha256(SAMPLE_FULL_MD.encode("utf-8")).hexdigest()


def contract_structure_payload(
    paper_id: int = 10,
    attachment_id: int = 50,
    source_pdf_sha256: str = "0" * 64,
) -> dict[str, Any]:
    from app.structure.blocks import content_list_to_blocks
    from app.structure.page_map import build_block_line_ranges, build_line_page_map
    from app.structure.tables import content_list_to_tables

    content_list = contract_content_list()
    page_map = build_line_page_map(SAMPLE_FULL_MD, content_list)
    ranges = build_block_line_ranges(SAMPLE_FULL_MD, content_list)
    return {
        "paper_id": paper_id,
        "attachment_id": attachment_id,
        "page_count": page_map["total_pages"],
        "blocks": [
            block.model_dump()
            for block in content_list_to_blocks(content_list, page_map, ranges)
        ],
        "tables": [
            table.model_dump()
            for table in content_list_to_tables(content_list, page_map)
        ],
        "has_bbox": any(block.get("bbox") for block in content_list),
        "markdown_sha256": contract_markdown_sha256(),
        "schema_version": 1,
        "source_pdf_sha256": source_pdf_sha256,
        "bbox_coord_space": "mineru_1000",
        "page_width": None,
        "page_height": None,
        "rotation": None,
    }


def contract_review_with_provenance() -> dict[str, Any]:
    return {
        "review_md": (
            "## Evidence-backed review\n\n"
            "The method improves link prediction "
            "[[anchor:a10_12_0__occ0]][1][[/anchor]], and the text channel helps most "
            "on sparse graphs [[anchor:a10_12_1__occ1]][1][[/anchor]]."
        ),
        "provenance_map": {
            "a10_12_0__occ0": {
                "paper_id": 10,
                "attachment_id": 50,
                "page_no": 2,
                "block_idx": 12,
                "bbox": [72.0, 340.0, 523.0, 420.0],
                "table_idx": None,
                "cell_row": None,
                "cell_col": None,
                "section_title": "3 Results",
                "quote": "Across all three datasets our method improves link-prediction accuracy by an average of 7.4 percentage points over the strongest baseline.",
            },
            "a10_12_1__occ1": {
                "paper_id": 10,
                "attachment_id": 50,
                "page_no": 2,
                "block_idx": 12,
                "bbox": [72.0, 340.0, 523.0, 420.0],
                "table_idx": None,
                "cell_row": None,
                "cell_col": None,
                "section_title": "3 Results",
                "quote": "The gains are most pronounced on the sparsest graph, where structural signals alone are insufficient and the textual channel contributes substantially.",
            },
        },
    }
