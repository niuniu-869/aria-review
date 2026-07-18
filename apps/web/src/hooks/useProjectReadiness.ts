import { useMemo } from "react";
import type { ProjectDetail } from "../api/agentHooks";

export type ProjectReadinessStage = "no_papers" | "no_included" | "not_parsed" | "no_fulltext" | "ready";

export interface ProjectReadiness {
  stage: ProjectReadinessStage;
  label: string;
  actionText: string;
  actionHref: string;
}

export type ProjectReadinessStats = Pick<
  ProjectDetail,
  "paperCount" | "includedCount" | "readableFulltextCount"
> & {
  /**
   * F-12: OCR 解析完成篇数（来自 ProjectLibraryStats.ocr.done；项目详情 payload 本身无此信号）。
   * 缺省/null 表示未知，此时不细分 not_parsed，维持原 no_fulltext 文案。
   */
  ocrDoneCount?: number | null;
};

/** 将项目统计归一为各前端入口共用的就绪度语义。 */
export function getProjectReadiness(
  stats: ProjectReadinessStats | null | undefined,
  projectId: number,
): ProjectReadiness | undefined {
  if (!stats) return undefined;

  const libraryHref = `/projects/${projectId}/library`;
  if (stats.paperCount <= 0) {
    return {
      stage: "no_papers",
      label: "项目还没有文献",
      actionText: "去检索或导入文献",
      actionHref: libraryHref,
    };
  }
  if (stats.includedCount <= 0) {
    return {
      stage: "no_included",
      label: "已有题录，尚未纳入",
      actionText: "去筛选纳入",
      actionHref: libraryHref,
    };
  }
  if (stats.readableFulltextCount <= 0) {
    // F-12: 已纳入但一篇都未 OCR 解析时给出更准确的文案（区别于「有全文来源但未摄取」）
    if (stats.ocrDoneCount != null && stats.ocrDoneCount <= 0) {
      return {
        stage: "not_parsed",
        label: "文献尚未解析全文",
        actionText: "去文献库解析全文",
        actionHref: libraryHref,
      };
    }
    return {
      stage: "no_fulltext",
      label: "已纳入文献缺少可读全文",
      actionText: "去补充全文",
      actionHref: libraryHref,
    };
  }
  return {
    stage: "ready",
    label: "项目语料已就绪",
    actionText: "开始使用",
    actionHref: `/projects/${projectId}`,
  };
}

/** 薄 hook：仅为组件提供稳定的 selector 结果。 */
export function useProjectReadiness(
  stats: ProjectReadinessStats | null | undefined,
  projectId: number,
): ProjectReadiness | undefined {
  return useMemo(
    () => getProjectReadiness(stats, projectId),
    [projectId, stats?.includedCount, stats?.paperCount, stats?.readableFulltextCount, stats?.ocrDoneCount],
  );
}
