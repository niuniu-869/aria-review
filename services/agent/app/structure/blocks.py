"""块视图 + 证据解析：把 MinerU content_list 转成 StructureBlock[]，并把逐字 quote
确定性定位回具体 block（页码+坐标+章节+类型），供 B4 综述溯源用。

EvidenceResolver 搬自 FS_Agent backend/track3/extraction/evidence.py，**去财报**：
删去数值核心匹配（数字字段是财报专属），只保留通用的文本包含 / 前缀包含两段匹配。
纯函数 + 轻量状态（quote 去重），零 LLM。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..schemas import StructureBlock

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
# MinerU content_list 的 block_type → 受控 block_type
_TYPE_MAP = {"text": "text", "table": "table", "image": "image",
             "title": "title", "seal": "image"}
# 可成块(会出现在 StructureResponse.blocks)的类型；其余(page_number/header/footer/seal)是噪声。
# resolve 只索引这些类型 —— 否则 quote 命中页眉/页脚会返回一个不在 blocks[] 里的 block_idx,
# 前端锚点对不上(codex 二审 P2)。与 content_list_to_blocks 的成块判定保持一致。
_INDEXABLE = {"text", "table", "image"}


def _strip(s: str) -> str:
    """去标签 + 去所有空白，得到可比较的归一化文本。"""
    return _WS.sub("", _TAG.sub("", s or ""))


_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
# 单块字符上限：超长无空行文本(退化形态)会切出巨块 → 溯源退化为大段高亮 +
# EvidenceResolver 逐块扫描放大延迟(codex P2)。超阈值的正文段再按行/定长窗口切碎。
# 阈值取 4000：实测 Sciverse 段落中位 ~200、最大 ~2k 全在阈值内(正常数据行为不变)，只兜底退化形态。
_MAX_BLOCK_CHARS = 4000


def _chunk_long(text: str) -> list[str]:
    """正文段超 _MAX_BLOCK_CHARS 时再切：优先按换行切；单行仍超长 → 定长窗口。"""
    if len(text) <= _MAX_BLOCK_CHARS:
        return [text]
    out: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if len(line) <= _MAX_BLOCK_CHARS:
            out.append(line)
        else:
            out.extend(
                line[i : i + _MAX_BLOCK_CHARS]
                for i in range(0, len(line), _MAX_BLOCK_CHARS)
            )
    return out or [text[:_MAX_BLOCK_CHARS]]


def markdown_to_content_list(markdown: str) -> list[dict]:
    """把无结构全文 markdown 切成 content_list 块（无 page_idx/bbox），供纯文本来源
    （如 Sciverse /content，无 MinerU 结构）也能走 B4 溯源锚定。

    与 MinerU content_list 同形：每块 {"type":"text","text":...}，markdown 标题(#)块
    附 text_level（供 EvidenceResolver 章节追踪）。按空行(\\n\\n)分段——Sciverse 全文
    实测段落中位 ~200 字符，粒度适合"定位到段"。保留段落原文：EvidenceResolver 按文本
    子串匹配 quote→block，build_block_line_ranges 按锚文本回映 full.md 行号。
    无 page_idx → page_no 取 None；无 bbox → has_bbox=False（定位到段，不到坐标）。
    """
    blocks: list[dict] = []
    for para in re.split(r"\n[ \t]*\n", markdown or ""):
        para = para.strip()
        if not para:
            continue
        m = _MD_HEADING.match(para)
        if m and m.group(2).strip():
            blocks.append(
                {"type": "text", "text": m.group(2).strip(), "text_level": len(m.group(1))}
            )
        else:
            # 正常段直接成块；退化超长段(无空行)再切碎，避免巨块(codex P2)。
            for chunk in _chunk_long(para):
                blocks.append({"type": "text", "text": chunk})
    return blocks


@dataclass
class _Block:
    idx: int
    block_type: str
    page_no: int | None
    bbox: list[float] | None
    chapter: str
    norm_text: str
    raw_preview: str


class EvidenceResolver:
    """据一份文档的 content_list 预建可检索 block 列表，按 quote 确定性定位证据。"""

    def __init__(self, content_list: list[dict]):
        self._blocks: list[_Block] = []
        self._by_quote: dict[str, dict] = {}     # 归一化 quote → 结果（去重 memo）
        self._build(content_list or [])

    def _build(self, content_list: list[dict]) -> None:
        chapter = ""
        for i, blk in enumerate(content_list):
            btype = blk.get("type", "text")
            # 维护"当前章节"：带 text_level 的 text 块视为标题(在跳过判断之前,保证章节连续)
            if btype == "text" and blk.get("text_level") is not None:
                t = (blk.get("text") or "").strip()
                if t:
                    chapter = t[:60]
            if btype not in _INDEXABLE:
                continue  # 噪声块不索引；但 idx 仍用原 content_list 序号,与 blocks[] 对齐
            raw = blk.get("text") or blk.get("table_body") or blk.get("img_path") or ""
            try:
                page_no = int(blk.get("page_idx", 0)) + 1
            except (TypeError, ValueError):
                page_no = None
            self._blocks.append(_Block(
                idx=i, block_type=_TYPE_MAP.get(btype, "text"),
                page_no=page_no, bbox=blk.get("bbox"),
                chapter=chapter, norm_text=_strip(raw),
                raw_preview=_strip(raw)[:120],
            ))

    def _match_block(self, quote: str) -> tuple["_Block | None", str | None]:
        """找最匹配 quote 的 block，并返回匹配置信度 match_quality。优先级：
        ① quote ⊆ block：块文本含整条 quote → "exact"(最精确),取最短命中块；
        ② block ⊆ quote：整块被 quote 含(块≥8 字防短标题误命中)→ "partial",取最长块(更可能是正文段)；
        ③ 前 16 字前缀命中 → "prefix"(弱,仅开头吻合,后半可能幻觉/跨块),取最短块。
        返回 (None, None) 表示证据缺失。"""
        qn = _strip(quote)
        if len(qn) < 3:
            return None, None
        # ① quote ⊆ block(最精确)
        cands = [b for b in self._blocks if b.norm_text and qn in b.norm_text]
        if cands:
            return min(cands, key=lambda b: len(b.norm_text)), "exact"
        # ② block ⊆ quote(块≥8 字)取最长块,优先正文而非短标题
        cands = [b for b in self._blocks if len(b.norm_text) >= 8 and b.norm_text in qn]
        if cands:
            return max(cands, key=lambda b: len(b.norm_text)), "partial"
        # ③ 前缀/片段包含：quote 前 16 字命中,取最短块（弱匹配,溯源不应据此判"已定位"）
        head = qn[:16]
        cands = [b for b in self._blocks if head and head in b.norm_text]
        if cands:
            return min(cands, key=lambda b: len(b.norm_text)), "prefix"
        return None, None

    def resolve(self, quote: str | None) -> dict:
        """解析一条逐字 quote → 契约形状 dict。

        命中：{found:True, match_quality, block_idx, page_no, bbox, section_title, table_idx:None, quote}。
          match_quality: "exact"(quote⊆block) / "partial"(block⊆quote) / "prefix"(仅前16字命中,弱)。
          调用方据 match_quality 决定是否采信为"已定位"——零伪造场景应只信 exact/partial,
          prefix 仅开头吻合、后半可能幻觉或跨块,不足以作精确溯源(codex B4a 二审 P2)。
        未命中/空：found=False，各定位字段为 None；quote 仍回传（截 200 字）。
        相同 quote 复用同一结果（去重 memo）。
        """
        q_str = str(quote) if quote is not None else ""
        quote_out = q_str.strip()[:200]
        if not q_str.strip():
            return {"found": False, "match_quality": None, "block_idx": None, "page_no": None,
                    "bbox": None, "section_title": None, "table_idx": None, "quote": quote_out}
        key = _strip(q_str)
        if key in self._by_quote:
            return dict(self._by_quote[key])
        blk, quality = self._match_block(q_str)
        if blk is None:
            result = {"found": False, "match_quality": None, "block_idx": None, "page_no": None,
                      "bbox": None, "section_title": None, "table_idx": None, "quote": quote_out}
            self._by_quote[key] = result
            return dict(result)
        result = {"found": True, "match_quality": quality, "block_idx": blk.idx,
                  "page_no": blk.page_no, "bbox": blk.bbox, "section_title": blk.chapter,
                  "table_idx": None, "quote": quote_out}
        self._by_quote[key] = result
        return dict(result)


def content_list_to_blocks(
    content_list: list[dict],
    page_map: dict,
    block_line_ranges: dict[str, list[int]],
) -> list[StructureBlock]:
    """把 MinerU content_list 转成 StructureBlock[]（噪声块如 page_number 不入）。

    section_title 维护"当前章节"=最近一个带 text_level 的 text 块文本；标题块自身用自己的文本，
    正文/表/图块用当前章节（首个标题前可能为 ""）。md_line_start/end 仅取 block_line_ranges
    里的精确锚定区间；缺失则为 None（不可行级定位，零伪造，绝不回退伪造行号）。
    page_map 参数保留供端点传入（页一致性/未来页级降级用），当前行定位不依赖它。
    """
    out: list[StructureBlock] = []
    current_section = ""
    for block_idx, block in enumerate(content_list or []):
        mineru_type = block.get("type")
        text_level = block.get("text_level")
        # 判定契约 type；其余类型（page_number/header/footer）跳过
        if mineru_type == "text":
            ctype = "title" if text_level is not None else "text"
        elif mineru_type == "table":
            ctype = "table"
        elif mineru_type == "image":
            ctype = "image"
        else:
            continue

        try:
            page_no = int(block.get("page_idx", 0)) + 1
        except (TypeError, ValueError):
            page_no = 1
        if page_no < 1:
            page_no = 1

        # 章节归属：标题块更新当前章节并以自身为 section_title
        if ctype == "title":
            title_text = (block.get("text") or "").strip()
            if title_text:
                current_section = title_text
            section_title = title_text
        else:
            section_title = current_section

        # 行区间：仅取精确锚定的块行区间；缺失/非法时为 None(不可行级定位)。
        # 绝不回退到页首伪造行号——否则前端会把错误行当真高亮 = 静默伪溯源(codex 二审 P1,
        # 违反零伪造底座 + 契约 §5.4「禁用近似会高亮错行」)。前端遇 None 降级到页级/bbox。
        rng = (block_line_ranges or {}).get(str(block_idx))
        if rng and len(rng) == 2 and int(rng[0]) >= 1 and int(rng[1]) >= int(rng[0]):
            md_line_start, md_line_end = int(rng[0]), int(rng[1])
        else:
            md_line_start = md_line_end = None

        # 预览：文本/标题取前 120 字；表取 caption 或占位；图占位
        if ctype in {"text", "title"}:
            text_preview = (block.get("text") or "")[:120]
        elif ctype == "table":
            text_preview = block.get("caption") or "[表格]"
        else:  # image
            text_preview = "[图片]"

        out.append(StructureBlock(
            block_idx=block_idx,
            type=ctype,
            text_level=text_level,
            page_no=page_no,
            md_line_start=md_line_start,
            md_line_end=md_line_end,
            bbox=block.get("bbox"),
            section_title=section_title,
            text_preview=text_preview,
        ))
    return out
