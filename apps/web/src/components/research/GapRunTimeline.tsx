/**
 * GapRunTimeline.tsx — gap discover/verify 实时进度时间线（P1 可观测）。
 *
 * 让长精读/核验阶段「不黑箱」：精读 N/M 进度条 + subagent(gap-finder/value-evidence) 活动流
 * （在调什么工具、想到哪）。数据源 = useGapRunStream 的 gap SSE；与 ScratchpadLive 并存
 * （此处显示"过程"，ScratchpadLive 显示"逐条落库的 gap"）。run 终态后自动隐藏（不占位）。
 */
import type { GapRunProgress } from "../../hooks/useGapRunStream";
import type { GapSseEvent } from "../../api/client";

const PHASE_LABEL: Record<GapRunProgress["phase"], string> = {
  idle: "等待启动",
  started: "启动中…",
  summarizing: "精读文献中",
  discovering: "发现研究空白中",
  verifying: "价值核验中",
  done: "已完成",
  error: "运行失败",
};

/** subagent 活动 → 一行可读文案。 */
function activityLine(e: GapSseEvent): string {
  const skill = e.skill === "feasibility-scout"
    ? "可行性核验"
    : e.skill === "value-evidence" ? "价值核验" : "发现";
  if (e.child_type === "tools_start" && e.tool_calls?.length) {
    const names = e.tool_calls.map((t) => t.name).join(", ");
    return `${skill} agent 调用工具：${names}`;
  }
  if (e.child_type === "round_complete") {
    if (e.thinking) return `${skill} agent：${e.thinking}`;
    if (e.tool_results?.length) {
      const ok = e.tool_results.filter((r) => r.success).length;
      return `${skill} agent 完成一轮（${ok}/${e.tool_results.length} 工具成功）`;
    }
    return `${skill} agent 完成一轮`;
  }
  if (e.child_type === "run_complete") return `${skill} agent 收束`;
  if (e.child_type === "error") return `${skill} agent 报错：${e.child_error ?? ""}`;
  return `${skill} agent 活动`;
}

export interface GapRunTimelineProps {
  progress: GapRunProgress;
}

export function GapRunTimeline({ progress }: GapRunTimelineProps) {
  const { phase, summarizeDone, summarizeTotal, activity, error } = progress;
  // 终态/未启动不渲染（交回 ScratchpadLive 的空/完成态，避免重复占位）
  if (phase === "idle" || phase === "done") return null;

  const live = phase === "started" || phase === "summarizing" || phase === "discovering" || phase === "verifying";
  const pct = summarizeTotal > 0 ? Math.round((summarizeDone / summarizeTotal) * 100) : 0;

  return (
    <section className="gap-timeline" aria-label="gap 运行进度" aria-live="polite">
      <header className="gap-timeline-head">
        <span className={`gap-timeline-pulse${live ? " is-live" : ""}`} aria-hidden="true" />
        <span className="gap-timeline-phase">{PHASE_LABEL[phase]}</span>
      </header>

      {phase === "error" && (
        <div className="gap-timeline-error" role="alert">
          {error ?? "运行失败"}
        </div>
      )}

      {phase === "summarizing" && summarizeTotal > 0 && (
        <div className="gap-timeline-progress">
          <div className="gap-timeline-bar">
            <div className="gap-timeline-bar-fill" style={{ width: `${pct}%` }} />
          </div>
          <span className="gap-timeline-count">
            精读 {summarizeDone}/{summarizeTotal} 篇
          </span>
        </div>
      )}

      {activity.length > 0 && (
        <ul className="gap-timeline-feed">
          {activity.map((e, i) => (
            <li className="gap-timeline-item" key={`${e.seq ?? i}-${i}`} data-child-type={e.child_type}>
              <span className="gap-timeline-dot" aria-hidden="true" />
              <span className="gap-timeline-text">{activityLine(e)}</span>
            </li>
          ))}
        </ul>
      )}

      {live && activity.length === 0 && phase !== "summarizing" && (
        <div className="gap-timeline-waiting">agent 正在工作，稍候…</div>
      )}
    </section>
  );
}
