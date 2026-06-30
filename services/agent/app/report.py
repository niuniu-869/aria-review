"""综述报告组装 (移植自 legacy fct_report.R 的思路)。

从已有分析 DTO (overview/sources/authors/documents) 组装报告, 渲染为 Markdown / HTML / DOCX。
- MD/HTML: 零额外依赖 (字符串拼接 + 转义)。
- DOCX: 复用 _md 生成 markdown → pandoc 转 docx (pandoc 缺失时端点降级, 见 main.py)。
A7: 增 sections 过滤 (只渲染选中章节) + author/title 注入 + docx 分支。
  - 可选外部内容 (prismaCounts/reviewMarkdown) 由前端提供, 无则该 section 渲染"未提供"提示。
安全: HTML 用 html.escape; Markdown 对动态语料字段做最小转义 (Codex slice5-P2),
防被允许 HTML 的 markdown 渲染器预览时注入。
"""
from __future__ import annotations

import html
import re
import subprocess
import tempfile
from pathlib import Path

# 转义 markdown 敏感字符 (动态语料字段用)
_MD_SPECIAL = re.compile(r"([\\`*_\[\]()<>#|])")

# 溯源锚点标记 [[anchor:a582_5_0__occ0]][11][[/anchor]] — 前端渲染为可点引用；
# 导出(md/html/docx)时须剥成纯 [11]，否则原始锚点串泄漏进导出文本(用户报告"乱码")。
_ANCHOR_RE = re.compile(r"\[\[anchor:[^\]]*\]\]|\[\[/anchor\]\]")


def _strip_export_anchors(text):
    """剥离 reviewMarkdown 里的溯源锚点包裹标记，保留中间的 [n] 引用编号。"""
    return _ANCHOR_RE.sub("", text) if text else text

# 可选章节枚举 (与 schemas.ReportOptions / openapi 同步)。
# overview/sources/authors/documents/references 可由现有 DTO 组装;
# prisma/review 需外部内容 (prismaCounts/reviewMarkdown), 缺则渲染"未提供"提示。
SECTIONS = ("overview", "sources", "authors", "documents", "references", "prisma", "review")
DEFAULT_SECTIONS = ["overview", "sources", "authors", "documents", "references"]

# pandoc 转 docx 超时 (秒)。spec §3.11 codex P1: subprocess 超时 30s。
PANDOC_TIMEOUT = 30

DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class PandocUnavailable(RuntimeError):
    """pandoc 不可用 (未安装/探测失败)。端点据此返回 503。"""


class PandocTimeout(RuntimeError):
    """pandoc 转换超时。端点据此返回 503/500。"""


class PandocFailed(RuntimeError):
    """pandoc 转换失败 (非零退出)。端点据此返回 500。"""


def _mde(s) -> str:
    return _MD_SPECIAL.sub(r"\\\1", str(s if s is not None else ""))


def _normalize_sections(sections) -> list[str]:
    """过滤为合法且去重保序的章节列表; 空/None → 默认全章节。"""
    if not sections:
        return list(DEFAULT_SECTIONS)
    seen: list[str] = []
    for s in sections:
        if s in SECTIONS and s not in seen:
            seen.append(s)
    return seen or list(DEFAULT_SECTIONS)


def _md(meta: dict, overview: dict, sources: dict, authors: dict, documents: dict,
        sections: list[str]) -> str:
    s = overview.get("stats", {})
    author = meta.get("author")
    lines = [
        f"# {_mde(meta.get('title', '文献计量分析报告'))}",
        "",
    ]
    if author:
        lines += [f"**作者**: {_mde(author)}", ""]
    lines += [
        f"> 生成于 BiblioCN · 语料 {int(s.get('documents', 0))} 篇 · "
        f"{s.get('timespanFrom', '?')}–{s.get('timespanTo', '?')}",
        "",
    ]

    if "overview" in sections:
        lines += [
            "## 领域概览",
            "",
            f"- 文献数: {s.get('documents', '-')}",
            f"- 期刊数: {s.get('sources', '-')}",
            f"- 作者数: {s.get('authors', '-')}",
            f"- 篇均被引: {s.get('avgCitationsPerDoc', '-')}",
            "",
        ]

    if "sources" in sections:
        lines += ["## 核心期刊", ""]
        for it in sources.get("topSources", [])[:10]:
            lines.append(f"- {_mde(it['source'])} ({int(it['articles'])})")
        lines.append("")

    if "authors" in sections:
        lines += ["## 核心作者", ""]
        for it in authors.get("topAuthors", [])[:10]:
            lines.append(f"- {_mde(it['author'])} ({int(it['articles'])})")
        lines.append("")

    if "documents" in sections:
        lines += ["## 高频关键词", ""]
        kws = ", ".join(
            f"{_mde(k['term'])}({int(k['freq'])})" for k in documents.get("keywords", [])[:20]
        )
        lines.append(kws or "(无)")
        lines += ["", "## 高被引文献", ""]
        for d in documents.get("topCited", [])[:10]:
            lines.append(
                f"- [{int(d.get('cited', 0))}] {_mde(d.get('title', '(无标题)'))} ({d.get('year', '-')})"
            )
        lines.append("")

    if "references" in sections:
        lines += ["## 参考文献", ""]
        cites = meta.get("citations") or []
        if cites:
            for c in cites[:200]:
                lines.append(f"- {_mde(c)}")
        else:
            lines.append("(参考文献以高被引文献为代表; 完整引用可在导出报告外用「引用导出」获取)")
        lines.append("")

    if "prisma" in sections:
        lines += ["## PRISMA 流程", ""]
        pc = meta.get("prismaCounts")
        if pc:
            lines += [
                f"- 识别记录数: {pc.get('identified', '-')}",
                f"- 去重数: {pc.get('duplicates', '-')}",
                f"- 筛选数: {pc.get('screened', '-')}",
                f"- 排除数: {pc.get('excluded', '-')}",
                f"- 纳入数: {pc.get('included', '-')}",
            ]
        else:
            lines.append("> 未提供 PRISMA 计数 (可在 PRISMA 面板填写后再导出含此章节的报告)。")
        lines.append("")

    if "review" in sections:
        lines += ["## AI 综述", ""]
        rv = meta.get("reviewMarkdown")
        if rv:
            lines.append(str(rv))
        else:
            lines.append("> 未提供 AI 综述内容 (可在 AI 综述面板生成后再导出含此章节的报告)。")
        lines.append("")

    return "\n".join(lines) + "\n"


def _html(meta: dict, overview: dict, sources: dict, authors: dict, documents: dict,
          sections: list[str]) -> str:
    e = lambda x: html.escape(str(x if x is not None else ""))  # noqa: E731
    s = overview.get("stats", {})

    def li(items):
        return "".join(f"<li>{e(x)}</li>" for x in items)

    title = e(meta.get("title", "文献计量分析报告"))
    author = meta.get("author")
    body: list[str] = [f"<h1>{title}</h1>"]
    if author:
        body.append(f"<p><strong>作者</strong>: {e(author)}</p>")
    body.append(
        f"<p>语料 {e(s.get('documents', '?'))} 篇 · "
        f"{e(s.get('timespanFrom', '?'))}–{e(s.get('timespanTo', '?'))}</p>"
    )

    if "overview" in sections:
        body.append(
            f"<h2>领域概览</h2><ul>"
            f"<li>文献数: {e(s.get('documents', '-'))}</li><li>期刊数: {e(s.get('sources', '-'))}</li>"
            f"<li>作者数: {e(s.get('authors', '-'))}</li>"
            f"<li>篇均被引: {e(s.get('avgCitationsPerDoc', '-'))}</li></ul>"
        )
    if "sources" in sections:
        src = [f"{i['source']} ({i['articles']})" for i in sources.get("topSources", [])[:10]]
        body.append(f"<h2>核心期刊</h2><ul>{li(src)}</ul>")
    if "authors" in sections:
        aut = [f"{i['author']} ({i['articles']})" for i in authors.get("topAuthors", [])[:10]]
        body.append(f"<h2>核心作者</h2><ul>{li(aut)}</ul>")
    if "documents" in sections:
        kw = ", ".join(f"{k['term']}({k['freq']})" for k in documents.get("keywords", [])[:20])
        cited = [
            f"[{d.get('cited', 0)}] {d.get('title', '(无标题)')} ({d.get('year', '-')})"
            for d in documents.get("topCited", [])[:10]
        ]
        body.append(f"<h2>高频关键词</h2><p>{e(kw) or '(无)'}</p>")
        body.append(f"<h2>高被引文献</h2><ul>{li(cited)}</ul>")
    if "references" in sections:
        cites = meta.get("citations") or []
        if cites:
            body.append(f"<h2>参考文献</h2><ul>{li(cites[:200])}</ul>")
        else:
            body.append(
                "<h2>参考文献</h2><p>参考文献以高被引文献为代表; "
                "完整引用可在导出报告外用「引用导出」获取。</p>"
            )
    if "prisma" in sections:
        pc = meta.get("prismaCounts")
        if pc:
            rows = [
                f"识别记录数: {pc.get('identified', '-')}",
                f"去重数: {pc.get('duplicates', '-')}",
                f"筛选数: {pc.get('screened', '-')}",
                f"排除数: {pc.get('excluded', '-')}",
                f"纳入数: {pc.get('included', '-')}",
            ]
            body.append(f"<h2>PRISMA 流程</h2><ul>{li(rows)}</ul>")
        else:
            body.append("<h2>PRISMA 流程</h2><p>未提供 PRISMA 计数。</p>")
    if "review" in sections:
        rv = meta.get("reviewMarkdown")
        # reviewMarkdown 为外部文本, 转义后以 <pre> 呈现, 不直接注入 HTML。
        body.append(
            f"<h2>AI 综述</h2>"
            + (f"<pre style='white-space:pre-wrap'>{e(rv)}</pre>" if rv
               else "<p>未提供 AI 综述内容。</p>")
        )

    return (
        f"<!doctype html><html lang=zh-CN><head><meta charset=utf-8>"
        f"<title>{title}</title></head>"
        f"<body style='font-family:system-ui;max-width:820px;margin:2rem auto'>"
        + "".join(body)
        + "</body></html>"
    )


def _to_docx(md_content: str, pandoc_path: str = "pandoc") -> bytes:
    """用 pandoc 把 markdown 转 docx, 返回二进制。

    spec §3.11 codex P1: tempfile + try/finally 清理; subprocess 列表参数 (绝不 shell=True);
    超时 30s; 失败/超时抛对应异常供端点映射状态码。
    """
    md_path: Path | None = None
    docx_path: Path | None = None
    try:
        # 临时输入 md 与输出 docx (delete=False, 由 finally 显式清理)。
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", encoding="utf-8", delete=False
        ) as f_md:
            f_md.write(md_content)
            md_path = Path(f_md.name)
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f_docx:
            docx_path = Path(f_docx.name)

        try:
            proc = subprocess.run(
                [pandoc_path, str(md_path), "-f", "markdown", "-t", "docx",
                 "-o", str(docx_path)],
                capture_output=True,
                timeout=PANDOC_TIMEOUT,
                check=False,
            )
        except FileNotFoundError as exc:  # pandoc 二进制不在 PATH
            raise PandocUnavailable("pandoc 不可用") from exc
        except subprocess.TimeoutExpired as exc:
            raise PandocTimeout("pandoc 转换超时") from exc

        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", "replace")[:500]
            raise PandocFailed(f"pandoc 转换失败: {err}")

        data = docx_path.read_bytes()
        if not data:
            raise PandocFailed("pandoc 输出为空")
        return data
    finally:
        for p in (md_path, docx_path):
            if p is not None:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass


def probe_pandoc(pandoc_path: str = "pandoc") -> bool:
    """探测 pandoc 是否可用 (启动时调用一次并缓存)。"""
    try:
        proc = subprocess.run(
            [pandoc_path, "--version"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def build_report(fmt: str, meta: dict, overview: dict, sources: dict,
                 authors: dict, documents: dict,
                 sections: list[str] | None = None,
                 pandoc_path: str = "pandoc") -> tuple[str | bytes, str]:
    """返回 (content, media_type)。fmt: md | html | docx。

    sections: 选中的章节子集 (None → 默认全章节)。
    docx: 复用 _md 生成 markdown → pandoc 转 docx (pandoc 缺失/超时/失败抛异常)。
    """
    secs = _normalize_sections(sections)
    # 导出前剥离综述里的溯源锚点标记(否则原始 [[anchor:...]] 串泄漏到导出文本)。
    if meta.get("reviewMarkdown"):
        meta = {**meta, "reviewMarkdown": _strip_export_anchors(meta["reviewMarkdown"])}
    # 文本导出加 UTF-8 BOM：导出文件本身是合法 UTF-8，但中文系统(记事本/Excel)默认按 GBK
    # 打开会 mojibake；BOM 让其自动识别 UTF-8。docx 走 pandoc(md_content 不加 BOM, 免污染输入)。
    if fmt == "md":
        return "\ufeff" + _md(meta, overview, sources, authors, documents, secs), \
            "text/markdown; charset=utf-8"
    if fmt == "html":
        return "\ufeff" + _html(meta, overview, sources, authors, documents, secs), \
            "text/html; charset=utf-8"
    if fmt == "docx":
        md_content = _md(meta, overview, sources, authors, documents, secs)
        return _to_docx(md_content, pandoc_path), DOCX_MEDIA
    raise ValueError(f"unsupported format: {fmt}")
