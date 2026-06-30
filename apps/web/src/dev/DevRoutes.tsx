/**
 * DevRoutes.tsx — 仅供 playwright/手动联调的开发路由（挂在 /dev/*）。
 *
 * 这些路由把溯源组件直挂、用 query 参数喂场景，playwright 全程用 page.route 注入契约
 * fixture（不依赖后端在线）。F2 起逐 task 在此追加子路由，routes.tsx 只接一次 /dev/*。
 */
import { useState, useEffect } from "react";
import { Routes, Route, useSearchParams } from "react-router-dom";
import { SourceViewer } from "../components/source/SourceViewer";
import { ReviewWithProvenance } from "../components/review/ReviewWithProvenance";
import { QualityPanel } from "../components/quality/QualityPanel";
import { ResearchView } from "../pages/ResearchView";
import { FIXTURE_PID, FIXTURE_CID } from "../api/research.fixtures";
import type { ProvenanceMap } from "../types/provenance";

function num(v: string | null, dflt: number): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : dflt;
}

/** 内联样例（形状严格按契约；anchor 指向 mock 结构里存在的 block_idx 1/2） */
const DEV_REVIEW_MD =
  "## 隐私保护研究综述\n\n研究普遍表明，[[anchor:ax1]]隐私保护技术在近年显著提升[[/anchor]]；" +
  "在量化层面，[[anchor:ax2]]零伪造率达到 100%[[/anchor]]，体现证据可信。\n";
const DEV_REVIEW_MAP: ProvenanceMap = {
  ax1: {
    paper_id: 1, attachment_id: 10, page_no: 1, block_idx: 1,
    bbox: [120, 200, 880, 268], table_idx: null, cell_row: null, cell_col: null,
    section_title: "引言", quote: "隐私保护技术在近年显著提升",
  },
  ax2: {
    paper_id: 1, attachment_id: 10, page_no: 2, block_idx: 2,
    bbox: [100, 300, 900, 520], table_idx: 0, cell_row: 1, cell_col: 1,
    section_title: "结果", quote: "零伪造率达到 100%",
  },
};
// 降级样例：正文含 anchor 标记但无 provenance_map → 应剥离标记为纯文本（无可点锚点）
const DEV_REVIEW_DEGRADE_MD =
  "## 综述\n\n这段含 [[anchor:axX]]溯源标记[[/anchor]] 但无映射，应剥离为纯文本，不报错也不可点。\n";

/** /dev/source-viewer?projectId=1&paperId=1&blockIdx=1 */
function DevSourceViewer() {
  const [sp] = useSearchParams();
  const projectId = num(sp.get("projectId"), 1);
  const paperId = num(sp.get("paperId"), 1);
  const blockIdxRaw = sp.get("blockIdx");
  const focusBlockIdx = blockIdxRaw == null ? null : num(blockIdxRaw, 0);
  return (
    <div className="container" style={{ paddingTop: "1rem" }}>
      <SourceViewer projectId={projectId} paperId={paperId} focusBlockIdx={focusBlockIdx} anchorId="dev" />
    </div>
  );
}

/** /dev/review-provenance?degrade=1 — 杀手锏 split-pane（degrade=1 走优雅降级）。
 *  playwright 可注入 window.__DEV_REVIEW__（Track A 真实 review fixture）联调真实数据。 */
function DevReviewProvenance() {
  const [sp] = useSearchParams();
  const projectId = num(sp.get("projectId"), 1);
  const degrade = sp.get("degrade") === "1";
  const jobId = sp.get("jobId");
  // ?jobId=N 时直接拉真实 ai-job（resultText + provenanceMap），联调真实数据
  const [fetched, setFetched] = useState<{ review_md: string; provenance_map: ProvenanceMap } | null>(null);
  useEffect(() => {
    if (!jobId) return;
    fetch(`/api/projects/${projectId}/ai/jobs/${jobId}`)
      .then((r) => r.json())
      .then((j) => setFetched({ review_md: j.resultText || "", provenance_map: (j.provenanceMap || {}) as ProvenanceMap }))
      .catch(() => {});
  }, [jobId, projectId]);
  const injected =
    (typeof window !== "undefined"
      ? (window as Window & { __DEV_REVIEW__?: { review_md: string; provenance_map: ProvenanceMap } }).__DEV_REVIEW__
      : undefined) || fetched || undefined;
  const reviewMd = degrade ? DEV_REVIEW_DEGRADE_MD : injected?.review_md ?? DEV_REVIEW_MD;
  const map = degrade ? undefined : injected?.provenance_map ?? DEV_REVIEW_MAP;
  return (
    <div className="container" style={{ paddingTop: "1rem", minHeight: "70vh" }}>
      <ReviewWithProvenance projectId={projectId} reviewMd={reviewMd} provenanceMap={map} />
    </div>
  );
}

/** /dev/quality?projectId=5 — 语料质检面板 */
function DevQuality() {
  const [sp] = useSearchParams();
  const projectId = num(sp.get("projectId"), 1);
  return (
    <div className="container" style={{ paddingTop: "1rem", maxWidth: 560 }}>
      <QualityPanel projectId={projectId} />
    </div>
  );
}

/** /dev/research — 研究副驾 HITL 全流程（discover→scratchpad→verify→verdict→accept）。
 *  playwright 用 page.route 注入研究契约 fixture（不依赖后端在线）。固定 pid/cid override。 */
function DevResearch() {
  return (
    <div className="container" style={{ paddingTop: "1rem" }}>
      <ResearchView projectId={FIXTURE_PID} corpusId={FIXTURE_CID} />
    </div>
  );
}

export function DevRoutes() {
  return (
    <Routes>
      <Route path="source-viewer" element={<DevSourceViewer />} />
      <Route path="review-provenance" element={<DevReviewProvenance />} />
      <Route path="quality" element={<DevQuality />} />
      <Route path="research" element={<DevResearch />} />
    </Routes>
  );
}
