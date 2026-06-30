"""A2 · FileReader 导航原语 —— read_paper 的纯函数底座（零 LLM）。

给 GAP / 价值 subagent 一条"按需翻页取证"的路径（呼应 Anthropic context-engineering），
不再被整篇全文撑爆 context：
  - build_outline    : 章节 → 行区间 + 页标签（复用 structure.tables.parse_outline）
  - read_section     : 按行区间逐字切全文（verbatim，带 max_chars）
  - search_evidence  : 关键词命中 → block_idx/page_no/bbox/section_title + 上下文，
                       坐标一律取自 EvidenceResolver（"可经 EvidenceResolver 回定位"）

全部纯函数，输入 (full_md / content_list / page_map)；逐字保留，绝不改写命中文本。
领域无关：无任何商科/会计词，跨 5 领域（含 2 工程）通用。
"""
from __future__ import annotations

from typing import Any

from .blocks import EvidenceResolver, _INDEXABLE, _strip
from .page_map import page_for_line, page_label_for_range
from .tables import parse_outline


def build_outline(full_md: str, page_map: dict | None = None) -> list[dict]:
    """全文 Markdown → 章节大纲（标题 + 1-based 行区间 + 页标签）。

    复用 structure.tables.parse_outline（纯 markdown heading 扫描）。page_map 存在时
    附 page_no（章节起始行所在页）与 page_label（'第7页'/'第7-9页'）。
    """
    sections = parse_outline(full_md or "")
    out: list[dict] = []
    for sec in sections:
        start, end = int(sec["start_line"]), int(sec["end_line"])
        page_no = page_for_line(page_map, start) if page_map else None
        page_label = page_label_for_range(page_map, start, end) if page_map else ""
        # parse_outline 返回带 '#' 的原始标题行；大纲剥掉 markdown 标记只留章节名（逐字内容不变）。
        raw_title = str(sec.get("title", ""))
        clean_title = raw_title.lstrip("#").strip()
        out.append({
            "title": clean_title,
            "level": int(sec.get("level", 1)),
            "start_line": start,
            "end_line": end,
            "page_no": page_no,
            "page_label": page_label,
        })
    return out


def read_section(
    full_md: str,
    start_line: int,
    end_line: int,
    *,
    max_chars: int = 4000,
    page_map: dict | None = None,
) -> dict:
    """按 1-based 闭区间行号逐字切全文，带 max_chars 截断（保护 context）。

    行号越界自动夹取到有效范围；start>end 视为空。返回 verbatim 文本（绝不改写）。
    """
    lines = (full_md or "").split("\n")
    total = len(lines)
    start = max(1, int(start_line))
    end = min(total, int(end_line))
    if end < start or total == 0:
        text = ""
    else:
        text = "\n".join(lines[start - 1:end])
    full_len = len(text)
    truncated = full_len > max_chars
    if truncated:
        text = text[:max_chars]
    page_label = page_label_for_range(page_map, start, end) if page_map else ""
    return {
        "start_line": start,
        "end_line": end,
        "text": text,
        "truncated": truncated,
        "total_chars": full_len,
        "page_label": page_label,
    }


def _block_original_text(blk: dict) -> str:
    """取块的原始文本（text / 表 / 图占位），逐字不改写。"""
    return blk.get("text") or blk.get("table_body") or blk.get("img_path") or ""


def _snippet_around(text: str, query: str, pad: int = 80) -> str:
    """在原文里定位 query（大小写不敏感），截取其周围 verbatim 片段；定位失败回退块首段。"""
    if not text:
        return ""
    lo = text.casefold().find(query.casefold())
    if lo < 0:
        # 归一化层命中但原文含空白/标签分隔：回退取块首一段（仍 verbatim、仍可被 resolver 定位）。
        return text[: pad * 2]
    a = max(0, lo - pad)
    b = min(len(text), lo + len(query) + pad)
    return text[a:b]


def search_evidence(
    content_list: list[dict],
    query: str,
    *,
    limit: int = 5,
    pad: int = 80,
) -> list[dict]:
    """关键词检索 → 命中块的源坐标 + 逐字上下文。

    命中判定：归一化后块文本包含归一化 query。坐标**一律取自 EvidenceResolver**——
    把抽出的 verbatim 片段回喂 resolver.resolve()，只采信 match_quality in (exact, partial)
    （零伪造，拒 prefix），保证"返回的 quote 可经 EvidenceResolver 回定位到同一 block"。
    """
    q = (query or "").strip()
    if not q or not content_list:
        return []
    qn = _strip(q)
    if not qn:
        return []

    resolver = EvidenceResolver(content_list)
    hits: list[dict] = []
    seen_blocks: set[int] = set()
    for i, blk in enumerate(content_list):
        if blk.get("type", "text") not in _INDEXABLE:
            continue
        raw = _block_original_text(blk)
        if not raw or qn not in _strip(raw):
            continue
        snippet = _snippet_around(raw, q, pad=pad)
        loc = resolver.resolve(snippet)
        # 零伪造：只采信确定性高置信定位（exact/partial），拒 prefix。
        if not (loc.get("found") and loc.get("match_quality") in ("exact", "partial")):
            continue
        bidx = loc["block_idx"]
        if bidx in seen_blocks:
            continue
        seen_blocks.add(bidx)
        hits.append({
            "block_idx": bidx,
            "page_no": loc["page_no"],
            "bbox": loc["bbox"],
            "section_title": loc["section_title"],
            "match_quality": loc["match_quality"],
            "quote": snippet,
            "preview": _strip(raw)[:120],
        })
        if len(hits) >= max(1, limit):
            break
    return hits
