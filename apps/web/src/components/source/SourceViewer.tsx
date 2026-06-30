/**
 * SourceViewer.tsx — 原文渲染 + 按块行级高亮（F2，markdown 级必达档）。
 *
 * 杀手锏右栏：拉 /structure + /markdown，按 StructureBlock.md_line_start/end 对原文做
 * 行级高亮（契约 §5.3：行范围来自后端真实对齐，不在前端用文本搜索近似定位）。
 * 命中后平滑滚动到目标块并播一次 sv-pulse 朱砂光环；高亮块挂 data-source-anchor 供
 * F3 双向联动（点原文块 → 反向高亮综述锚点）。
 *
 * bbox 像素档由 F4 接入（focusBbox 存在且 has_bbox 校准就绪时切像素档，否则回退行级）。
 */
import { useEffect, useMemo, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { marked } from "marked";
import DOMPurify from "dompurify";
import { getStructure, getMarkdown } from "../../api/structure";
import type { StructureResponse } from "../../types/provenance";

export interface SourceViewerProps {
  projectId: number;
  paperId: number;
  /** 聚焦的 StructureBlock.block_idx（行级高亮目标） */
  focusBlockIdx?: number | null;
  /** F4 像素档：归一化 0-1000 bbox（存在且校准就绪才切像素档） */
  focusBbox?: [number, number, number, number] | null;
  /** F4 像素档：目标页码（1-based） */
  focusPage?: number | null;
  /** 高亮块回链的综述锚点 id（双向联动用） */
  anchorId?: string | null;
  /** 点击原文高亮块 → 反向高亮综述锚点（F3） */
  onSelectBlock?: (anchorId: string) => void;
}

/** 单行原文：行内 markdown（不做块级变换，保证行号 1:1 可定位）。 */
function renderLineHtml(text: string): string {
  const heading = /^(#{1,6})\s+(.*)$/.exec(text);
  const src = heading ? `**${heading[2]}**` : text;
  const html = marked.parseInline(src || " ", { async: false }) as string;
  return DOMPurify.sanitize(html);
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

/** 与原 PDF 对齐的坐标系（仅这些允许像素级 bbox；mineru_layout 等版面坐标不在内）。 */
const PDF_ALIGNED_COORD_SPACES = new Set(["pdf", "pdf_page", "page_pdf"]);

/** bbox 校准合法性（契约 §5.4）：四元组有限、正向、归一化在 0-1000 内。 */
function isValidBbox(b?: [number, number, number, number] | null): boolean {
  if (!b || b.length !== 4) return false;
  const [x0, y0, x1, y1] = b;
  return (
    [x0, y0, x1, y1].every((n) => Number.isFinite(n)) &&
    x0 >= 0 && x1 > x0 && x1 <= 1000 &&
    y0 >= 0 && y1 > y0 && y1 <= 1000
  );
}

export function SourceViewer({
  projectId,
  paperId,
  focusBlockIdx,
  focusBbox,
  focusPage,
  anchorId,
  onSelectBlock,
}: SourceViewerProps) {
  const enabled = paperId > 0;
  const structureQ = useQuery({
    queryKey: ["structure", projectId, paperId],
    queryFn: () => getStructure(projectId, paperId),
    enabled,
    retry: false,
  });
  const markdownQ = useQuery({
    queryKey: ["markdown", projectId, paperId],
    queryFn: () => getMarkdown(projectId, paperId),
    enabled,
    retry: false,
  });

  const structure: StructureResponse | undefined = structureQ.data;
  const lines = useMemo(() => (markdownQ.data?.markdown ?? "").split("\n"), [markdownQ.data]);

  const block =
    focusBlockIdx != null
      ? structure?.blocks.find((b) => b.block_idx === focusBlockIdx) ?? null
      : null;

  const hlRef = useRef<HTMLDivElement>(null);

  // 命中后：平滑滚动到高亮块并播一次 sv-pulse（animationend 后移除，保证可重复触发）。
  useEffect(() => {
    const el = hlRef.current;
    if (!el || block == null || lines.length === 0) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.add("is-pulsing");
    const done = () => el.classList.remove("is-pulsing");
    el.addEventListener("animationend", done, { once: true });
    return () => el.removeEventListener("animationend", done);
  }, [block, lines.length, focusBlockIdx]);

  if (!enabled) return <div className="sv-pane sv-empty muted">未选择文献</div>;
  if (structureQ.isLoading || markdownQ.isLoading)
    return <div className="sv-pane sv-empty muted">加载原文…</div>;
  if (structureQ.error || markdownQ.error)
    return <div className="sv-pane sv-empty muted">原文不可用（该文献可能尚未 OCR 解析）</div>;

  // 校验原始行范围（codex P1）：异常(越界/end<start/非整数)判为"无法定位"，
  // 绝不把非法范围 clamp 到错误行而显示假溯源（零伪造铁律）。
  const rawStart = block?.md_line_start ?? 0;
  const rawEnd = block?.md_line_end ?? 0;
  const locatable =
    !!block &&
    Number.isInteger(rawStart) &&
    Number.isInteger(rawEnd) &&
    rawStart >= 1 &&
    rawEnd >= rawStart &&
    rawStart <= lines.length;
  const start = locatable ? rawStart : 0;
  const end = locatable ? clamp(rawEnd, start, lines.length) : 0;

  // 像素档可用性（契约 §5.4，codex 终审 P2）：仅当坐标系已与原 PDF 对齐时才可像素级，
  // mineru_layout 等版面坐标系 ≠ PDF → 不可用（否则是不准确的能力展示）。还需有效页码 + 合法 bbox。
  const pixelAvailable = !!(
    structure?.has_bbox &&
    structure.bbox_coord_space &&
    PDF_ALIGNED_COORD_SPACES.has(structure.bbox_coord_space) &&
    focusPage &&
    focusPage >= 1 &&
    isValidBbox(focusBbox)
  );

  const out: React.ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const lineNo = i + 1;
    if (locatable && lineNo === start) {
      const segment = lines.slice(start - 1, end);
      out.push(
        <div
          key="sv-hl"
          ref={hlRef}
          className="sv-block sv-block-hl"
          data-block-highlight="true"
          data-source-anchor={anchorId ?? undefined}
          role={onSelectBlock && anchorId ? "button" : undefined}
          tabIndex={onSelectBlock && anchorId ? 0 : undefined}
          onClick={onSelectBlock && anchorId ? () => onSelectBlock(anchorId) : undefined}
          onKeyDown={
            onSelectBlock && anchorId
              ? (e) => {
                  if (e.key === "Enter" || e.key === " ") onSelectBlock(anchorId);
                }
              : undefined
          }
          aria-current="true"
        >
          {segment.map((ln, k) => (
            <div
              key={start + k}
              className="sv-line"
              data-md-line={start + k}
              dangerouslySetInnerHTML={{ __html: renderLineHtml(ln) }}
            />
          ))}
          {onSelectBlock && anchorId && (
            <span className="sv-backlink" aria-hidden="true" title="回链综述">↩</span>
          )}
        </div>,
      );
      i = end; // 跳过已并入高亮块的行
    } else {
      out.push(
        <div
          key={lineNo}
          className="sv-line"
          data-md-line={lineNo}
          dangerouslySetInnerHTML={{ __html: renderLineHtml(lines[i]) }}
        />,
      );
      i += 1;
    }
  }

  const crumbParts = [
    block?.section_title || structure?.blocks[0]?.section_title,
    block?.page_no ? `第 ${block.page_no} 页` : focusPage ? `第 ${focusPage} 页` : null,
  ].filter(Boolean);

  return (
    <div className="sv-pane">
      <div className="sv-head">
        <span className="sv-crumb muted">原文{crumbParts.length ? ` · ${crumbParts.join(" · ")}` : ""}</span>
        <span className="sv-mode-toggle" role="group" aria-label="高亮档位">
          <span className="sv-mode active">行级</span>
          <span className="sv-mode" aria-disabled={!pixelAvailable}>像素级</span>
        </span>
      </div>
      {block && !locatable && (
        <div className="sv-degrade muted">该引用在原文中无法精确定位（行范围异常），已跳过高亮</div>
      )}
      {locatable && !pixelAvailable && focusBbox && (
        <div className="sv-degrade muted">坐标系未校准，已降级为段级高亮</div>
      )}
      <div className="sv-body md">{out}</div>
    </div>
  );
}
