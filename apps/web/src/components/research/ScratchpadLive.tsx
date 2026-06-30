/**
 * ScratchpadLive.tsx — 研究笔记本实时视图（B3 / 类 harness 工作记忆可视化）。
 *
 * 让用户「看见 agent 在思考」。轮询 GET .../scratchpad，像研究笔记本一样动态显示
 * agent 在一次 GAP run 内累积的条目与状态流转（draft→verified→accepted）。
 * 有节奏感：运行中脉冲指示 + 条目淡入 + lens/status 实时计数；run_status 终态停轮询。
 *
 * 诚实：run_status=failed 显式呈现失败（不静默成完成）；无 run / 空条目有友好态。
 * 纯展示 ScratchpadLive（state 驱动，易测/易 e2e）+ 接线 ScratchpadLiveConnected（轮询）。
 */
import { useMemo } from "react";
import type { GapCandidate, GapStatus, ScratchpadState } from "../../types/research";
import { useScratchpad } from "../../api/agentHooks";
import { ErrMsg } from "../../lib/ui";

const STATUS_META: Record<GapStatus, { label: string; cls: string }> = {
  draft: { label: "草稿", cls: "sp-stage-draft" },
  verified: { label: "已核验", cls: "sp-stage-verified" },
  accepted: { label: "已采纳", cls: "sp-stage-accepted" },
  rejected: { label: "已驳回", cls: "sp-stage-rejected" },
};

const LENS_LABEL: Record<GapCandidate["lens"], string> = {
  concept: "概念",
  method: "方法",
  theory: "理论",
};

export interface ScratchpadLiveProps {
  state?: ScratchpadState | null;
  isLoading?: boolean;
  error?: Error | null;
  onSelectGap?: (gap: GapCandidate) => void;
  selectedGapId?: string | null;
}

/** 按 status 计数（驱动「draft→verified→accepted」流转可见性）。 */
function tally(entries: GapCandidate[]): Record<GapStatus, number> {
  const t: Record<GapStatus, number> = { draft: 0, verified: 0, accepted: 0, rejected: 0 };
  for (const e of entries) t[e.status] += 1;
  return t;
}

export function ScratchpadLive({ state, isLoading, error, onSelectGap, selectedGapId }: ScratchpadLiveProps) {
  const entries = state?.entries ?? [];
  const counts = useMemo(() => tally(entries), [entries]);
  const runStatus = state?.run_status;
  const running = runStatus === "running" || (isLoading && !state);

  // run 头部状态文案（诚实：failed 显式）
  // 状态徽标与脉冲/空态文案保持一致（codex B3-P2）：无 run_status 且非加载 → 「未启动」，
  // 不再误显「运行中」。
  const statusBadge =
    runStatus === "failed"
      ? { label: "运行失败", cls: "sp-run-failed" }
      : runStatus === "completed"
        ? { label: "已完成", cls: "sp-run-done" }
        : running
          ? { label: "运行中", cls: "sp-run-live" }
          : { label: "未启动", cls: "sp-run-idle" };

  return (
    <section className="scratchpad" aria-label="研究笔记本（实时）" aria-live="polite">
      <header className="sp-head">
        <span className={`sp-pulse${running ? " is-live" : ""}`} aria-hidden="true" />
        <div className="sp-head-text">
          <h3 className="sp-title">研究笔记本</h3>
          <p className="sp-sub">agent 在本次 run 内累积的结构化空白（实时）</p>
        </div>
        <span className={`badge sp-run ${statusBadge.cls}`}>{statusBadge.label}</span>
      </header>

      {error && <ErrMsg error={error} />}

      {runStatus === "failed" && (
        <div className="sp-failnote" role="alert">
          本次 GAP 发现 run 失败（已显式标注，未静默成完成）。可重试。
        </div>
      )}

      {/* lens/status 实时计数：看着数字从 draft 流向 verified/accepted */}
      <div className="sp-tally" role="status">
        <span className="sp-tally-item sp-tally-total">{entries.length} 条</span>
        <span className="sp-tally-sep" aria-hidden="true">
          ·
        </span>
        <span className="sp-tally-item sp-stage-draft">草稿 {counts.draft}</span>
        <span className="sp-tally-item sp-stage-verified">已核验 {counts.verified}</span>
        <span className="sp-tally-item sp-stage-accepted">已采纳 {counts.accepted}</span>
        {counts.rejected > 0 && (
          <span className="sp-tally-item sp-stage-rejected">已驳回 {counts.rejected}</span>
        )}
      </div>

      {entries.length === 0 && !error ? (
        <div className="sp-empty" role="note">
          {running ? "agent 正在翻阅文献、记录空白…" : "暂无条目。启动 GAP 发现后将在此实时累积。"}
        </div>
      ) : (
        <ul className="sp-feed">
          {entries.map((e) => {
            const stage = STATUS_META[e.status];
            const selected = selectedGapId === e.gap_id;
            return (
              <li
                key={e.gap_id}
                className={`sp-entry ${stage.cls}${selected ? " is-selected" : ""}`}
                data-gap-id={e.gap_id}
                data-status={e.status}
              >
                <button
                  type="button"
                  className="sp-entry-btn"
                  onClick={() => onSelectGap?.(e)}
                  title="查看该空白详情"
                >
                  <span className="sp-entry-rail" aria-hidden="true" />
                  <span className="sp-entry-main">
                    <span className="sp-entry-meta">
                      <span className="sp-lens">{LENS_LABEL[e.lens]}</span>
                      <span className={`sp-stage-tag ${stage.cls}`}>{stage.label}</span>
                      {e.value_verdict && (
                        <span className="sp-verdict-dot" data-verdict={e.value_verdict.verdict} aria-hidden="true" />
                      )}
                    </span>
                    <span className="sp-entry-statement">{e.statement}</span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {state?.updated_at && (
        <div className="sp-foot">更新于 {state.updated_at}</div>
      )}
    </section>
  );
}

export interface ScratchpadLiveConnectedProps {
  projectId: number;
  /** GAP 发现 run id（null 时不轮询） */
  runId: string | null;
  onSelectGap?: (gap: GapCandidate) => void;
  selectedGapId?: string | null;
  /** 轮询间隔（ms，默认 1500） */
  pollMs?: number;
}

/** 接线版：轮询 scratchpad（run_status 终态自动停轮询，见 useScratchpad）。 */
export function ScratchpadLiveConnected({
  projectId,
  runId,
  onSelectGap,
  selectedGapId,
  pollMs,
}: ScratchpadLiveConnectedProps) {
  const { data, isLoading, error } = useScratchpad(projectId, runId, { pollMs });
  return (
    <ScratchpadLive
      state={data}
      isLoading={isLoading}
      error={(error as Error) ?? null}
      onSelectGap={onSelectGap}
      selectedGapId={selectedGapId}
    />
  );
}
