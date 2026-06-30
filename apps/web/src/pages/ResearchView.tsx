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
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import {
  useProject,
  useDiscoverGaps,
  useLatestGapDiscoverRun,
  useScratchpad,
  useVerifyGap,
  useGapVerdict,
  usePatchGap,
} from "../api/agentHooks";
import { ApiError } from "../api/client";
import type { GapCandidate, GapPatchAction } from "../types/research";
import { ErrMsg } from "../lib/ui";
import { GapPanel } from "../components/research/GapPanel";
import { ScratchpadLive } from "../components/research/ScratchpadLive";
import { ValueVerdictCard } from "../components/research/ValueVerdictCard";

function is404(err: unknown): boolean {
  return err instanceof ApiError && err.status === 404;
}

export interface ResearchViewProps {
  /** dev/e2e override；缺省取自路由 params */
  projectId?: number;
  /** dev/e2e override；缺省取自 activeCorpus.rCorpusId */
  corpusId?: string;
}

export function ResearchView({ projectId: pidProp, corpusId: cidProp }: ResearchViewProps = {}) {
  const params = useParams<{ pid: string }>();
  const pid = pidProp ?? Number(params.pid);
  const validPid = Number.isFinite(pid) && pid > 0;

  // 有 corpusId override(dev) 时不拉 project；否则从 activeCorpus 取就绪的 R 语料 id
  const project = useProject(cidProp || !validPid ? 0 : pid);
  const activeCorpus = project.data?.activeCorpus ?? null;
  const cid = cidProp ?? (activeCorpus?.status === "ready" ? activeCorpus.rCorpusId : null);

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

  const selectedGap = gaps.find((g) => g.gap_id === selectedGapId) ?? null;

  const verify = useVerifyGap(pid);
  // A3-P2：verify 异步约数分钟，给等待加已耗时计时（否则盲等无反馈）。
  const [verifyElapsed, setVerifyElapsed] = useState(0);
  useEffect(() => {
    if (!verify.isPending) {
      setVerifyElapsed(0);
      return;
    }
    const t0 = Date.now();
    const iv = setInterval(() => setVerifyElapsed(Math.floor((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(iv);
  }, [verify.isPending]);
  const needsVerdict = !!selectedGap && selectedGap.status !== "draft";
  // 裁决异步产出：verify 后短暂 404 时继续轮询，避免「无 verdict/无错/无加载」的空白态（codex B5-P2）
  const verdict = useGapVerdict(pid, needsVerdict ? selectedGapId : null, { poll: true });
  const patch = usePatchGap(pid);

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
      {discover.isError && <ErrMsg error={discover.error} />}

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
            ) : selectedGap.status === "draft" ? (
              <div className="card research-verify-prompt">
                <p className="research-verify-text">该研究空白尚未核验价值。</p>
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={verify.isPending}
                  onClick={() => verify.mutate({ gapId: selectedGap.gap_id })}
                >
                  {verify.isPending && verify.variables?.gapId === selectedGap.gap_id
                    ? "核验中…"
                    : "核验研究价值"}
                </button>
                {/* codex A3-P2: 计时只在"当前选中 gap 正在核验"时显示，避免核验中切到别的 draft gap 时误显示 */}
                {verify.isPending && verify.variables?.gapId === selectedGap.gap_id && (
                  <p
                    className="research-verify-progress muted"
                    role="status"
                    style={{ fontSize: "0.8rem", marginTop: "0.4rem" }}
                  >
                    已耗时 {verifyElapsed}s · 反向检索 + 计量核验通常需 1–3 分钟，请稍候
                  </p>
                )}
                {verify.isError && <ErrMsg error={verify.error} />}
              </div>
            ) : verdict.data ? (
              <ValueVerdictCard
                result={verdict.data}
                gap={selectedGap}
                onDecide={onDecide}
                isDeciding={patch.isPending}
                decideError={(patch.error as Error) ?? null}
              />
            ) : verdict.isError && !is404(verdict.error) ? (
              <div className="card">
                <ErrMsg error={verdict.error} />
              </div>
            ) : (
              // 裁决加载中或 404(尚未产生)：显式 pending，不留空白（codex B5-P2）
              <div className="card research-detail-pending" role="status">
                <span className="spinner" /> 价值裁决生成中…
              </div>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
