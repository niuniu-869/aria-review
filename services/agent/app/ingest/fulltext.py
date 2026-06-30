"""全文摄取：PDF → MinerU Markdown → 存盘 + 入库。

主入口：
  ingest_pdf(path, language, session) → {"paper_id": int, "markdown_len": int, ...}
  ingest_pdfs(paths, language, session, batch_size=50) → list[dict]

元数据抽取策略（优先级）：
  1. Markdown 首部结构：
     - 第一个 # 标题 → title
     - ## Abstract / ## ABSTRACT 段落 → abstract
     - 首部行中 "Author" / "Authors" 正则抽作者
  2. 文件名兜底（格式：作者_年_标题.pdf）
     - 首段以 "_" 分割：第1段→作者, 第2段→年, 第3段→标题

存储位置：BIBLIOCN_CORPORA_DIR/fulltext/<sha256>.md
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import Attachment, DocumentStructure
from ..repositories.library import add_paper
from ..structure.page_map import build_block_line_ranges, build_line_page_map
from .mineru import parse_pdfs

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 元数据抽取
# ---------------------------------------------------------------------------

_ABSTRACT_RE = re.compile(
    r"(?:^|\n)##\s+(?:ABSTRACT|Abstract|abstract)\s*\n+(.*?)(?=\n\s*(?:#{1,6}[ \t]+|(?:KEY\s*WORDS?|Key\s*words?|key\s*words?|Keywords?|keywords?)\s*[:：]?)|\Z)",
    re.DOTALL,
)
_ABSTRACT_INLINE_RE = re.compile(
    r"(?:^|\n)\s*(?:ABSTRACT|Abstract|abstract)\s*[:：]\s*(.*?)(?=\n\s*(?:Key\s*words?|Keywords?|Introduction|[A-Z][A-Za-z ]{0,30}\s*[:：])|\n#|\Z)",
    re.DOTALL,
)
_KEYWORDS_RE = re.compile(
    r"(?:^|\n)\s*(?:#{1,6}[ \t]+)?(?:KEY\s*WORDS?|Key\s*words?|key\s*words?|Keywords?|keywords?)\s*[:：]?\s*\n?(.*?)(?=\n\s*(?:#{1,6}[ \t]+|(?:INTRODUCTION|Introduction|ABSTRACT|Abstract|References?|REFERENCES|Acknowledg|ACKNOWLEDG)\b)|\Z)",
    re.DOTALL,
)
_HEADING_RE = re.compile(r"^#{1,6}[ \t]+(.+?)\s*$", re.MULTILINE)
_PDF_VISIBLE_TEXT_RE = re.compile(rb"\(([^()\r\n]{3,300})\)\s*Tj")
# 作者行：冒号后的内容，支持 "Author:" / "Authors:" / "Author(s):"
_AUTHOR_LINE_RE = re.compile(
    r"^(?:Authors?|Author\(s\))\s*[：:]\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)
_HEX_PREFIX_RE = re.compile(r"^[0-9a-fA-F]{16,64}$")
_SHORT_CODE_RE = re.compile(r"^[A-Z0-9._-]{2,12}$")
_SECTION_TITLE_RE = re.compile(
    r"^(abstract|keywords?|key\s*words?|introduction|background|methods?|materials?|results?|discussion|conclusions?|references?|acknowledg(?:e)?ments?)$",
    re.IGNORECASE,
)


def _is_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _looks_like_noise_title(title: str) -> bool:
    value = title.strip()
    if _SECTION_TITLE_RE.match(value):
        return True
    if len(value) < 8:
        return True
    if _HEX_PREFIX_RE.match(value) or _SHORT_CODE_RE.match(value):
        return True
    if value.startswith("(") and value.endswith(")"):
        return True
    letters = sum(ch.isalpha() for ch in value)
    return letters < 4 and not _is_cjk(value)


def _split_people(raw: str) -> list[dict]:
    raw = re.sub(r"\s+", " ", raw).strip(" ,;；，")
    if not raw:
        return []
    parts = re.split(r"[,;，；]+", raw)
    return [{"literal": p.strip()} for p in parts if p.strip()]


def _normalize_keywords(raw: str) -> str:
    raw = re.sub(r"\*{1,2}|_{1,2}|`", "", raw or "")
    raw = re.sub(r"^\s*[-*•]\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s+", " ", raw).strip(" .;；,，")
    if not raw:
        return ""
    parts = re.split(r"[;；,，]\s*", raw)
    cleaned: list[str] = []
    seen: set[str] = set()
    for part in parts:
        item = part.strip(" .")
        if not item:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(item)
    return "; ".join(cleaned)


def _strip_upload_prefix(stem: str) -> str:
    parts = stem.split("_")
    while parts and (_HEX_PREFIX_RE.match(parts[0]) or re.match(r"^[0-9a-fA-F]{8,}$", parts[0])):
        parts = parts[1:]
        if parts and parts[0].isdigit():
            parts = parts[1:]
    return "_".join(parts) or stem


def _extract_metadata_from_markdown(markdown: str) -> dict:
    """从 MinerU 输出的 Markdown 抽取标题/摘要/作者。"""
    meta: dict = {}

    # 标题：选择第一个非噪声 Markdown 标题，避免页眉编号/短代码覆盖真实标题。
    title_match = None
    for m in _HEADING_RE.finditer(markdown):
        candidate = m.group(1).strip()
        if not _looks_like_noise_title(candidate):
            meta["title"] = candidate
            title_match = m
            break

    # 摘要
    m = _ABSTRACT_RE.search(markdown)
    if not m:
        m = _ABSTRACT_INLINE_RE.search(markdown)
    if m:
        abstract_text = m.group(1).strip()
        # 去除可能的 markdown 强调标记
        abstract_text = re.sub(r"\*{1,2}|_{1,2}", "", abstract_text)
        meta["abstract"] = abstract_text

    # 关键词：PDF/MinerU 常见为 "Keywords:"、"Key words:" 或 "## Keywords"。
    m = _KEYWORDS_RE.search(markdown[:6000])
    if m:
        keywords = _normalize_keywords(m.group(1))
        if keywords:
            meta["keywords"] = keywords

    # 作者行（可能在前几行）
    m = _AUTHOR_LINE_RE.search(markdown[:3000])  # 只看前 3000 字符
    if m:
        meta["creators"] = _split_people(m.group(1))
    elif title_match:
        # MinerU 常把标题下一行作者直接输出为普通段落，没有 Authors: 前缀。
        tail = markdown[title_match.end(): title_match.end() + 1000]
        lines = [line.strip() for line in tail.splitlines() if line.strip()]
        if lines:
            first = lines[0]
            if not first.lower().startswith(("abstract", "keywords", "introduction")):
                creators = _split_people(first)
                if creators:
                    meta["creators"] = creators

    return meta


def _extract_metadata_from_filename(filename: str) -> dict:
    """从文件名兜底抽取元数据。格式：作者_年_标题.pdf"""
    meta: dict = {}
    stem = _strip_upload_prefix(Path(filename).stem)
    parts = stem.split("_", 2)
    if len(parts) >= 3:
        author, year_str, title = parts[0], parts[1], parts[2]
        meta["title"] = title.replace("_", " ").strip()
        meta["creators"] = [{"literal": author.strip()}] if author.strip() else []
        try:
            year = int(year_str)
            if 1900 <= year <= 2100:
                meta["year"] = year
        except ValueError:
            pass
    elif len(parts) == 2:
        left, right = parts[0].strip(), parts[1].strip()
        try:
            year = int(right)
            if 1900 <= year <= 2100:
                meta["year"] = year
                meta["creators"] = [{"literal": left}] if left else []
        except ValueError:
            # 常见中文文件名为「标题_作者.pdf」，不是「作者_标题.pdf」。
            if _is_cjk(left) and len(left) >= len(right):
                meta["title"] = left
                meta["creators"] = [{"literal": right}] if right else []
            else:
                meta["title"] = right
                meta["creators"] = [{"literal": left}] if left else []
    else:
        meta["title"] = stem.strip()
    return meta


def _merge_metadata(from_md: dict, from_fn: dict) -> dict:
    """合并两个来源，Markdown 优先，文件名兜底。"""
    merged = dict(from_fn)
    merged.update(from_md)  # Markdown 覆盖文件名结果
    # 保证 title 始终存在
    if not merged.get("title"):
        merged["title"] = "Unknown Title"
    return merged


def _fallback_markdown_from_pdf(path: Path) -> str:
    """无 MinerU Token 时的本地降级解析，保证 PDF 导入链路可复现。"""
    meta = _extract_metadata_from_filename(path.name)
    title = meta.get("title") or path.stem
    try:
        with path.open("rb") as fh:
            raw = fh.read(16 * 1024 * 1024)  # 限读前 16MB,防大 PDF 降级解析 OOM(codex Batch3 P2)
        texts = [
            m.group(1).decode("latin-1", errors="ignore").strip()
            for m in _PDF_VISIBLE_TEXT_RE.finditer(raw)
        ]
    except Exception:
        texts = []
    body = "\n\n".join(t for t in texts if t)
    if not body:
        body = f"PDF filename: {path.name}"
    return f"# {title}\n\n## Extracted Text\n\n{body}\n"


def _use_local_pdf_fallback(_mineru_client=None) -> bool:
    """仅生产运行缺少 MinerU Token 时降级；测试 patch parse_pdfs 时仍走 mock。"""
    if settings.ocr_token or _mineru_client is not None:
        return False
    return getattr(parse_pdfs, "__module__", "") == "app.ingest.mineru"


# ---------------------------------------------------------------------------
# 存盘
# ---------------------------------------------------------------------------

def _save_markdown(sha256: str, markdown: str) -> Path:
    """把 markdown 存到 BIBLIOCN_CORPORA_DIR/fulltext/<sha256>.md，返回路径。"""
    base_dir = Path(settings.corpora_dir) / "fulltext"
    base_dir.mkdir(parents=True, exist_ok=True)
    out_path = base_dir / f"{sha256}.md"
    out_path.write_text(markdown, encoding="utf-8")
    return out_path


def _sha256_of_file(path: Path) -> str:
    """计算文件 sha256（16 进制）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# DocumentStructure 落库（可信溯源 B1）
# ---------------------------------------------------------------------------

async def _upsert_document_structure(
    session: AsyncSession,
    attachment_id: int,
    sha256: str,
    markdown: str,
    content_list: list,
) -> None:
    """据 content_list 计算结构并 upsert 一条 DocumentStructure（按 attachment_id 幂等）。

    重新摄取同一附件时更新现有行而非堆叠（attachment_id unique）。
    page_map / block_line_ranges 均对齐到「真实 markdown」（非重建文本）。
    """
    page_map = build_line_page_map(markdown, content_list)
    block_line_ranges = build_block_line_ranges(markdown, content_list)
    has_bbox = any(b.get("bbox") for b in content_list)
    markdown_sha256 = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    # page_count 从全部块 page_idx 最大值+1 计（不只锚定块）：末页若只剩图/页脚/未命中块,
    # 仅用锚定页数会低估（codex 二审 P2）。锚点仍只用于行↔页映射(page_map)。
    page_idxs: list[int] = []
    for _b in content_list:
        try:
            page_idxs.append(int(_b.get("page_idx", 0)))
        except (TypeError, ValueError):
            pass
    page_count = (max(page_idxs) + 1) if page_idxs else page_map.get("total_pages", 1)
    bbox_coord_space = "mineru_1000" if has_bbox else None

    existing = (
        await session.execute(
            select(DocumentStructure).where(
                DocumentStructure.attachment_id == attachment_id
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.content_list = content_list
        existing.page_map = page_map
        existing.block_line_ranges = block_line_ranges
        existing.page_count = page_count
        existing.has_bbox = has_bbox
        existing.markdown_sha256 = markdown_sha256
        existing.source_pdf_sha256 = sha256
        existing.bbox_coord_space = bbox_coord_space
    else:
        session.add(
            DocumentStructure(
                attachment_id=attachment_id,
                content_list=content_list,
                page_map=page_map,
                block_line_ranges=block_line_ranges,
                page_count=page_count,
                has_bbox=has_bbox,
                markdown_sha256=markdown_sha256,
                source_pdf_sha256=sha256,
                bbox_coord_space=bbox_coord_space,
            )
        )
    await session.commit()
    logger.info(
        "DocumentStructure 落库: attachment_id=%d page_count=%d has_bbox=%s",
        attachment_id, page_count, has_bbox,
    )


# ---------------------------------------------------------------------------
# 公共：单篇 markdown → 存盘 + Paper + Attachment
# ---------------------------------------------------------------------------

async def _store_parsed_pdf(
    path: Path,
    sha256: str,
    markdown: str,
    *,
    content_list: list | None = None,
    session: AsyncSession,
) -> dict:
    """把解析好的 Markdown 存盘并写入数据库（Paper + Attachment + DocumentStructure）。

    此函数被 ingest_pdf 和 ingest_pdfs 共用，避免重复逻辑。

    Args:
        path:     原始 PDF 路径（用于文件名元数据兜底）。
        sha256:   PDF 文件内容的 sha256 哈希。
        markdown: MinerU 输出的 Markdown 全文。
        content_list: MinerU content_list（结构+page_idx+bbox），用于落库 DocumentStructure；
                      None（如缓存恢复无此数据）时跳过结构捕获。
        session:  AsyncSession（调用方管理事务）。

    Returns:
        {paper_id, attachment_id, markdown_len, markdown_path, status="done", err=None}
    """
    # 1. 存 Markdown
    md_path = _save_markdown(sha256, markdown)
    logger.info("Markdown 存盘: %s (%d chars)", md_path, len(markdown))

    # 2. 抽取元数据
    meta_md = _extract_metadata_from_markdown(markdown)
    meta_fn = _extract_metadata_from_filename(path.name)
    meta = _merge_metadata(meta_md, meta_fn)

    # 3. 建 Paper（幂等）
    paper_data = {
        "title": meta.get("title", "Unknown Title"),
        "creators": meta.get("creators", []),
        "year": meta.get("year"),
        "abstract": meta.get("abstract"),
        "keywords": meta.get("keywords"),
        "source": "upload",
        "item_type": "journalArticle",
    }
    paper = await add_paper(session, paper_data)
    logger.info("Paper 建好: id=%d title=%r", paper.id, paper.title)

    # 4. 建 Attachment
    attachment = Attachment(
        paper_id=paper.id,
        path=str(path),
        content_type="application/pdf",
        sha256=sha256,
        mineru_status="done",
        markdown_path=str(md_path),
    )
    session.add(attachment)
    await session.commit()
    await session.refresh(attachment)
    logger.info("Attachment 建好: id=%d", attachment.id)

    # 先把标量 id 取出：结构落库失败需 rollback，rollback 会过期会话内 ORM 对象，
    # 之后再访问 attachment.id/paper.id 在 async 下可能触发懒加载报错（codex 二审 P2）。
    paper_id = paper.id
    attachment_id = attachment.id

    # 5. 落库 DocumentStructure（可信溯源基础；失败不阻断摄取）
    if content_list:
        try:
            await _upsert_document_structure(
                session, attachment_id, sha256, markdown, content_list
            )
        except Exception as exc:  # 结构捕获失败不应导致整篇摄取失败
            logger.warning(
                "DocumentStructure 落库失败（不阻断摄取）: attachment_id=%s: %r",
                attachment_id, exc,
            )
            await session.rollback()

    return {
        "paper_id": paper_id,
        "attachment_id": attachment_id,
        "markdown_len": len(markdown),
        "markdown_path": str(md_path),
        "status": "done",
        "err": None,
    }


# ---------------------------------------------------------------------------
# 主入口（单篇）
# ---------------------------------------------------------------------------

async def ingest_pdf(
    path: Path | str,
    language: str = "en",
    *,
    session: AsyncSession,
    _mineru_client=None,  # 测试注入
) -> dict:
    """解析单个 PDF → 存 Markdown → 建 Paper + Attachment。

    Args:
        path: PDF 文件路径。
        language: MinerU 语言参数 ("en" / "ch")。
        session: AsyncSession（调用方管理事务）。
        _mineru_client: 测试注入 httpx.AsyncClient（传给 parse_pdfs）。

    Returns:
        {
            "paper_id": int,
            "attachment_id": int,
            "markdown_len": int,
            "markdown_path": str,
            "status": "done" | "failed",
            "err": str | None,
        }
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"PDF 不存在: {path}")

    # 1. MinerU 解析
    logger.info("MinerU 解析: %s", path.name)
    results = await parse_pdfs(
        [path], language=language, max_files=1, _client=_mineru_client
    )
    result = results[0]

    if result["status"] != "done" or not result.get("markdown"):
        return {
            "paper_id": None,
            "attachment_id": None,
            "markdown_len": 0,
            "markdown_path": None,
            "status": "failed",
            "err": result.get("err", "MinerU 返回 status != done"),
        }

    markdown: str = result["markdown"]

    # 2. 计算 sha256（基于 PDF 内容）
    sha256 = _sha256_of_file(path)

    # 3. 存盘 + 入库（公共逻辑）
    return await _store_parsed_pdf(
        path, sha256, markdown,
        content_list=result.get("content_list"),
        session=session,
    )


# ---------------------------------------------------------------------------
# 主入口（批量）
# ---------------------------------------------------------------------------

async def ingest_pdfs(
    paths: list[Path | str],
    language: str = "en",
    *,
    session: AsyncSession,
    batch_size: int = 50,
    _mineru_client=None,  # 测试注入
) -> list[dict]:
    """批量解析多个 PDF → 分批提交 MinerU → 存 Markdown → 建 Paper + Attachment。

    已缓存（sha256.md 已存在）的路径直接从磁盘恢复，不重新提交 MinerU。
    每批内单篇失败不影响其他篇（失败隔离）。

    Args:
        paths:      PDF 文件路径列表。
        language:   MinerU 语言参数 ("en" / "ch")。
        session:    AsyncSession（调用方管理事务）。
        batch_size: 每批提交 MinerU 的最大文件数（默认 50，留余量防超 200 限制）。
        _mineru_client: 测试注入 httpx.AsyncClient（传给 parse_pdfs）。

    Returns:
        list[dict]，每项对应一个输入路径，结构同 ingest_pdf 返回值，另加：
          "pdf_path": str   — 原始 PDF 路径
          "sha256":   str   — 文件 sha256（空串表示计算失败）
    """
    paths = [Path(p) for p in paths]
    if not paths:
        return []

    corpora_dir = settings.corpora_dir
    results: list[dict] = []

    # ------------------------------------------------------------------
    # 第一步：区分「已缓存」和「待解析」
    # ------------------------------------------------------------------
    cached: list[dict] = []      # 已有 Markdown 的条目
    pending: list[Path] = []     # 需要提交 MinerU 的路径
    pending_sha256s: list[str] = []

    for path in paths:
        if not path.exists():
            results.append({
                "pdf_path": str(path),
                "sha256": "",
                "paper_id": None,
                "attachment_id": None,
                "markdown_len": 0,
                "markdown_path": None,
                "status": "failed",
                "err": f"PDF 不存在: {path}",
            })
            continue

        try:
            sha256 = _sha256_of_file(path)
        except Exception as exc:
            results.append({
                "pdf_path": str(path),
                "sha256": "",
                "paper_id": None,
                "attachment_id": None,
                "markdown_len": 0,
                "markdown_path": None,
                "status": "failed",
                "err": f"sha256 计算失败: {exc}",
            })
            continue

        md_path = Path(corpora_dir) / "fulltext" / f"{sha256}.md"
        if md_path.exists():
            logger.info("缓存命中，跳过 MinerU: %s", path.name)
            cached.append({
                "path": path,
                "sha256": sha256,
                "md_path": md_path,
            })
        else:
            pending.append(path)
            pending_sha256s.append(sha256)

    # ------------------------------------------------------------------
    # 第二步：恢复缓存条目（读盘 + 建 Paper/Attachment，若尚未入库）
    # ------------------------------------------------------------------
    for item in cached:
        path = item["path"]
        sha256 = item["sha256"]
        md_path = item["md_path"]
        try:
            markdown = md_path.read_text(encoding="utf-8")
            # 缓存恢复无 content_list（盘上只存 markdown）→ 跳过结构捕获，不臆造。
            r = await _store_parsed_pdf(
                path, sha256, markdown, content_list=None, session=session
            )
            r["pdf_path"] = str(path)
            r["sha256"] = sha256
            r["status"] = "cached"
            results.append(r)
        except Exception as exc:
            logger.error("缓存恢复失败: %s: %s", path.name, exc)
            results.append({
                "pdf_path": str(path),
                "sha256": sha256,
                "paper_id": None,
                "attachment_id": None,
                "markdown_len": 0,
                "markdown_path": None,
                "status": "failed",
                "err": f"缓存恢复失败: {exc}",
            })

    # ------------------------------------------------------------------
    # 第三步：分批提交 MinerU 解析待解析路径
    # ------------------------------------------------------------------
    if not pending:
        return results

    if _use_local_pdf_fallback(_mineru_client):
        for path, sha256 in zip(pending, pending_sha256s):
            try:
                markdown = _fallback_markdown_from_pdf(path)
                r = await _store_parsed_pdf(path, sha256, markdown, session=session)
                r["pdf_path"] = str(path)
                r["sha256"] = sha256
                r["status"] = "done"
                results.append(r)
            except Exception as exc:
                results.append({
                    "pdf_path": str(path),
                    "sha256": sha256,
                    "paper_id": None,
                    "attachment_id": None,
                    "markdown_len": 0,
                    "markdown_path": None,
                    "status": "failed",
                    "err": f"本地 PDF 降级解析失败: {exc}",
                })
        return results

    num_batches = math.ceil(len(pending) / batch_size)
    logger.info(
        "批量 MinerU 解析: 共 %d 篇，分 %d 批（batch_size=%d）",
        len(pending), num_batches, batch_size,
    )

    for batch_idx in range(num_batches):
        start = batch_idx * batch_size
        end = start + batch_size
        batch_paths = pending[start:end]
        batch_sha256s = pending_sha256s[start:end]

        logger.info(
            "批次 %d/%d: 提交 %d 篇给 MinerU",
            batch_idx + 1, num_batches, len(batch_paths),
        )

        # 调用 parse_pdfs（一次 MinerU 批，服务端并行）
        try:
            parse_results: list[dict[str, Any]] = await parse_pdfs(
                batch_paths,
                language=language,
                max_files=batch_size,
                _client=_mineru_client,
            )
        except Exception as exc:
            # 整批失败 → 每篇都记为 failed（失败隔离）。
            # 用 repr + exc_info: 瞬时网络异常(超时/连接重置)的 str() 常为空,
            # 只 %s 会得到空白错误信息(历史 "批次调用失败: " 后空白即此因)。
            logger.error("批次 %d 整批解析失败: %r", batch_idx + 1, exc, exc_info=True)
            err_text = f"MinerU 批次调用失败: {exc!r}"
            for path, sha256 in zip(batch_paths, batch_sha256s):
                results.append({
                    "pdf_path": str(path),
                    "sha256": sha256,
                    "paper_id": None,
                    "attachment_id": None,
                    "markdown_len": 0,
                    "markdown_path": None,
                    "status": "failed",
                    "err": err_text,
                })
            continue

        # 逐篇处理解析结果
        for path, sha256, pr in zip(batch_paths, batch_sha256s, parse_results):
            if pr.get("status") != "done" or not pr.get("markdown"):
                results.append({
                    "pdf_path": str(path),
                    "sha256": sha256,
                    "paper_id": None,
                    "attachment_id": None,
                    "markdown_len": 0,
                    "markdown_path": None,
                    "status": "failed",
                    "err": pr.get("err") or "MinerU 返回 status != done",
                })
                continue

            markdown: str = pr["markdown"]
            try:
                r = await _store_parsed_pdf(
                    path, sha256, markdown,
                    content_list=pr.get("content_list"),
                    session=session,
                )
                r["pdf_path"] = str(path)
                r["sha256"] = sha256
                results.append(r)
            except Exception as exc:
                logger.error("存盘/入库失败: %s: %s", path.name, exc)
                results.append({
                    "pdf_path": str(path),
                    "sha256": sha256,
                    "paper_id": None,
                    "attachment_id": None,
                    "markdown_len": 0,
                    "markdown_path": None,
                    "status": "failed",
                    "err": f"存盘/入库失败: {exc}",
                })

    logger.info(
        "ingest_pdfs 完成: 共 %d 篇，成功 %d，失败 %d",
        len(results),
        sum(1 for r in results if r.get("status") in ("done", "cached")),
        sum(1 for r in results if r.get("status") == "failed"),
    )
    return results
