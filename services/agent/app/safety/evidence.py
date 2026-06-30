"""EvidenceRef — 将引用/论断绑定到语料真实记录的结构体。

设计参考: 分层 evidence_bundle schema 的轻量化裁剪。
本结构轻量化, 专注于引用溯源 (而非代码/章节 evidence)。

用途:
  check_citations_against_records 对文中每条命中的引用产出一个 EvidenceRef;
  GuardedStream 将 EvidenceRef 列表附加在每个放行段的 metadata 中。
"""
from __future__ import annotations

import hashlib
import datetime
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class EvidenceRef:
    """把文中一条引用绑定到语料真实记录的轻量证据结构。

    Attributes:
        paper_id:    语料中记录的唯一标识 (records 列表 1-based 行号 / 或记录自带的 idx)
        corpus_id:   语料/项目标识 (留给调用方填充; 本模块内置为 "local_corpus")
        record_hash: 目标记录关键字段的 sha256 (title + year + doi 拼接, 用于防篡改溯源)
        span:        原文中引用的字面字符串 (如 "10.1234/abc" 或 "Smith (2020)")
        claim:       包含此引用的上下文句子 (可选, 供审计)
        created_at:  产出时间戳 (ISO 8601)
        cite_type:   引用类型 (doi / pmid / en / cn / num)
        match_quality: 匹配质量 (green / yellow — 对应 cite_check 三色判定中的 green/yellow)
        source_content_sha256: 引用所绑文献"全文文档内容"的 sha256 (P3-2 文档内容溯源)。
                       与 record_hash 是两个维度: record_hash 绑题录 (title/year/doi),
                       source_content_sha256 绑全文文档内容 (= Attachment.sha256 =
                       markdown 文件名 stem); None 表示该记录无可溯源全文。
    """

    paper_id: int
    corpus_id: str
    record_hash: str
    span: Optional[str]
    claim: Optional[str]
    created_at: str = field(default_factory=lambda: datetime.datetime.utcnow().isoformat() + "Z")
    cite_type: str = "unknown"
    match_quality: str = "green"
    source_content_sha256: Optional[str] = None
    # B4a 精读溯源：块级定位字段（map 阶段由 EvidenceResolver 解析逐字 quote 得到）。
    # 全部可选、默认 None，保持向后兼容（from_record 不填，恒为 None）。
    page_no: Optional[int] = None
    block_idx: Optional[int] = None
    bbox: Optional[list[float]] = None  # 契约 §1.3: list[float] | None (0-1000 归一化框)
    table_idx: Optional[int] = None
    cell_row: Optional[int] = None
    cell_col: Optional[int] = None
    section_title: Optional[str] = None
    anchor_id: Optional[str] = None

    # ------------------------------------------------------------------
    # 工厂方法
    # ------------------------------------------------------------------

    @classmethod
    def from_record(
        cls,
        paper_id: int,
        record: dict,
        span: Optional[str] = None,
        claim: Optional[str] = None,
        corpus_id: str = "local_corpus",
        cite_type: str = "unknown",
        match_quality: str = "green",
        source_content_sha256: Optional[str] = None,
    ) -> "EvidenceRef":
        """从 records 条目构造 EvidenceRef。

        Args:
            paper_id:   记录在语料中的行号 (1-based)
            record:     records 中对应的 dict ({title, authors, year, doi, ...})
            span:       原文中的引用字符串
            claim:      包含此引用的句子
            corpus_id:  语料标识
            cite_type:  引用类型
            match_quality: 命中质量
            source_content_sha256: 全文文档内容 sha256 (P3-2)。未显式传入时回退读
                        record["content_sha256"] (records 由项目加载函数填充该字段)。
        """
        h = cls._hash_record(record)
        # 显式参数优先; 否则回退读 record 内的 content_sha256
        content_sha = source_content_sha256
        if content_sha is None:
            content_sha = record.get("content_sha256")
        return cls(
            paper_id=paper_id,
            corpus_id=corpus_id,
            record_hash=h,
            span=span,
            claim=claim,
            cite_type=cite_type,
            match_quality=match_quality,
            source_content_sha256=content_sha,
        )

    @staticmethod
    def _hash_record(record: dict) -> str:
        """对记录的关键字段计算 sha256, 用于防篡改溯源。"""
        title = str(record.get("title") or "").strip().lower()
        year = str(record.get("year") or "")
        doi = str(record.get("doi") or "").strip().lower()
        payload = f"{title}|{year}|{doi}"
        return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "paper_id": self.paper_id,
            "corpus_id": self.corpus_id,
            "record_hash": self.record_hash,
            "span": self.span,
            "claim": self.claim,
            "created_at": self.created_at,
            "cite_type": self.cite_type,
            "match_quality": self.match_quality,
            "source_content_sha256": self.source_content_sha256,
            "page_no": self.page_no,
            "block_idx": self.block_idx,
            "bbox": self.bbox,
            "table_idx": self.table_idx,
            "cell_row": self.cell_row,
            "cell_col": self.cell_col,
            "section_title": self.section_title,
            "anchor_id": self.anchor_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EvidenceRef":
        return cls(
            paper_id=int(d["paper_id"]),
            corpus_id=str(d.get("corpus_id", "local_corpus")),
            record_hash=str(d["record_hash"]),
            span=d.get("span"),
            claim=d.get("claim"),
            created_at=d.get("created_at", datetime.datetime.utcnow().isoformat() + "Z"),
            cite_type=str(d.get("cite_type", "unknown")),
            match_quality=str(d.get("match_quality", "green")),
            source_content_sha256=d.get("source_content_sha256"),
            page_no=d.get("page_no", None),
            block_idx=d.get("block_idx", None),
            bbox=d.get("bbox", None),
            table_idx=d.get("table_idx", None),
            cell_row=d.get("cell_row", None),
            cell_col=d.get("cell_col", None),
            section_title=d.get("section_title", None),
            anchor_id=d.get("anchor_id", None),
        )
