/**
 * ResearchView.tsx — 研究副驾工作台（B5 接线 / HITL 全流程）。
 *
 * 把 B2/B3/B4 三视图编排成人在环上的研究空白发现闭环：
 *   discover → scratchpad 实时累积 → 选中 GAP → verify 价值核验 → ValueVerdict → accept/reject/revise。
 * 单一 scratchpad 数据源（useScratchpad）同时喂 GapPanel(结构化browse) 与 ScratchpadLive(实时feed)，
 * 避免双拉。所有裁决浮现给人审，绝不自动定稿（HITL 红线）。
 *
 * 路由：/projects/:pid/research（pid 取自 params）。
 * dev/e2e：可传 projectId/corpusId override，跳过 project 拉取（见 DevRoutes /dev/research）。
 */
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import {
  useProject,
  useDiscoverGaps,
  useLatestGapDiscoverRun,
  useScratchpad,
  useVerifyGap,
  useGapVerdict,
  useFeasibilityVerify,
  useFeasibilityVerdict,
  isFeasibilityVerdictPending,
  usePatchGap,
  useAiJob,
  getActiveRCorpusId,
} from "../api/agentHooks";
import { ApiError } from "../api/client";
import { isTerminalScratchpadRunStatus } from "../api/runStatus";
import { asRCorpusId } from "../api/corpusIds";
import type { GapCandidate, GapPatchAction } from "../types/research";
import { ErrMsg } from "../lib/ui";
import { ProjectGate } from "../components/ProjectGate";
import { GapPanel } from "../components/research/GapPanel";
import { ScratchpadLive } from "../components/research/ScratchpadLive";
import { GapRunTimeline } from "../components/research/GapRunTimeline";
import { ValueVerdictCard } from "../components/research/ValueVerdictCard";
import { FeasibilityVerdictCard } from "../components/research/FeasibilityVerdictCard";
import { useGapRunStream } from "../hooks/useGapRunStream";
import { track } from "../lib/track";

function is404(err: unknown): boolean {
  return err instanceof ApiError && err.status === 404;
}

// 未核验 409/404 现由 useFeasibilityVerdict 静默为 {pending:true} 哨兵（F-20）；
// 本守卫仅兜底历史/竞态下仍以错误形式暴露的「未就绪」，与 pending 一样不显示错误。
function isFeasibilityNotReady(err: unknown): boolean {
  return err instanceof ApiError
    && (err.status === 404 || (err.status === 409 && err.code === "GAP_NOT_FEASIBILITY_CHECKED"));
}

type GapDiagnostic = {
  structured: boolean;
  includedCount?: number;
  includedWithFulltext?: number;
  fulltextEligibleCount?: number;
  candidatesWithFulltextCount?: number;
  message?: string;
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function numberField(source: Record<string, unknown>, key: keyof GapDiagnostic): number | undefined {
  const value = source[key];
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function parseGapDiagnostic(error: unknown): GapDiagnostic | null {
  if (!(error instanceof ApiError) || error.status !== 400) return null;
  const detail = asRecord(error.detail);
  if (!detail) return { structured: false, message: error.message };
  return {
    structured: true,
    includedCount: numberField(detail, "includedCount"),
    includedWithFulltext: numberField(detail, "includedWithFulltext"),
    fulltextEligibleCount: numberField(detail, "fulltextEligibleCount"),
    candidatesWithFulltextCount: numberField(detail, "candidatesWithFulltextCount"),
    message: typeof detail.message === "string" ? detail.message : error.message,
  };
}

function GapReadinessCard({ error, projectId }: { error: unknown; projectId: number }) {
  const diagnostic = parseGapDiagnostic(error);
  if (!diagnostic) return <ErrMsg error={error} />;

  const includedCount = diagnostic.includedCount ?? 0;
  const includedWithFulltext = diagnostic.includedWithFulltext ?? 0;
  const fulltextEligibleCount = diagnostic.fulltextEligibleCount ?? 0;
  const candidatesWithFulltextCount = diagnostic.candidatesWithFulltextCount ?? 0;
  const conditions = [
    { label: "已有纳入文献", ok: includedCount > 0, value: `${includedCount} 篇` },
    { label: "纳入文献含可读全文", ok: includedWithFulltext > 0, value: `${includedWithFulltext} 篇` },
    { label: "可自动补全文的题录", ok: fulltextEligibleCount > 0, value: `${fulltextEligibleCount} 篇` },
    { label: "已有全文但尚未纳入", ok: candidatesWithFulltextCount > 0, value: `${candidatesWithFulltextCount} 篇` },
  ];

  return (
    <div className="research-readiness card" role="alert">
      <div className="research-readiness-head">
        <h3 className="research-readiness-title">研究空白发现条件未满足</h3>
        <p className="research-readiness-msg">
          {diagnostic.message ?? "项目暂无可用于精读的纳入全文语料。"}
        </p>
      </div>
      {diagnostic.structured && (
        <ul className="research-readiness-list">
          {conditions.map((item) => (
            <li key={item.label} className={item.ok ? "is-ok" : "is-missing"}>
              <span className="research-readiness-dot" aria-hidden="true">{item.ok ? "✓" : "!"}</span>
              <span>{item.label}</span>
              <strong>{item.value}</strong>
            </li>
          ))}
        </ul>
      )}
      <div className="research-readiness-actions">
        <Link className="btn btn-primary" to={`/projects/${projectId}/library`}>
          去文献库补全文
        </Link>
        <Link className="btn" to={`/projects/${projectId}/library`}>
          去筛选纳入
        </Link>
        <Link className="btn" to={`/projects/${projectId}/library`}>
          去上传 PDF
        </Link>
      </div>
    </div>
  );
}

export interface ResearchViewProps {
  /** dev/e2e override；缺省取自路由 params */
  projectId?: number;
  /** dev/e2e override；缺省取自 activeCorpus.rCorpusId */
  corpusId?: string;
}

export function ResearchView({ projectId: pidProp, corpusId: cidProp }: ResearchViewProps = {}) {
  const params = useParams<{ pid: string }>();
  const queryClient = useQueryClient();
  const pid = pidProp ?? Number(params.pid);
  const validPid = Number.isFinite(pid) && pid > 0;

  useEffect(() => {
    track("gap_view", undefined, pid);
    // 每次研究区组件挂载仅上报一次。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 有 corpusId override(dev) 时不拉 project；否则从 activeCorpus 取就绪的 R 语料 id
  const project = useProject(cidProp || !validPid ? 0 : pid);
  const activeCorpus = project.data?.activeCorpus ?? null;
  const cid = cidProp ? asRCorpusId(cidProp) : getActiveRCorpusId(activeCorpus);

  const discover = useDiscoverGaps(pid);
  const [runId, setRunId] = useState<string | null>(null);
  const [selectedGapId, setSelectedGapId] = useState<string | null>(null);
  // codex A3-P2：切换项目(同组件实例复用)时清空 run/选中，避免用旧项目 run id 请求新项目 scratchpad。
  useEffect(() => {
    setRunId(null);
    setSelectedGapId(null);
  }, [pid]);
  // A3-P1：挂载/刷新时回填本项目最近一次 gap_discover run，避免刷新后已发现的 GAP 全消失需重跑。
  // run_id 即 str(job.id)（routes_research discover 返回 {run_id: str(job.id)}），故用 job.id 回填正确。
  const latestRun = useLatestGapDiscoverRun(validPid ? pid : 0);
  useEffect(() => {
    if (runId == null) {
      const last = latestRun.data?.jobs?.[0];
      if (last) setRunId(String(last.id));
    }
  }, [latestRun.data, runId]);
  const scratchpad = useScratchpad(pid, runId);
  const gaps: GapCandidate[] = scratchpad.data?.entries ?? [];

  // F-24: scratchpad 轮询在 run 终态即停，停前做最后一次失效重拉，
  // 保证 GAP 计数/状态与后端最终落库一致。每个 run id 只触发一次。
  const scratchpadFinalRef = useRef<string | null>(null);
  const scratchpadRunStatus = scratchpad.data?.run_status;
  useEffect(() => {
    if (!runId || scratchpadFinalRef.current === runId) return;
    if (!isTerminalScratchpadRunStatus(scratchpadRunStatus)) return;
    scratchpadFinalRef.current = runId;
    void queryClient.invalidateQueries({ queryKey: ["scratchpad", pid] });
  }, [pid, queryClient, runId, scratchpadRunStatus]);

  const selectedGap = gaps.find((g) => g.gap_id === selectedGapId) ?? null;

  const verify = useVerifyGap(pid);
  const [verifyingGap, setVerifyingGap] = useState<{ gapId: string; runId: string | null } | null>(null);
  const feasibilityVerify = useFeasibilityVerify(pid);
  const [feasibilityRun, setFeasibilityRun] = useState<{ gapId: string; runId: string | null } | null>(null);
  useEffect(() => {
    setVerifyingGap(null);
    setFeasibilityRun(null);
  }, [pid]);
  // P1 可观测：优先流当前核验 run（较短），否则流发现 run（长精读黑箱）。SSE 实时冒
  // 精读 N/M + subagent 活动；run 终态后 GapRunTimeline 自动隐藏。
  const activeGapRunId = feasibilityRun?.runId ?? verifyingGap?.runId ?? runId;
  const gapProgress = useGapRunStream(pid, activeGapRunId, { enabled: validPid });
  const verifyJobId = verifyingGap?.runId && /^\d+$/.test(verifyingGap.runId)
    ? Number(verifyingGap.runId)
    : null;
  const verifyJob = useAiJob(pid, verifyJobId, { enabled: !!verifyingGap, pollMs: 4000 });
  const verifyJobStatus = verifyJob.data?.status;
  const currentGapVerifying = !!selectedGap && verifyingGap?.gapId === selectedGap.gap_id;
  const verifyFailed = currentGapVerifying && (verifyJobStatus === "failed" || verifyJobStatus === "cancelled");
  const feasibilityJobId = feasibilityRun?.runId && /^\d+$/.test(feasibilityRun.runId)
    ? Number(feasibilityRun.runId)
    : null;
  const feasibilityJob = useAiJob(pid, feasibilityJobId, { enabled: !!feasibilityRun, pollMs: 4000 });
  const currentGapFeasibility = !!selectedGap && feasibilityRun?.gapId === selectedGap.gap_id;
  const feasibilityFailed = currentGapFeasibility
    && (feasibilityJob.data?.status === "failed" || feasibilityJob.data?.status === "cancelled");
  // A3-P2：verify 异步约数分钟，给等待加已耗时计时（否则盲等无反馈）。
  const [verifyElapsed, setVerifyElapsed] = useState(0);
  const [verifyStartedAt, setVerifyStartedAt] = useState<number | null>(null);
  useEffect(() => {
    if (!verifyingGap || verifyStartedAt == null) {
      setVerifyElapsed(0);
      return;
    }
    const tick = () => setVerifyElapsed(Math.floor((Date.now() - verifyStartedAt) / 1000));
    tick();
    const iv = setInterval(tick, 1000);
    return () => clearInterval(iv);
  }, [verifyingGap, verifyStartedAt]);
  const needsVerdict = !!selectedGap && (selectedGap.status !== "draft" || currentGapVerifying);
  const shouldPollVerdict = !verifyFailed;
  // 裁决异步产出：verify 后短暂 404 时继续轮询，避免「无 verdict/无错/无加载」的空白态（codex B5-P2）
  const verdict = useGapVerdict(pid, needsVerdict ? selectedGapId : null, {
    poll: shouldPollVerdict,
    pollMs: 4000,
  });
  const feasibilityVerdict = useFeasibilityVerdict(pid, selectedGapId, {
    poll: currentGapFeasibility && !feasibilityFailed,
    pollMs: 4000,
  });
  const patch = usePatchGap(pid);

  useEffect(() => {
    if (!verifyingGap || !verdict.data || verdict.data.gap_id !== verifyingGap.gapId) return;
    void queryClient.invalidateQueries({ queryKey: ["scratchpad", pid] });
  }, [pid, queryClient, verdict.data, verifyingGap]);

  useEffect(() => {
    if (!verifyingGap || !selectedGap || selectedGap.gap_id !== verifyingGap.gapId) return;
    if (selectedGap.status !== "draft") setVerifyingGap(null);
  }, [selectedGap, verifyingGap]);

  useEffect(() => {
    if (!feasibilityRun || !feasibilityVerdict.data) return;
    if (isFeasibilityVerdictPending(feasibilityVerdict.data)) return;
    if (feasibilityVerdict.data.gap_id === feasibilityRun.gapId) setFeasibilityRun(null);
  }, [feasibilityRun, feasibilityVerdict.data]);

  function startVerifyGap(gapId: string) {
    setVerifyStartedAt(Date.now());
    setVerifyingGap({ gapId, runId: null });
    verify.mutate(
      { gapId },
      {
        onSuccess: (r) => setVerifyingGap({ gapId, runId: r.verify_run_id }),
        onError: () => setVerifyingGap(null),
      },
    );
  }

  function startFeasibilityVerify(gapId: string) {
    track("gap_feasibility_click", { gapId }, pid);
    setFeasibilityRun({ gapId, runId: null });
    feasibilityVerify.mutate(
      { gapId },
      {
        onSuccess: (r) => setFeasibilityRun({ gapId, runId: r.feasibility_run_id }),
        onError: () => setFeasibilityRun(null),
      },
    );
  }

  function startDiscover() {
    if (!cid) return;
    // A3-P2：重新发现会开启新一轮 run（新 gap_id），旧 run 的核验/裁决无法回溯。
    // 已有裁决，或已有 run 但 scratchpad 尚未加载完(gaps 空、无法判断有无裁决,codex A3-P2)，都先确认。
    const decided = gaps.filter((g) => g.status !== "draft").length;
    const runLoadingUnknown = runId != null && !scratchpad.data;
    if (
      (decided > 0 || runLoadingUnknown) &&
      !window.confirm(
        decided > 0
          ? `本次已有 ${decided} 条研究空白完成核验/裁决；重新发现将开启新一轮，之前的裁决无法回溯。确认重新发现？`
          : "当前已有一轮研究空白（正在加载，可能含已核验/裁决）；重新发现将开启新一轮且无法回溯。确认重新发现？",
      )
    ) {
      return;
    }
    track("gap_run", undefined, pid);
    discover.mutate(
      { cid },
      {
        onSuccess: (r) => {
          setRunId(r.run_id);
          setSelectedGapId(null);
        },
      },
    );
  }

  function onDecide(action: GapPatchAction, statement?: string): Promise<unknown> {
    if (!selectedGapId) return Promise.resolve();
    if (action === "revise") {
      // revise 必带非空 statement（契约 GapRevise）；空则显式拒绝，不伪装成合法请求（codex B5-P2）
      const s = (statement ?? "").trim();
      if (!s) return Promise.reject(new Error("改写内容不能为空"));
      return patch.mutateAsync({ gapId: selectedGapId, action: "revise", statement: s });
    }
    return patch.mutateAsync({ gapId: selectedGapId, action });
  }

  return (
    <ProjectGate project={project}>
      <div className="research-view" data-testid="research-view">
        <header className="research-head">
          <div className="research-head-text">
            <h2 className="research-title">研究空白发现 · 价值核验</h2>
            <p className="research-sub">
              agent 发现结构化研究空白，确定性核验其价值；所有裁决<strong>浮现给你审定</strong>，不自动定稿。
            </p>
          </div>
          <button
            type="button"
            className="btn btn-primary research-discover-btn"
            disabled={!cid || discover.isPending}
            onClick={startDiscover}
            title={cid ? "启动 GAP 发现 run" : "需先构建分析语料"}
          >
            {discover.isPending ? "发现中…" : runId ? "重新发现" : "发现研究空白"}
          </button>
        </header>

        {!cid && (
          <div className="research-need-corpus" role="note">
            需先在「分析」区构建就绪的分析语料（R corpus），才能发现研究空白。
          </div>
        )}
        {discover.isError && <GapReadinessCard error={discover.error} projectId={pid} />}

        <div className="research-grid">
          <main className="research-main">
            <GapPanel
              projectId={pid}
              gaps={gaps}
              isLoading={!!runId && scratchpad.isLoading}
              error={(scratchpad.error as Error) ?? null}
              onSelectGap={(g) => setSelectedGapId(g.gap_id)}
              selectedGapId={selectedGapId}
            />
          </main>

          <aside className="research-aside">
            {/* P1 可观测：长精读/核验阶段实时进度（不黑箱）；run 终态后自动隐藏 */}
            <GapRunTimeline progress={gapProgress} />
            <ScratchpadLive
              state={runId ? scratchpad.data : null}
              isLoading={!!runId && scratchpad.isLoading}
              error={(scratchpad.error as Error) ?? null}
              onSelectGap={(g) => setSelectedGapId(g.gap_id)}
              selectedGapId={selectedGapId}
            />

            <div className="research-detail">
              {!selectedGap ? (
                <div className="card research-detail-empty" role="note">
                  从左侧选择一个研究空白，查看价值核验与 HITL 决策。
                </div>
              ) : (
                <>
                  {verdict.data ? (
                    <ValueVerdictCard
                      result={verdict.data}
                      gap={selectedGap}
                      onDecide={onDecide}
                      isDeciding={patch.isPending}
                      decideError={(patch.error as Error) ?? null}
                    />
                  ) : selectedGap.status === "draft" ? (
                    <div className="card research-verify-prompt">
                      {currentGapVerifying && !verifyFailed ? (
                        <p className="research-verify-progress muted" aria-live="polite" role="status">
                          <span className="spinner" /> 价值核验进行中 · 已耗时 {verifyElapsed}s · 每 4 秒自动刷新
                        </p>
                      ) : (
                        <>
                          <p className="research-verify-text">该研究空白尚未核验价值。</p>
                          <button type="button" className="btn btn-primary" disabled={verify.isPending}
                            onClick={() => startVerifyGap(selectedGap.gap_id)}>
                            {verify.isPending && verify.variables?.gapId === selectedGap.gap_id ? "核验中…" : "核验研究价值"}
                          </button>
                        </>
                      )}
                      {verifyFailed && <div className="research-verify-failed" role="alert">价值核验任务失败，未生成裁决。可稍后重试。</div>}
                      {verify.isError && <ErrMsg error={verify.error} />}
                    </div>
                  ) : verdict.isError && !is404(verdict.error) ? (
                    <div className="card"><ErrMsg error={verdict.error} /></div>
                  ) : (
                    <div className="card research-detail-pending" role="status">
                      <span className="spinner" /> 价值裁决生成中…
                    </div>
                  )}

                  {feasibilityVerdict.data && !isFeasibilityVerdictPending(feasibilityVerdict.data) ? (
                    <FeasibilityVerdictCard result={feasibilityVerdict.data} />
                  ) : (
                    <div className="card research-verify-prompt">
                      {currentGapFeasibility && !feasibilityFailed ? (
                        <p className="research-verify-progress muted" aria-live="polite" role="status">
                          <span className="spinner" /> 可行性核验进行中 · 每 4 秒自动刷新
                        </p>
                      ) : (
                        <>
                          <p className="research-verify-text">核验数据、方法与资源是否足以支撑该方向。</p>
                          <button type="button" className="btn btn-primary" disabled={feasibilityVerify.isPending}
                            onClick={() => startFeasibilityVerify(selectedGap.gap_id)}>
                            {feasibilityVerify.isPending && feasibilityVerify.variables?.gapId === selectedGap.gap_id
                              ? "核验中…"
                              : "可行性核验"}
                          </button>
                        </>
                      )}
                      {feasibilityFailed && (
                        <div className="research-verify-failed" role="alert">
                          可行性核验任务失败：{feasibilityJob.data?.error || "未生成裁决，可稍后重试。"}
                        </div>
                      )}
                      {feasibilityVerify.isError && <ErrMsg error={feasibilityVerify.error} />}
                      {feasibilityVerdict.error
                        && !isFeasibilityNotReady(feasibilityVerdict.error)
                        && !currentGapFeasibility
                        && <ErrMsg error={feasibilityVerdict.error} />}
                    </div>
                  )}
                </>
              )}
            </div>
          </aside>
        </div>
      </div>
    </ProjectGate>
  );
}
