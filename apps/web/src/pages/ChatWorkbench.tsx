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
import { useQueryClient } from "@tanstack/react-query";
import {
  useProject,
  useArtifacts,
  useCreateArtifact,
  useProjectLibraryStats,
  useGlobalLibraryStats,
  useRuns,
} from "../api/agentHooks";
import type { ArtifactItem } from "../api/agentHooks";
import { getRun } from "../api/client";
import type { FrontendEvidenceRef } from "../components/GroundingOverlay";
import { AgentChat } from "../components/AgentChat";
import { ArtifactCard } from "../components/ArtifactCard";
import { ArtifactCanvas } from "../components/ArtifactCanvas";
import { LibraryStatusBar } from "../components/LibraryStatusBar";
import { TrustCard } from "../components/TrustCard";
import { EmptyGuide } from "../components/EmptyGuide";
import { TrustBadgeStrip } from "../components/TrustBadgeStrip";
import type { FillPayload } from "../components/PresetLauncher";

export function ChatWorkbench() {
  const { pid } = useParams<{ pid: string }>();
  const pidNum = Number(pid);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { data } = useProject(pidNum);

  // W1: 文献库统计
  const { data: projectStats } = useProjectLibraryStats(pidNum);
  const { data: globalStats } = useGlobalLibraryStats();

  // 已 pin 工件（跨会话恢复，侧栏展示）
  const { data: pinnedData, refetch: refetchPinned } = useArtifacts(pidNum, true);
  const createArtifact = useCreateArtifact(pidNum);

  // Phase 2: 历史可见 TrustCard。进入页时拉本项目 runs，定位最新 status==="done" 的 run，
  // 让首次 CLI demo 产生的 run 一进对话页即可见可信凭证；新跑的 run 完成后覆盖（见下）。
  const { data: runsData } = useRuns(pidNum);
  const [trustRunId, setTrustRunId] = useState<number | null>(null);
  // 用户已在本会话跑过 run 后，不再被 runs 列表回拉覆盖（新 run 优先）。
  const trustPinnedRef = useRef(false);
  useEffect(() => {
    if (trustPinnedRef.current) return;
    const latestDone = runsData?.runs?.find((r) => r.status === "done");
    if (latestDone) setTrustRunId(Number(latestDone.runId));
  }, [runsData]);

  // 本次 run 产出的工件（本地临时，AgentChat 新 run 后清空）
  const [localArtifacts, setLocalArtifacts] = useState<ArtifactItem[]>([]);

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

  // Canvas 状态
  const [canvasArtifact, setCanvasArtifact] = useState<ArtifactItem | null>(null);
  const [canvasContent, setCanvasContent] = useState<string | null>(null);
  const [canvasEvidenceRefs, setCanvasEvidenceRefs] = useState<FrontendEvidenceRef[] | null>(null);

  // 去重: 已处理的 runId:eventSeq, 防同一 run_complete 回调重复造工件 (codex M4-P2#1)
  const processedRef = useRef<Set<string>>(new Set());

  /**
   * AgentChat run 完成回调 (codex M4-P2: 经 AgentChat onRunComplete prop 传入真实 runId,
   * 替代旧的 DOM MutationObserver + runId=-1。真实 runId → createArtifact 不再 404。)
   */
  const handleRunComplete = useCallback(
    async (runId: string, finalOutput: string, eventSeq: number) => {
      // 去重: 同一 run 的同一完成事件只造一次工件
      const dedupKey = `${runId}:${eventSeq}`;
      if (processedRef.current.has(dedupKey)) return;
      processedRef.current.add(dedupKey);

      // W4 Task 7: 标记已发起过 run，隐藏空状态引导
      setHasRun(true);

      // SSE run_complete 才是 agent 真正完成的时点；在此失效统计/项目缓存，
      // 确保读到 agent 工具（纳排/语料修改）之后的最新值。
      void qc.invalidateQueries({ queryKey: ["projectLibraryStats", pidNum] });
      void qc.invalidateQueries({ queryKey: ["globalLibraryStats"] });
      void qc.invalidateQueries({ queryKey: ["project", pidNum] });

      // 1. 派生工件标题（取 final_output 首行 heading，fallback 到运行时间戳）
      const titleMatch = finalOutput.match(/^#+\s+(.+)/m);
      const title = titleMatch ? titleMatch[1].trim() : `综述 ${new Date().toLocaleTimeString()}`;

      // 2. 持久化工件身份到后端（真实 runId + sourceEventSeq）
      try {
        const artifact = await createArtifact.mutateAsync({
          type: "review",
          title,
          runId: Number(runId),
          sourceEventSeq: eventSeq,
          contentRef: `run:${runId}`,
          pinned: false,
        });
        setLocalArtifacts((prev) => [artifact, ...prev]);
      } catch {
        // 持久化失败: 回退为本地临时工件(仍带真实 runId, 展开可加载内容), 不阻断 UX
        setLocalArtifacts((prev) => [
          {
            id: -1 * Date.now(),
            projectId: pidNum,
            runId: Number(runId),
            type: "review",
            title,
            contentRef: `run:${runId}`,
            pinned: false,
            order: 0,
          },
          ...prev,
        ]);
      }
    },
    [createArtifact, pidNum, qc],
  );

  // 展开 Canvas
  const handleExpand = useCallback(
    async (artifact: ArtifactItem) => {
      setCanvasArtifact(artifact);
      setCanvasContent(null);
      setCanvasEvidenceRefs(null);

      // 取 RunDetail（final_output + evidenceRefs）
      if (artifact.runId && artifact.runId > 0) {
        try {
          const detail = await getRun(pidNum, String(artifact.runId));
          setCanvasContent(detail.finalOutput ?? null);
          setCanvasEvidenceRefs(
            ((detail.evidenceRefs as FrontendEvidenceRef[] | null) ?? null),
          );
        } catch {
          setCanvasContent("（加载综述内容失败）");
        }
      }
    },
    [pidNum],
  );

  // 合并：本地工件 + 已 pin 工件（去重）
  const pinnedArtifacts = pinnedData?.artifacts ?? [];
  const pinnedIds = new Set(pinnedArtifacts.map((a) => a.id));
  const localOnly = localArtifacts.filter((a) => !pinnedIds.has(a.id));

  const hasCanvas = canvasArtifact !== null;

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
              fillPrompt={fillPrompt}
              onRunStart={() => setHasActivity(true)}
              onRunComplete={(info) => {
                setHasRun(true);
                // Phase 2: 新跑完的 run 覆盖历史 run（且后续不再被 runs 列表回拉覆盖）
                trustPinnedRef.current = true;
                setTrustRunId(Number(info.runId));
                void handleRunComplete(info.runId, info.finalOutput, info.eventSeq);
              }}
            />
          </div>

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
                  onExpand={handleExpand}
                />
              ))}
            </div>
          )}
        </div>

        {/* 侧栏：已 pin 工件 */}
        {!hasCanvas && (
          <aside className="workbench-aside">
            {/* 已 pin 工件（跨会话） */}
            {pinnedArtifacts.length > 0 && (
              <div className="card">
                <h3 style={{ marginTop: 0, fontSize: "0.95rem" }}>已 Pin 工件</h3>
                {pinnedArtifacts.map((art) => (
                  <ArtifactCard
                    key={art.id}
                    artifact={art}
                    projectId={pidNum}
                    onExpand={handleExpand}
                  />
                ))}
              </div>
            )}
          </aside>
        )}

        {/* ArtifactCanvas（右侧展开时替代侧栏） */}
        {hasCanvas && (
          <div className="workbench-canvas-pane">
            <ArtifactCanvas
              artifact={canvasArtifact}
              projectId={pidNum}
              content={canvasContent}
              evidenceRefs={canvasEvidenceRefs}
              onClose={() => {
                setCanvasArtifact(null);
                setCanvasContent(null);
                setCanvasEvidenceRefs(null);
                void refetchPinned();
              }}
            />
          </div>
        )}
      </div>
    </div>
  );
}
