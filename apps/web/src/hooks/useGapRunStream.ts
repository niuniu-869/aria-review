/**
 * useGapRunStream — 消费 gap discover/verify 进度 SSE（P1 可观测）。
 *
 * 长精读/核验阶段不再是黑箱：实时冒「精读 N/M」+ subagent 活动 +
 * 终态。与 useScratchpad 轮询并存——SSE 显示进度/思考，轮询显示 gap 逐条落库。
 * runId 变化即重连；lastEventId 断点续传（刷新/重连不丢已发进度）。
 */
import { useEffect, useRef, useState } from "react";
import { streamGapRun } from "../api/client";
import type { GapSseEvent } from "../api/client";

export type GapPhase = "idle" | "started" | "summarizing" | "discovering" | "verifying" | "done" | "error";

export interface GapRunProgress {
  phase: GapPhase;
  /** summarizing 阶段：已精读 / 总篇数 */
  summarizeDone: number;
  summarizeTotal: number;
  /** subagent 活动流（保留最近若干条，供「看见 agent 在想什么」） */
  activity: GapSseEvent[];
  error: string | null;
}

const _EMPTY: GapRunProgress = {
  phase: "idle",
  summarizeDone: 0,
  summarizeTotal: 0,
  activity: [],
  error: null,
};

const _MAX_ACTIVITY = 12;

export function useGapRunStream(
  projectId: number,
  runId: string | null,
  opts?: { enabled?: boolean },
): GapRunProgress {
  const enabled = opts?.enabled ?? true;
  const [progress, setProgress] = useState<GapRunProgress>(_EMPTY);
  const abortRef = useRef<AbortController | null>(null);
  const lastSeqRef = useRef(0);

  useEffect(() => {
    if (!runId || !enabled || !(projectId > 0)) return;
    // 新 run：重置进度 + seq
    setProgress(_EMPTY);
    lastSeqRef.current = 0;
    const ac = new AbortController();
    abortRef.current = ac;

    const apply = (e: GapSseEvent) => {
      if (typeof e.seq === "number" && e.seq > lastSeqRef.current) lastSeqRef.current = e.seq;
      setProgress((prev) => {
        const next = { ...prev };
        switch (e.type) {
          case "started":
            next.phase = "started";
            break;
          case "summarizing":
            next.phase = "summarizing";
            next.summarizeDone = e.done ?? prev.summarizeDone;
            next.summarizeTotal = e.total ?? prev.summarizeTotal;
            break;
          case "discovering":
            next.phase = "discovering";
            break;
          case "verifying":
          case "scouting":
            next.phase = "verifying";
            break;
          case "subagent_event":
            next.activity = [...prev.activity, e].slice(-_MAX_ACTIVITY);
            break;
          case "done":
          case "done_empty":
            next.phase = "done";
            break;
          case "error":
            next.phase = "error";
            next.error = e.error ?? "运行失败";
            break;
        }
        return next;
      });
    };

    // onEvent 已覆盖全部类型（含 done/error，见 apply 的 switch）；不再另接 onDone/onError，
    // 否则终态被 apply 两次（codex P2）。lastEventId 取已收最大 seq（重连/重挂载续接）。
    void streamGapRun(
      projectId,
      runId,
      { signal: ac.signal, lastEventId: lastSeqRef.current },
      { onEvent: apply },
    ).catch((err) => {
      if (err instanceof Error && err.name === "AbortError") return;
      // 流异常/中断：明确进入 error 阶段（否则 UI 仍显示 live 却不再更新，codex P2）。
      setProgress((prev) => ({
        ...prev,
        phase: prev.phase === "done" ? prev.phase : "error",
        error: prev.error ?? (err as Error).message,
      }));
    });

    return () => ac.abort();
  }, [projectId, runId, enabled]);

  return progress;
}
