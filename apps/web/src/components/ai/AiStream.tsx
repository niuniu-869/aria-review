/**
 * AiStream.tsx — 统一流式 / 结果 markdown 展示 (A7)
 *
 * 三面板共用: 流式中用 renderMarkdown(streaming) 平滑渲染半成品; 完成后渲染最终 markdown。
 * 统一排版 (.md), aria-live 无障碍。不直接渲染裸文本 — 复用既有安全 renderMarkdown + DOMPurify。
 */
import { useProjectPapers } from "../../api/agentHooks";
import { renderMarkdown, type CitationLinkRef } from "../../lib/markdown";

export function useCitationRefs(projectId?: number | string): CitationLinkRef[] {
  const pid = Number(projectId);
  const { data } = useProjectPapers(Number.isFinite(pid) ? pid : 0);
  const included = (data?.papers ?? []).filter((p) => p.inclusionStatus === "included");
  return included.map((p, i) => ({
    index: i + 1,
    projectId: pid,
    paperId: p.paperId,
    title: p.title,
  }));
}

export function AiMarkdown({
  content,
  streaming = false,
  live = false,
  projectId,
}: {
  content: string;
  /** 流式中 (平滑闭合未完成围栏) */
  streaming?: boolean;
  /** aria-live="polite" (流式增量更新时用) */
  live?: boolean;
  /** 传入项目 id 后，[n] 引用会跳转到对应文献详情。 */
  projectId?: number | string;
}) {
  const citationRefs = useCitationRefs(projectId);
  return (
    <div
      className="md ai-markdown"
      aria-live={live ? "polite" : undefined}
      dangerouslySetInnerHTML={{
        __html: renderMarkdown(content, { streaming, citationRefs, projectId: Number(projectId) || undefined }),
      }}
    />
  );
}
