import { useCallback, useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { cancelRun, confirmRun, createRun, streamAgentRun } from "../api/client";
import type {
  AgentEntry,
  AgentSseEvent,
  AgentToolConfirmRequiredEvent,
  LlmRequestOptions,
  SciverseRequestOptions,
  SearchCandidate,
} from "../api/client";
import type { RunStatus } from "../api/runStatus";

export interface AccumulatedSearchResult {
  candidates: SearchCandidate[];
  query: string;
  searchCount: number;
  latestCount: number;
  partial: boolean;
  partialReason?: string | null;
}

export interface AgentRunCompleteInfo {
  runId: string;
  finalOutput: string;
  eventSeq: number;
  status: RunStatus;
}

interface UseAgentRunStreamOptions {
  projectId: number;
  llmOptions: LlmRequestOptions;
  sciverseOptions: SciverseRequestOptions;
  /** P0 三入口隔离：本次对话所属入口，随 createRun 传给后端做 tool_ids 收窄 + 历史隔离。 */
  entry?: AgentEntry;
  onRunComplete?: (info: AgentRunCompleteInfo) => void;
  onRunStart?: () => void;
}

function candidateDedupeKey(c: SearchCandidate): string {
  const stableId = c.openalexId ?? c.sciverseDocId ?? c.sciverseUniqueId ?? c.doi ?? c.candidate_id;
  if (stableId) return stableId.toLowerCase();
  return `${c.title}:${c.year ?? ""}`.toLowerCase();
}

function mergeSearchResults(
  previous: AccumulatedSearchResult | null,
  candidates: SearchCandidate[],
  query: string,
  partial?: boolean,
  partialReason?: string | null,
): AccumulatedSearchResult {
  const byKey = new Map<string, SearchCandidate>();
  for (const c of previous?.candidates ?? []) byKey.set(candidateDedupeKey(c), c);
  for (const c of candidates) byKey.set(candidateDedupeKey(c), c);
  return {
    candidates: Array.from(byKey.values()),
    query,
    searchCount: (previous?.searchCount ?? 0) + 1,
    latestCount: candidates.length,
    partial: Boolean(previous?.partial || partial),
    partialReason: partialReason ?? previous?.partialReason,
  };
}

export function useAgentRunStream({
  projectId,
  llmOptions,
  sciverseOptions,
  entry,
  onRunComplete,
  onRunStart,
}: UseAgentRunStreamOptions) {
  const [prompt, setPrompt] = useState("");
  const [events, setEvents] = useState<AgentSseEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [showFollowUps, setShowFollowUps] = useState(false);
  const [submitError, setSubmitError] = useState<Error | null>(null);
  const [autoConfirm, setAutoConfirm] = useState(true);
  const [rid, setRid] = useState<string | null>(null);
  const [pendingConfirm, setPendingConfirm] = useState<AgentToolConfirmRequiredEvent | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [runCount, setRunCount] = useState(0);
  const [searchResult, setSearchResult] = useState<AccumulatedSearchResult | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const submit = useCallback(async () => {
    const text = prompt.trim();
    if (!text || running) return;

    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;

    setRunning(true);
    setSubmitError(null);
    setEvents([]);
    setRid(null);
    setPendingConfirm(null);
    setShowFollowUps(false);
    setSearchResult(null);
    setRunCount((c) => c + 1);
    onRunStart?.();

    try {
      const ref = await createRun(
        projectId,
        { prompt: text, autoConfirm, entry },
        llmOptions,
        sciverseOptions,
      );
      const runId = String(ref.runId);
      setRid(runId);

      await streamAgentRun(
        projectId,
        runId,
        { signal: ac.signal },
        {
          onRunStart: (d) => setEvents((prev) => [...prev, d]),
          onLlmStart: (d) => setEvents((prev) => [...prev, d]),
          onToolsStart: (d) => setEvents((prev) => [...prev, d]),
          onRoundComplete: (d) => setEvents((prev) => [...prev, d]),
          onRunComplete: (d) => {
            setEvents((prev) => [...prev, d]);
            if (d.final_output) {
              onRunComplete?.({ runId, finalOutput: d.final_output, eventSeq: d.seq, status: d.status });
              setShowFollowUps(true);
            }
          },
          onError: (d) => setEvents((prev) => [...prev, d]),
          onPaused: (d) => setEvents((prev) => [...prev, d]),
          onResumed: (d) => setEvents((prev) => [...prev, d]),
          onCancelled: (d) => {
            setEvents((prev) => (prev.some((e) => e.type === "cancelled") ? prev : [...prev, d]));
            setRunning(false);
          },
          onToolConfirmRequired: (d) => {
            setEvents((prev) => [...prev, d]);
            setPendingConfirm(d);
          },
          onSearchResults: (d) => {
            setSearchResult((prev) => mergeSearchResults(prev, d.candidates, d.query, d.partial, d.partialReason));
          },
          // P1: 综述进度/成稿入 events（RunTimeline 渲染），综述入口不再看不到成稿。
          onReviewProgress: (d) => setEvents((prev) => [...prev, d]),
          onReviewComplete: (d) => setEvents((prev) => [...prev, d]),
        },
      );
    } catch (e) {
      if (e instanceof Error && e.name === "AbortError") return;
      setSubmitError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      if (!ac.signal.aborted) {
        setRunning(false);
      }
    }
  }, [autoConfirm, entry, llmOptions, onRunComplete, onRunStart, projectId, prompt, running, sciverseOptions]);

  const decide = useCallback(
    async (decision: "approve" | "reject") => {
      if (!rid || !pendingConfirm || confirming) return;
      const confirmedId = pendingConfirm.toolCallId;
      setConfirming(true);
      setSubmitError(null);
      try {
        await confirmRun(projectId, rid, { toolCallId: confirmedId, decision });
        // 只清刚放行的确认项，避免同一条流上的下一条确认被误清。
        setPendingConfirm((cur) => (cur?.toolCallId === confirmedId ? null : cur));
      } catch (e) {
        setSubmitError(e instanceof Error ? e : new Error(String(e)));
      } finally {
        setConfirming(false);
      }
    },
    [confirming, pendingConfirm, projectId, rid],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
    setRunning(false);
    setEvents((prev) =>
      prev.some((e) => e.type === "cancelled")
        ? prev
        : [...prev, { type: "cancelled", status: "cancelled", seq: -1 }],
    );
    if (rid) void cancelRun(projectId, rid).catch(() => {});
  }, [projectId, rid]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        void submit();
      }
    },
    [submit],
  );

  return {
    prompt,
    setPrompt,
    events,
    running,
    showFollowUps,
    setShowFollowUps,
    submitError,
    autoConfirm,
    setAutoConfirm,
    rid,
    pendingConfirm,
    confirming,
    runCount,
    searchResult,
    submit,
    decide,
    stop,
    handleKeyDown,
  };
}
