/**
 * ReviewWithProvenance.tsx — ★杀手锏：可溯源综述（F3，markdown 级）。
 *
 * review_md 里 [[anchor:<id>]]命中文本[[/anchor]] → 可点击 .prov-anchor；点击查
 * provenance_map[id]:ProvenanceRef → 打开右侧 SourceViewer（同屏 split-pane），按
 * block 的 md_line 行级高亮原文并平滑滚动；双向联动：点原文高亮块反向高亮综述锚点。
 *
 * 安全（codex P1）：anchor 内文先 HTML 转义再受控生成 span，杜绝"结构逃逸"伪造可点锚点。
 * 引用不回退（codex P1）：复用 AiMarkdown 同一套 citation 渲染（[n] 链接 + 三色徽标）。
 * 优雅降级：provenance_map 为空 → 剥离 anchor 标记为纯文本（无可点锚点、无 split 右栏）。
 * occurrence 级（§5.5）：anchor_id 每个出现位置独立唯一，按 id 切 is-active / 反查互不串扰。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { renderMarkdown } from "../../lib/markdown";
import { useCitationRefs } from "../ai/AiStream";
import { SourceViewer } from "../source/SourceViewer";
import type { ProvenanceMap, ProvenanceRef } from "../../types/provenance";

export interface ReviewWithProvenanceProps {
  /** 综述所属项目（structure/markdown 端点项目作用域 + [n] 引用链接上下文） */
  projectId: number;
  /** 综述正文 markdown（含 [[anchor:id]]…[[/anchor]] 标记） */
  reviewMd: string;
  /** anchor_id → 溯源定位；为空时优雅降级为纯文本 */
  provenanceMap?: ProvenanceMap | null;
}

const ANCHOR_RE = /\[\[anchor:([A-Za-z0-9_-]+)\]\]([\s\S]*?)\[\[\/anchor\]\]/g;

/** HTML 转义 anchor 内文，杜绝结构逃逸（伪造 .prov-anchor）。 */
function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * 把 anchor 标记替换为受控的可点击 span（内文已转义；occurrence 级，每处独立可点）。
 * 零伪造（codex 终审 P1）：只对 map 中存在定位的 id 注入可点锚点；不在 map 的 anchor
 * 剥离为纯文本，绝不显示"可点溯源"假象。
 */
function injectAnchors(md: string, map: ProvenanceMap): string {
  return md.replace(ANCHOR_RE, (_m, id: string, text: string) => {
    if (!map[id]) return escapeHtml(text);
    return `<span class="prov-anchor" data-anchor-id="${id}" role="button" tabindex="0">${escapeHtml(text)}</span>`;
  });
}

/** 降级：剥离 anchor 标记，仅保留内文（纯文本综述）。 */
function stripAnchors(md: string): string {
  return md.replace(ANCHOR_RE, (_m, _id: string, text: string) => text);
}

/**
 * 中和正文里可能出现的"原生" .prov-anchor / data-anchor-id（模型直出或注入），
 * 确保只有受控的 [[anchor:id]] 注入才产生真锚点（零伪造闭合，codex 复审 P1）。
 * data-anchor-id / prov-anchor 在学术正文中不会作为正常词出现，全量中和安全。
 */
function neutralizeRawAnchors(md: string): string {
  return md.replace(/data-anchor-id/gi, "data-anchor-id-x").replace(/prov-anchor/gi, "prov-anchor-x");
}

function escapeId(id: string): string {
  if (typeof CSS !== "undefined" && typeof CSS.escape === "function") return CSS.escape(id);
  return id.replace(/['"\\]/g, "\\$&");
}

export function ReviewWithProvenance({ projectId, reviewMd, provenanceMap }: ReviewWithProvenanceProps) {
  const map = provenanceMap ?? {};
  const mapKey = Object.keys(map).sort().join(","); // 注入只取决于 map 的 id 集合
  const active = mapKey.length > 0; // 有定位映射才启用锚点交互

  // 复用 AiMarkdown 的 [n] 引用链接上下文（不回退既有引用功能）
  const citationRefs = useCitationRefs(projectId);
  const refsKey = citationRefs.map((r) => `${r.index}:${r.paperId}`).join("|");

  const html = useMemo(
    () => {
      const safeMd = neutralizeRawAnchors(reviewMd); // 先中和原生锚点，再受控注入
      return renderMarkdown(active ? injectAnchors(safeMd, map) : stripAnchors(safeMd), {
        citationRefs,
        projectId,
      });
    },
    // citationRefs/map 每渲染新引用，用稳定 refsKey/mapKey 作依赖避免抖动
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [reviewMd, projectId, refsKey, active, mapKey],
  );

  const reviewRef = useRef<HTMLDivElement>(null);
  const [selected, setSelected] = useState<{ anchorId: string; ref: ProvenanceRef } | null>(null);

  const openAnchor = useCallback(
    (id: string) => {
      const ref = map[id];
      if (ref) setSelected({ anchorId: id, ref });
    },
    [map],
  );

  // 引用编号 [n] 点击 → 打开该 paper 原文（paper 级，无具体 block 高亮）。统一「点引用→看原文」，
  // 不再跳文献库（修复：此前 [n] 是 <a href=库>，浏览器导航抢先，SourceViewer 没机会）。
  // block_idx=null → SourceViewer 显示该 paper 全文、不做行级高亮。
  const openPaper = useCallback((paperId: number) => {
    if (!(paperId > 0)) return;
    const ref: ProvenanceRef = {
      paper_id: paperId, attachment_id: null, page_no: null, block_idx: null,
      bbox: null, table_idx: null, cell_row: null, cell_col: null,
      section_title: null, quote: "",
    };
    setSelected({ anchorId: `cite:${paperId}`, ref });
  }, []);

  // 左栏锚点点击（事件委托：raw HTML span 非 React 节点）
  const onReviewClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      const target = e.target as HTMLElement;
      // 1) 命中文本 prov-anchor → 精确 block 高亮。
      // preventDefault 必须在此调用：综述里 [n] 常被 [[anchor]] 包裹（命中文本含引用编号），
      // 点击目标是 anchor 内层的 <a class=citation-link href>，不阻止默认就原生跳库
      // （dogfood P0：本场景多数引用属此种，上轮只在分支2 preventDefault，漏了这里）。
      const anchorId = target.closest(".prov-anchor")?.getAttribute("data-anchor-id");
      if (anchorId) {
        e.preventDefault();
        openAnchor(anchorId);
        return;
      }
      // 2) 引用编号 [n] → 打开该 paper 原文，阻止 <a href> 跳文献库
      const citeEl = target.closest("a.citation-link");
      if (citeEl) {
        const pid = citeEl.getAttribute("data-paper-id");
        if (pid) {
          e.preventDefault();
          openPaper(Number(pid));
        }
      }
    },
    [openAnchor, openPaper],
  );

  const onReviewKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      const target = e.target as HTMLElement;
      const anchorId = target.closest(".prov-anchor")?.getAttribute("data-anchor-id");
      if (anchorId) {
        e.preventDefault();
        openAnchor(anchorId);
        return;
      }
      const citeEl = target.closest("a.citation-link");
      if (citeEl) {
        const pid = citeEl.getAttribute("data-paper-id");
        if (pid) {
          e.preventDefault();
          openPaper(Number(pid));
        }
      }
    },
    [openAnchor, openPaper],
  );

  // 右栏点高亮块 → 反向高亮综述锚点（双向联动）。id occurrence 唯一，querySelector 命中该处。
  const onSelectBlock = useCallback(
    (id: string) => {
      const ref = map[id];
      if (ref) setSelected({ anchorId: id, ref });
      reviewRef.current
        ?.querySelector<HTMLElement>(`.prov-anchor[data-anchor-id="${escapeId(id)}"]`)
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
    },
    [map],
  );

  // 同步锚点选中态（is-active）。id occurrence 唯一 → 只命中当前出现位置。
  useEffect(() => {
    const root = reviewRef.current;
    if (!root) return;
    root.querySelectorAll<HTMLElement>(".prov-anchor").forEach((el) => {
      el.classList.toggle("is-active", el.getAttribute("data-anchor-id") === selected?.anchorId);
    });
  }, [selected, html]);

  const open = !!selected;

  return (
    <div className={`prov-split split-pane${open ? " open" : ""}`}>
      <div
        ref={reviewRef}
        className="prov-review md"
        onClick={active ? onReviewClick : undefined}
        onKeyDown={active ? onReviewKeyDown : undefined}
        dangerouslySetInnerHTML={{ __html: html }}
      />
      {selected && (
        <SourceViewer
          projectId={projectId}
          paperId={selected.ref.paper_id}
          focusBlockIdx={selected.ref.block_idx}
          focusBbox={selected.ref.bbox}
          focusPage={selected.ref.page_no}
          anchorId={selected.anchorId}
          onSelectBlock={onSelectBlock}
        />
      )}
    </div>
  );
}
