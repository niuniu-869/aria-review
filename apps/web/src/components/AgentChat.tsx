// AgentChat — 对话输入框 + SSE 流接收 + RunTimeline 渲染 (P1-10)
// P2-3: 增加写操作确认开关 + ConfirmCard 批准/拒绝 + RunLog 下载。
// P2-4: 接入 onSearchResults → 渲染候选卡 SearchCandidateCards。
import { useState, useRef, useCallback, useEffect } from "react";
import { createRun, streamAgentRun, confirmRun, cancelRun } from "../api/client";
import type { AgentSseEvent, AgentToolConfirmRequiredEvent, SearchCandidate } from "../api/client";
import { RunTimeline } from "./RunTimeline";
import { ConfirmCard } from "./ConfirmCard";
import { SearchCandidateCards } from "./SearchCandidateCards";
import { ErrorBoundary } from "./ErrorBoundary";
import { ErrMsg } from "../lib/ui";
import { useLlmSettings } from "../api/useLlmSettings";
import { useSciverseSettings } from "../api/useSciverseSettings";

interface Props {
  projectId: number;
  /**
   * M4 (codex P2): run 完成时回调真实 runId + finalOutput + eventSeq,
   * 供上层(ChatWorkbench)据此创建工件。替代不可靠的 DOM MutationObserver 监听。
   */
  onRunComplete?: (info: { runId: string; finalOutput: string; eventSeq: number }) => void;
  /**
   * I-2: run 开始时（handleSubmit 启动）通知父级，父级可据此隐藏空状态引导。
   * 出错/取消后不重置，避免引导闪回。
   */
  onRunStart?: () => void;
  /**
   * W4 (Task 7-8): 填入预设/建议追问文本（受控注入，不自动发送）。
   * I-1 修复：使用 {text, seq} 对象；seq 每次点击都递增，确保同一文本二次点击
   * 也能触发 useEffect（引用每次变化）。
   */
  fillPrompt?: { text: string; seq: number } | null;
}

// 建议追问 chips（run 完成后显示）
const SUGGEST_FOLLOW_UPS = [
  "为综述补充文献计量佐证（发文趋势、高被引、关键词聚类）",
  "把综述导出为 DOCX 格式下载",
  "检索补充更多相关文献，扩充语料库",
];

interface AccumulatedSearchResult {
  candidates: SearchCandidate[];
  query: string;
  searchCount: number;
  latestCount: number;
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
): AccumulatedSearchResult {
  const byKey = new Map<string, SearchCandidate>();
  for (const c of previous?.candidates ?? []) byKey.set(candidateDedupeKey(c), c);
  for (const c of candidates) byKey.set(candidateDedupeKey(c), c);
  return {
    candidates: Array.from(byKey.values()),
    query,
    searchCount: (previous?.searchCount ?? 0) + 1,
    latestCount: candidates.length,
  };
}

export function AgentChat({ projectId, onRunComplete, onRunStart, fillPrompt }: Props) {
  const { settings: llm } = useLlmSettings();
  const { settings: sciverse } = useSciverseSettings();
  const llmOptions = {
    apiKey: llm.apiKey || undefined,
    baseUrl: llm.baseUrl || undefined,
    model: llm.model || undefined,
  };
  const sciverseOptions = {
    apiToken: sciverse.apiToken || undefined,
    baseUrl: sciverse.baseUrl || undefined,
  };
  const [prompt, setPrompt] = useState("");
  const [events, setEvents] = useState<AgentSseEvent[]>([]);
  const [running, setRunning] = useState(false);
  // W4: 是否刚完成一次 run，用于显示建议追问 chips
  const [showFollowUps, setShowFollowUps] = useState(false);
  const [submitError, setSubmitError] = useState<Error | null>(null);
  // P2-3: 自动确认写操作开关。默认 true 保持现有 UX; 取消勾选才触发确认流。
  const [autoConfirm, setAutoConfirm] = useState(true);
  // P2-3: 当前 run 的 rid (createRun 返回), 供确认端点与 RunLog 下载使用。
  const [rid, setRid] = useState<string | null>(null);
  // P2-3: 待确认的写操作; 非空时在时间线下方渲染 ConfirmCard。
  const [pendingConfirm, setPendingConfirm] = useState<AgentToolConfirmRequiredEvent | null>(null);
  // P2-3: 确认请求在途时禁用按钮。
  const [confirming, setConfirming] = useState(false);
  // 修复4: 每次新 run 递增，作为 ErrorBoundary 的 key 触发 reset
  const [runCount, setRunCount] = useState(0);
  // P2-4: 本次 run 的累计检索结果（run 重置时清空），多轮 search__topic 去重后统一供候选卡选择。
  const [searchResult, setSearchResult] = useState<AccumulatedSearchResult | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // 组件卸载时终止流
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  // W4: 外部注入 fillPrompt（预设/能力卡/建议追问点击），写入输入框（可编辑，不自动发送）
  // I-1 修复：依赖整个对象（引用每次都变），text 相同但 seq 递增时仍会重跑
  useEffect(() => {
    if (fillPrompt && fillPrompt.text) {
      setPrompt(fillPrompt.text);
    }
  }, [fillPrompt]);

  const handleSubmit = useCallback(async () => {
    const text = prompt.trim();
    if (!text || running) return;

    // 终止上一次未完成的流
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;

    setRunning(true);
    setSubmitError(null);
    setEvents([]);
    setRid(null);
    setPendingConfirm(null);
    setShowFollowUps(false);
    // P2-4: 新 run 开始时清空上次检索候选
    setSearchResult(null);
    // 修复4: 每次新 run 递增 runCount，让 ErrorBoundary key 变化从而 reset
    setRunCount((c) => c + 1);
    // I-2: 通知父级 run 已开始，父级可隐藏空状态引导（出错/取消后不重置，避免闪回）
    onRunStart?.();

    try {
      const ref = await createRun(
        projectId,
        { prompt: text, autoConfirm },
        llmOptions,
        sciverseOptions,
      );
      setRid(ref.runId);

      // P2-3: 确认流下流保持打开, 既有 streamAgentRun 持续消费; confirm 后续事件自动到达这些 handlers, 无需重连流。
      await streamAgentRun(
        projectId,
        ref.runId,
        { signal: ac.signal },
        {
          onRunStart: (d) => setEvents((prev) => [...prev, d]),
          onLlmStart: (d) => setEvents((prev) => [...prev, d]),
          onToolsStart: (d) => setEvents((prev) => [...prev, d]),
          onRoundComplete: (d) => setEvents((prev) => [...prev, d]),
          onRunComplete: (d) => {
            setEvents((prev) => [...prev, d]);
            // M4: 用真实 runId 通知上层创建工件(去 DOM 监听 + runId=-1 的坑)。
            if (d.final_output) {
              onRunComplete?.({ runId: ref.runId, finalOutput: d.final_output, eventSeq: d.seq });
              // W4 Task 8: run 完成后展示建议追问 chips
              setShowFollowUps(true);
            }
          },
          onError: (d) => setEvents((prev) => [...prev, d]),
          // Phase 5: 后端若在断流前发来 cancelled 终态事件，入时间线渲染「运行已取消」灰卡。
          // 去重(codex P1): handleStop 已本地追加 cancelled 时，此处不再重复，避免两张取消卡。
          onCancelled: (d) => {
            setEvents((prev) => (prev.some((e) => e.type === "cancelled") ? prev : [...prev, d]));
            setRunning(false);
          },
          // P2-3: 收到确认信号 → 记录待确认项(同时入时间线供展示)。流不关。
          onToolConfirmRequired: (d) => {
            setEvents((prev) => [...prev, d]);
            setPendingConfirm(d);
          },
          // P2-4: 收到检索候选 → 累计本次 run 内多轮结果，避免只显示最后一轮 20 篇。
          onSearchResults: (d) => {
            setSearchResult((prev) => mergeSearchResults(prev, d.candidates, d.query));
          },
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
  }, [prompt, running, projectId, autoConfirm, llmOptions.apiKey, llmOptions.baseUrl, llmOptions.model, onRunComplete, onRunStart]);

  // P2-3: 批准/拒绝写操作。不重连流——既有 streamAgentRun 仍在消费, confirm 后端在同一条流继续发后续事件。
  const handleDecision = useCallback(
    async (decision: "approve" | "reject") => {
      if (!rid || !pendingConfirm || confirming) return;
      const confirmedId = pendingConfirm.toolCallId;
      setConfirming(true);
      setSubmitError(null);
      try {
        await confirmRun(projectId, rid, { toolCallId: confirmedId, decision });
        // codex P1：只清「刚放行的那条」。同一条 SSE 流上，confirm POST resolve 前
        // 可能已到达下一个 tool_confirm_required（顺序写工具）并置了新的 pendingConfirm；
        // 无条件清空会吞掉它致 run 卡住。仅当当前 pending 仍是刚确认的那条才清。
        setPendingConfirm((cur) => (cur?.toolCallId === confirmedId ? null : cur));
      } catch (e) {
        setSubmitError(e instanceof Error ? e : new Error(String(e)));
      } finally {
        setConfirming(false);
      }
    },
    [projectId, rid, pendingConfirm, confirming],
  );

  // Phase 5: 停止运行。即时生效优先(codex P2)——立即断本地流 + 复位 + 反馈，不等后端往返；
  // 后端取消请求改后台 fire-and-forget(避免孤儿 run 继续烧 token)，失败吞掉不影响已即时生效的中止。
  const handleStop = useCallback(() => {
    abortRef.current?.abort();
    setRunning(false);
    // abort 让 SSE 流立即结束（onCancelled 多半来不及到达），本地追加一条 cancelled 终态事件，
    // 确保用户即时看到「运行已取消」反馈。seq=-1 与后端真实 seq 不冲突；onCancelled 已去重。
    setEvents((prev) =>
      prev.some((e) => e.type === "cancelled")
        ? prev
        : [...prev, { type: "cancelled", status: "cancelled", seq: -1 }],
    );
    // 后台通知后端取消（不 await，停止对用户即时生效）。
    if (rid) void cancelRun(projectId, rid).catch(() => {});
  }, [projectId, rid]);

  // P2-3 → Phase 2: RunLog 下载已迁入 TrustCard（可信凭证卡含下载入口），此处不再重复。

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        void handleSubmit();
      }
    },
    [handleSubmit],
  );

  return (
    <div className="agent-chat">
      <div className="agent-input-row">
        <textarea
          className="input"
          placeholder="输入研究指令，按 Ctrl+Enter 或点击发送…"
          value={prompt}
          disabled={running}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={handleKeyDown}
          rows={3}
          aria-label="Agent 指令输入"
        />
        <button
          className="btn btn-primary"
          disabled={running || !prompt.trim()}
          onClick={() => void handleSubmit()}
        >
          {running ? (
            <>
              <span className="spinner" />
              运行中
            </>
          ) : (
            "发送"
          )}
        </button>
        {/* Phase 5: 运行中显示「停止运行」——取消后端 run + 断本地流，避免孤儿 run 烧 token */}
        {running && rid && (
          <button
            className="btn btn-ghost"
            onClick={() => void handleStop()}
            aria-label="停止运行"
          >
            停止运行
          </button>
        )}
      </div>

      <div className="agent-options-row">
        <label className="agent-autoconfirm">
          <input
            type="checkbox"
            checked={autoConfirm}
            disabled={running}
            onChange={(e) => setAutoConfirm(e.target.checked)}
          />
          自动确认写操作
        </label>
        {/* Phase 2: 「下载 RunLog」已迁入可信凭证卡 TrustCard（含哈希链/grounding 指标）。 */}
      </div>

      {submitError && <ErrMsg error={submitError} />}

      <ErrorBoundary key={runCount}>
        <RunTimeline events={events} />
      </ErrorBoundary>

      {pendingConfirm && (
        <ConfirmCard
          toolId={pendingConfirm.toolId}
          action={pendingConfirm.action}
          argsPreview={pendingConfirm.argsPreview}
          pending={confirming}
          onApprove={() => void handleDecision("approve")}
          onReject={() => void handleDecision("reject")}
        />
      )}

      {/* P2-4: 检索候选卡 — 出现在时间线/确认卡之后、追问 chips 之前 */}
      {searchResult && searchResult.candidates.length > 0 && (
        <SearchCandidateCards
          projectId={projectId}
          candidates={searchResult.candidates}
          query={searchResult.query}
          searchCount={searchResult.searchCount}
          latestCount={searchResult.latestCount}
        />
      )}

      {/* W4 Task 8: run 完成后建议追问 chips */}
      {showFollowUps && !running && (
        <div className="follow-up-chips" role="group" aria-label="建议追问">
          <span className="follow-up-label">建议继续：</span>
          {SUGGEST_FOLLOW_UPS.map((text) => (
            <button
              key={text}
              className="follow-up-chip"
              onClick={() => setPrompt(text)}
              aria-label={text}
            >
              {text}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
