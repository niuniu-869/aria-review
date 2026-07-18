/**
 * ChatWorkbench.tsx — 对话工作台（M4 工件化）
 *
 * 在 M0 壳基础上新增：
 *   1. ArtifactCard —— run_complete 产出以工件卡形式呈现（类型徽章+标题+操作）
 *   2. ArtifactCanvas —— 右侧可折叠 Canvas，展开显示综述全文 + GroundingOverlay
 *   3. pin 持久化 —— 调后端 artifacts 端点存 pin 状态
 *   4. pinned 工件列表 —— 侧栏展示已 pin 工件（跨会话恢复）
 *
 * 架构约束：
 *   - 不重写 AgentChat/RunTimeline 内部逻辑
 *   - 工件内容派生自 AgentChat 暴露的 run_complete.final_output（通过 onRunComplete 回调）
 *   - 工件身份/pin 持久化调 /projects/{pid}/artifacts 端点
 *
 * grounding 数据来源：
 *   - AgentRun.evidenceRefs（通过 getRun 端点取回）
 *   - 每条 EvidenceRef 含 span（引用文本）、claim（上下文句子，可能为 null）、paper_id
 *   - 当前实现：句级 grounding（有 claim 时）/ 引用列表（无 claim 时降级）
 *   - TODO: claim 字段须 GuardedStream 在综述流时填充才有效；现有 AgentChat 综述路径可能不走 GuardedStream
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  useProject,
  useArtifacts,
  useProjectLibraryStats,
  useGlobalLibraryStats,
  useRuns,
} from "../api/agentHooks";
import { RUN_STATUS_DONE } from "../api/runStatus";
import { useArtifactCanvas } from "../hooks/useArtifactCanvas";
import { useProjectReadiness } from "../hooks/useProjectReadiness";
import { useQueryClient } from "@tanstack/react-query";
import { AgentChat } from "../components/AgentChat";
import { SearchNextStepCard } from "../components/SearchNextStepCard";
import { ArtifactCard } from "../components/ArtifactCard";
import { ArtifactCanvas } from "../components/ArtifactCanvas";
import { LibraryStatusBar } from "../components/LibraryStatusBar";
import { TrustCard } from "../components/TrustCard";
import { EmptyGuide } from "../components/EmptyGuide";
import { TrustBadgeStrip } from "../components/TrustBadgeStrip";
import { ErrMsg, Loading } from "../lib/ui";
import { track } from "../lib/track";
import type { FillPayload } from "../components/PresetLauncher";

export function ChatWorkbench() {
  const { pid } = useParams<{ pid: string }>();
  const pidNum = Number(pid);
  const navigate = useNavigate();
  const { data } = useProject(pidNum);
  const queryClient = useQueryClient();
  // S7: 检索 run 完成后的下一步推荐卡；关闭后本次会话不再弹出。
  const [showSearchNextStep, setShowSearchNextStep] = useState(false);
  const searchNextStepDismissedRef = useRef(false);

  // W1: 文献库统计
  const { data: projectStats } = useProjectLibraryStats(pidNum);
  const { data: globalStats } = useGlobalLibraryStats();

  // F-12: 项目详情本身无 OCR 计数，补入文献库统计的 ocr.done，
  // 让 readiness 能区分「已纳入但未解析全文」（见 useProjectReadiness not_parsed）。
  const readiness = useProjectReadiness(
    data ? { ...data, ocrDoneCount: projectStats?.ocr.done ?? null } : undefined,
    pidNum,
  );

  // 已 pin 工件（跨会话恢复，侧栏展示）
  const {
    data: pinnedData,
    isLoading: pinnedLoading,
    error: pinnedError,
    refetch: refetchPinned,
  } = useArtifacts(pidNum, true);

  // Phase 2: 历史可见 TrustCard。进入页时拉本项目 runs，定位最新 done run，
  // 让首次 CLI demo 产生的 run 一进对话页即可见可信凭证；新跑的 run 完成后覆盖（见下）。
  const { data: runsData } = useRuns(pidNum);
  const [trustRunId, setTrustRunId] = useState<number | null>(null);
  // 用户已在本会话跑过 run 后，不再被 runs 列表回拉覆盖（新 run 优先）。
  const trustPinnedRef = useRef(false);
  useEffect(() => {
    if (trustPinnedRef.current) return;
    const latestDone = runsData?.runs?.find((r) => r.status === RUN_STATUS_DONE);
    if (latestDone) setTrustRunId(Number(latestDone.runId));
  }, [runsData]);

  // F-07: 最近 done 的至多 3 条 run（列表新→旧），传给 AgentChat 渲染只读「历史运行」区
  const historyRunIds = (runsData?.runs ?? [])
    .filter((r) => r.status === RUN_STATUS_DONE)
    .slice(0, 3)
    .map((r) => Number(r.runId));

  const artifactCanvas = useArtifactCanvas(pidNum);

  // W4 Task 7: 预设/能力卡/建议追问注入输入框
  // I-1 修复：使用 {text, seq} 对象，seq 每次递增，确保同一文本二次点击也触发 useEffect
  const [fillPrompt, setFillPrompt] = useState<{ text: string; seq: number } | null>(null);
  // W4 Task 7: 是否已发起过至少一次 run（用于空状态判断）
  const [hasRun, setHasRun] = useState(false);
  // I-2 修复：run 已开始（running 中）时也隐藏引导，避免引导与对话流并存
  const [hasActivity, setHasActivity] = useState(false);

  // 处理 EmptyGuide / 建议追问 onFill：更新 fillPrompt 触发 AgentChat 注入（不自动发送）
  const handleFill = useCallback((payload: FillPayload) => {
    setFillPrompt((prev) => ({ text: payload.prompt, seq: (prev?.seq ?? 0) + 1 }));
  }, []);

  // 合并：本地工件 + 已 pin 工件（去重）
  const pinnedArtifacts = pinnedData?.artifacts ?? [];
  const pinnedIds = new Set(pinnedArtifacts.map((a) => a.id));
  const localOnly = artifactCanvas.localArtifacts.filter((a) => !pinnedIds.has(a.id));
  // 有缓存数据时后台刷新失败不阻断列表（stale-while-error），只在首载失败时展示错误
  const pinnedDisplayError = pinnedError && pinnedData == null
    ? Object.assign(new Error("已 Pin 工件加载失败，请重试。"), {
        originalMessage: pinnedError instanceof Error ? pinnedError.message : String(pinnedError),
      })
    : null;

  const hasCanvas = artifactCanvas.hasCanvas;

  // activeCorpus 摘要传给 LibraryStatusBar
  const activeCorpus = data?.activeCorpus ?? null;
  const corpusSummary = activeCorpus
    ? {
        status: activeCorpus.status,
        documentCount: activeCorpus.documentCount,
        stale: activeCorpus.stale,
      }
    : null;

  return (
    <div className="workbench-container">
      {/* Phase 5: 全局可信主张徽章条（轻量、状态栏之上；与本次运行实测的 TrustCard 共存互补） */}
      <TrustBadgeStrip />
      {/* W1: 文献库状态栏（导航下、对话区上） */}
      <div style={{ margin: "0 -1.5rem 1rem" }}>
        <LibraryStatusBar
          stats={projectStats ?? null}
          globalTotal={globalStats?.totalPapers ?? null}
          corpus={corpusSummary}
        />
      </div>
      <div className={`workbench-layout ${hasCanvas ? "workbench-with-canvas" : ""}`}>
        {/* 主区：对话 */}
        <div className="workbench-main">
          {/* Phase 2: 可信凭证卡（历史可见）—— 状态栏之下、对话卡之上的醒目位置。
              首次 CLI demo 产生的 done run，一进项目对话页即可见可验证 RunLog/grounding。 */}
          {trustRunId !== null && trustRunId > 0 && (
            <div style={{ marginBottom: "1rem" }}>
              <TrustCard projectId={pidNum} runId={trustRunId} />
            </div>
          )}
          {/* W4 Task 7: 空状态引导（未发起任何活动时，在输入框上方渲染） */}
          {/* I-2 修复：hasActivity（run 已开始）或 hasRun（run 已完成）时都不显示引导 */}
          {!hasActivity && !hasRun && (
            <EmptyGuide
              onFill={handleFill}
              stats={projectStats ?? null}
              onNavigate={(to) => navigate(`/projects/${pidNum}/${to}`)}
            />
          )}
          <div className="card">
            {/* M4: AgentChat 经 onRunComplete prop 回调真实 runId + finalOutput */}
            {/* W4 Task 7: fillPrompt prop 注入预设/建议追问文本（不自动发送） */}
            <AgentChat
              projectId={pidNum}
              readiness={readiness}
              fillPrompt={fillPrompt}
              historyRunIds={historyRunIds}
              onRunStart={({ entry }) => {
                setHasActivity(true);
                setShowSearchNextStep(false);
                if (entry === "search") track("search_run_start", { entry }, pidNum);
              }}
              onRunComplete={(info) => {
                setHasRun(true);
                if (info.entry === "search") {
                  track("search_run_done", { entry: info.entry, status: info.status }, pidNum);
                  // 检索可能已入库新文献：失效项目/库统计，让 readiness 与状态栏拿到新数据。
                  // codex 复核 P1：须等重拉完成再显示推荐卡，否则会按旧 readiness 闪错文案并误报曝光。
                  const refreshed = Promise.all([
                    queryClient.invalidateQueries({ queryKey: ["project", pidNum] }),
                    queryClient.invalidateQueries({ queryKey: ["projectLibraryStats", pidNum] }),
                    queryClient.invalidateQueries({ queryKey: ["projectPapers", pidNum] }),
                  ]);
                  if (info.status === "done" && !searchNextStepDismissedRef.current) {
                    void refreshed.finally(() => {
                      if (!searchNextStepDismissedRef.current) setShowSearchNextStep(true);
                    });
                  }
                }
                // Phase 2: 新跑完的 run 覆盖历史 run（且后续不再被 runs 列表回拉覆盖）
                trustPinnedRef.current = true;
                setTrustRunId(Number(info.runId));
                void artifactCanvas.handleRunComplete(info);
              }}
            />
          </div>

          {/* S7: 检索完成时刻的状态化下一步推荐（readiness 驱动，可关闭） */}
          {showSearchNextStep && readiness && (
            <div style={{ marginTop: "1rem" }}>
              <SearchNextStepCard
                projectId={pidNum}
                readiness={readiness}
                onClose={() => {
                  searchNextStepDismissedRef.current = true;
                  setShowSearchNextStep(false);
                }}
              />
            </div>
          )}

          {/* 本次 run 产出的工件卡（本次会话） */}
          {localOnly.length > 0 && (
            <div className="artifact-list" style={{ marginTop: "1rem" }}>
              <div
                style={{
                  fontSize: "0.78rem",
                  fontWeight: 600,
                  color: "var(--ink-3)",
                  marginBottom: "0.5rem",
                }}
              >
                本次产出
              </div>
              {localOnly.map((art) => (
                <ArtifactCard
                  key={art.id}
                  artifact={art}
                  projectId={pidNum}
                  onExpand={artifactCanvas.handleExpand}
                  onRetryPersist={artifactCanvas.handleRetryPersist}
                />
              ))}
            </div>
          )}
        </div>

        {/* 侧栏：已 pin 工件 */}
        {!hasCanvas && (
          <aside className="workbench-aside">
            {/* 已 pin 工件（跨会话） */}
            {pinnedLoading && (
              <div className="card">
                <h3 style={{ marginTop: 0, fontSize: "0.95rem" }}>已 Pin 工件</h3>
                <Loading label="加载已 Pin 工件…" />
              </div>
            )}
            {pinnedDisplayError && (
              <div className="card">
                <h3 style={{ marginTop: 0, fontSize: "0.95rem" }}>已 Pin 工件</h3>
                <ErrMsg
                  error={pinnedDisplayError}
                  action={
                    <button type="button" className="btn btn-ghost" onClick={() => void refetchPinned()}>
                      重试
                    </button>
                  }
                />
              </div>
            )}
            {!pinnedLoading && !pinnedDisplayError && pinnedArtifacts.length > 0 && (
              <div className="card">
                <h3 style={{ marginTop: 0, fontSize: "0.95rem" }}>已 Pin 工件</h3>
                {pinnedArtifacts.map((art) => (
                  <ArtifactCard
                    key={art.id}
                    artifact={art}
                    projectId={pidNum}
                    onExpand={artifactCanvas.handleExpand}
                  />
                ))}
              </div>
            )}
          </aside>
        )}

        {/* ArtifactCanvas（右侧展开时替代侧栏） */}
        {hasCanvas && (
          <div className="workbench-canvas-pane">
            {artifactCanvas.canvasArtifact?.runId
              && Number(artifactCanvas.canvasArtifact.runId) > 0
              && artifactCanvas.canvasContentState.error && (
              <div className="card" style={{ marginBottom: "0.75rem" }}>
                <span style={{ color: "var(--danger)" }}>加载工件内容失败。</span>
                <button
                  type="button"
                  className="btn btn-ghost"
                  style={{ marginLeft: "0.75rem" }}
                  onClick={artifactCanvas.canvasContentState.retry}
                >
                  重试
                </button>
              </div>
            )}
            <ArtifactCanvas
              artifact={artifactCanvas.canvasArtifact}
              projectId={pidNum}
              content={artifactCanvas.canvasContent}
              evidenceRefs={artifactCanvas.canvasEvidenceRefs}
              onClose={() => {
                artifactCanvas.setCanvasArtifact(null);
                void refetchPinned();
              }}
            />
          </div>
        )}
      </div>
    </div>
  );
}
