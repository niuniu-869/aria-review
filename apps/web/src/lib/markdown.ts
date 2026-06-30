// 安全 markdown 渲染 (对应 v0.6 render_markdown_safe: commonmark + 白名单 sanitize)
// 在此之上补两件事:
//   1. 流式安全 —— 渲染半成品正文时闭合未完成的代码围栏, 避免后文被吞;
//   2. 引用三色标记语义化 —— 把后端 cite_check 插入的行内 emoji 转成带文字的徽标。
import { marked } from "marked";
import DOMPurify from "dompurify";

marked.setOptions({ gfm: true, breaks: false });

// 引用校验三色标记。后端 cite_check._annotate 在正文行内插入 ✅/⚠️/❌ (CITE_MARK),
// 裸 emoji 嵌在中文正文里极难分辨, 这里统一转成带文字的语义徽标 (复用设计系统 .badge)。
type CiteKind = "ok" | "warn" | "bad";
const CITE: Record<CiteKind, { cls: string; label: string; title: string }> = {
  ok: { cls: "badge badge-ok cite-mark", label: "已核验", title: "DOI/PMID 精确命中语料" },
  warn: { cls: "badge badge-warn cite-mark", label: "待核", title: "作者+年模糊命中, 或编号待人工复核" },
  bad: { cls: "badge badge-danger cite-mark", label: "存疑", title: "语料中未找到, 疑似虚构" },
};

function citeBadge(kind: CiteKind): string {
  const b = CITE[kind];
  return `<span class="${b.cls}" title="${b.title}">${b.label}</span>`;
}

// 行内 emoji → 徽标 HTML (marked 原样透传行内 HTML, 随后由 DOMPurify 白名单清洗)。
function markCitations(md: string): string {
  return md
    .replace(/✅/g, citeBadge("ok")) // ✅
    .replace(/⚠️?/g, citeBadge("warn")) // ⚠ / ⚠️ (含可选变体选择符)
    .replace(/❌/g, citeBadge("bad")); // ❌
}

// 流式渲染时正文可能停在半截语法上, 最常见的是未闭合的 ``` 代码围栏——
// 它会把后续所有正文吞进代码块。补一个收尾围栏即可平滑渲染。
function balanceFences(md: string): string {
  const fences = (md.match(/```/g) || []).length;
  return fences % 2 === 1 ? `${md}\n\`\`\`` : md;
}

export interface CitationLinkRef {
  index: number;
  paperId: number;
  projectId: number;
  title?: string | null;
}

function linkCitationNumbers(md: string, refs: CitationLinkRef[] | undefined): string {
  if (!refs || refs.length === 0) return md;
  const byIndex = new Map(refs.map((ref) => [ref.index, ref]));
  return md.replace(/\[(\d{1,4})\]/g, (raw, nText: string) => {
    const ref = byIndex.get(Number(nText));
    if (!ref) return raw;
    const title = (ref.title || `文献 #${ref.paperId}`).replace(/"/g, "&quot;");
    const href = `/projects/${ref.projectId}/library/${ref.paperId}`;
    // data-paper-id: 溯源模式(ReviewWithProvenance)据此拦截点击→打开 SourceViewer 看原文，
    // preventDefault 阻止 href 跳库；非溯源渲染(AiMarkdown/GroundingOverlay)无拦截，仍按 href 跳库。
    return `<a class="citation-link" data-paper-id="${ref.paperId}" href="${href}" title="查看文献详情：${title}" aria-label="查看文献 ${nText} 的详情">${raw}</a>`;
  });
}

export function renderMarkdown(
  md: string,
  opts?: { streaming?: boolean; citationRefs?: CitationLinkRef[]; projectId?: number },
): string {
  const balanced = opts?.streaming ? balanceFences(md) : md;
  const linked = opts?.projectId
    ? linkCitationNumbers(
        balanced,
        opts.citationRefs?.map((ref) => ({ ...ref, projectId: opts.projectId as number })),
      )
    : balanced;
  const src = markCitations(linked);
  const html = marked.parse(src, { async: false }) as string;
  return DOMPurify.sanitize(html);
}
