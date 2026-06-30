/**
 * structure.ts — 可信溯源数据接入（项目作用域，契约 §5.2）。
 *
 * 真实端点（Track A 提供）：
 *   getStructure(pid, paperId) → GET /projects/{pid}/papers/{paperId}/structure
 *   getMarkdown(pid, paperId)  → GET /projects/{pid}/papers/{paperId}/markdown（既有端点复用）
 *
 * 实现集中在 client.ts（与其它端点同源，统一 ApiError/BASE）；本模块作类型化门面。
 * playwright 全程用 page.route 注入 fixture，不依赖后端在线（联调只在 F6）。
 */
import { getStructure, getPaperMarkdown } from "./client";
import type { StructureResponse, MarkdownResponse } from "../types/provenance";

export { getStructure };
export type { StructureResponse, MarkdownResponse };

/** 原文 markdown（契约 §2.2）。复用既有 getPaperMarkdown，返回形状与契约一致。 */
export async function getMarkdown(
  pid: number,
  paperId: number,
): Promise<MarkdownResponse> {
  return getPaperMarkdown(pid, paperId);
}
