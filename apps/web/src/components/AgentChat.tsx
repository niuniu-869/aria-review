// AgentChat — 对话输入框 + SSE 流接收 + RunTimeline 渲染 (P1-10)
// P2-3: 增加写操作确认开关 + ConfirmCard 批准/拒绝 + RunLog 下载。
// P2-4: 接入 onSearchResults → 渲染候选卡 SearchCandidateCards。
// P0 三入口隔离：顶部入口选择器（检索建库/综述撰写/研究空白），随 createRun 传 entry，
//   后端据此收窄 tool_ids + 隔离对话历史。路由 = 用户点按钮，无 AI 分诊。
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { useAgentRunStream } from "../hooks/useAgentRunStream";
import { RunTimeline } from "./RunTimeline";
import { RunHistory } from "./RunHistory";
import { ConfirmCard } from "./ConfirmCard";
import { SearchCandidateCards } from "./SearchCandidateCards";
import { ErrorBoundary } from "./ErrorBoundary";
import { ErrMsg } from "../lib/ui";
import { useLlmSettings } from "../api/useLlmSettings";
import { useSciverseSettings } from "../api/useSciverseSettings";
import type { AgentEntry } from "../api/client";
import type { RunStatus } from "../api/runStatus";
import type { ProjectReadiness } from "../hooks/useProjectReadiness";
import { track } from "../lib/track";

interface Props {
  projectId: number;
  /**
   * M4 (codex P2): run 完成时回调真实 runId + finalOutput + eventSeq,
   * 供上层(ChatWorkbench)据此创建工件。替代不可靠的 DOM MutationObserver 监听。
   */
  onRunComplete?: (info: {
    runId: string;
    finalOutput: string;
    eventSeq: number;
    entry: AgentEntry;
    status: RunStatus;
  }) => void;
  /**
   * I-2: run 开始时（handleSubmit 启动）通知父级，父级可据此隐藏空状态引导。
   * 出错/取消后不重置，避免引导闪回。
   */
  onRunStart?: (info: { entry: AgentEntry }) => void;
  /**
   * W4 (Task 7-8): 填入预设/建议追问文本（受控注入，不自动发送）。
   * I-1 修复：使用 {text, seq} 对象；seq 每次点击都递增，确保同一文本二次点击
   * 也能触发 useEffect（引用每次变化）。
   */
  fillPrompt?: { text: string; seq: number } | null;
  /** 项目就绪度未加载时为 undefined，此时不显示提示也不拦截。 */
  readiness?: ProjectReadiness;
  /**
   * F-07: 最近已完成（done）的 runId（至多 3 条，新→旧）。
   * 在 RunTimeline 上方渲染只读「历史运行」折叠区；缺省/空数组则不渲染。
   */
  historyRunIds?: number[];
}

// P0 三入口元数据：标签 + 副文案 + 输入占位 + 建议追问（每入口独立，路由 = 用户点按钮）。
const ENTRY_META: Record<
  AgentEntry,
  { label: string; hint: string; placeholder: string; followUps: string[] }
> = {
  search: {
    label: "检索建库",
    hint: "多源检索 · 相关性筛选 · 入库 · 全文摄取 · 结构化抽取",
    placeholder: "例：检索「联邦学习 隐私保护」近五年文献，筛选相关的约 30 篇加入语料库…",
    followUps: [
      "检索补充更多相关文献，扩充语料库",
      "对已入库文献补全摘要/作者/年份等缺失元数据",
      "把项目内 PDF 解析全文并抽取研究问题/方法/发现",
    ],
  },
  review: {
    label: "综述撰写",
    hint: "基于已纳入语料合成逐句可回链原文的综述",
    placeholder: "例：基于本项目已纳入语料写一篇文献综述（如需指定论型可注明 paper_type）…",
    followUps: [
      "把综述导出为 DOCX 格式下载",
      "针对某一小节补充更细的论证与引用",
      "核查某条论断对应的原文出处",
    ],
  },
  gap: {
    label: "研究空白",
    hint: "围绕语料探讨研究空白（系统性发现/核验在【研究】面板后台跑）",
    placeholder: "例：围绕本项目语料，和我讨论还有哪些值得做的研究空白…",
    followUps: [
      "把这个研究空白记入 scratchpad",
      "为这个空白找一篇最相关的原文精读佐证",
      "到【研究】面板跑系统性 GAP 发现与价值核验",
    ],
  },
};

const ENTRY_ORDER: AgentEntry[] = ["search", "review", "gap"];

export function AgentChat({ projectId, onRunComplete, onRunStart, fillPrompt, readiness, historyRunIds }: Props) {
  const queryClient = useQueryClient();
  const { settings: llm } = useLlmSettings();
  const { settings: sciverse } = useSciverseSettings();
  const llmOptions = useMemo(() => ({
    apiKey: llm.apiKey || undefined,
    baseUrl: llm.baseUrl || undefined,
    model: llm.model || undefined,
  }), [llm.apiKey, llm.baseUrl, llm.model]);
  const sciverseOptions = useMemo(() => ({
    apiToken: sciverse.apiToken || undefined,
    baseUrl: sciverse.baseUrl || undefined,
  }), [sciverse.apiToken, sciverse.baseUrl]);
  // P0 三入口：默认「检索建库」（语料生产线起点）；用户可点按钮切换。运行中禁止切换。
  const [entry, setEntry] = useState<AgentEntry>("search");
  const entryMeta = ENTRY_META[entry];
  const readinessBlocked = entry !== "search" && (
    readiness?.stage === "no_papers"
    || readiness?.stage === "no_included"
    || (entry === "review" && (readiness?.stage === "no_fulltext" || readiness?.stage === "not_parsed"))
  );
  const showReadiness = entry !== "search" && readiness != null && readiness.stage !== "ready";
  const trackedGateRef = useRef<string | null>(null);
  const {
    prompt,
    setPrompt,
    events,
    running,
    showFollowUps,
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
  } = useAgentRunStream({
    projectId,
    llmOptions,
    sciverseOptions,
    entry,
    onRunStart: () => onRunStart?.({ entry }),
    onRunComplete: (info) => onRunComplete?.({ ...info, entry }),
  });

  // W4: 外部注入 fillPrompt（预设/能力卡/建议追问点击），写入输入框（可编辑，不自动发送）
  // I-1 修复：依赖整个对象（引用每次都变），text 相同但 seq 递增时仍会重跑
  useEffect(() => {
    if (fillPrompt && fillPrompt.text) {
      setPrompt(fillPrompt.text);
    }
  }, [fillPrompt]);

  useEffect(() => {
    if (!readinessBlocked || !readiness) {
      trackedGateRef.current = null;
      return;
    }
    const key = `${entry}:${readiness.stage}`;
    if (trackedGateRef.current === key) return;
    trackedGateRef.current = key;
    track("chat_gate_blocked", { entry, stage: readiness.stage }, projectId);
  }, [entry, projectId, readiness, readinessBlocked]);

  const handleComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (readinessBlocked && event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      return;
    }
    handleKeyDown(event);
  };

  const gateMessage = readiness?.stage === "no_papers"
    ? "GAP 与综述需要先建立项目文献库，发送按钮已暂时禁用。"
    : readiness?.stage === "no_included"
      ? "项目已有题录，但还没有纳入文献。先完成筛选纳入后再发送。"
      : readiness?.stage === "not_parsed"
        ? "请先在文献库完成 OCR 解析（或 AI 解析），再生成综述。"
        : readinessBlocked
          ? "综述依赖已纳入且可读的全文。先补充并解析全文后再发送。"
          : "当前已纳入文献暂无可读全文。仍可继续讨论研究空白，系统会通过检索补充旁证。";

  // P2-3 → Phase 2: RunLog 下载已迁入 TrustCard（可信凭证卡含下载入口），此处不再重复。

  return (
    <div className="agent-chat">
      {/* P0 三入口选择器：三平级按钮，用户点即路由，无 AI 分诊；运行中禁切换。 */}
      <div className="agent-entry-tabs" role="tablist" aria-label="工作入口">
        {ENTRY_ORDER.map((e) => {
          const meta = ENTRY_META[e];
          const active = e === entry;
          return (
            <button
              key={e}
              type="button"
              role="tab"
              aria-selected={active}
              className={`agent-entry-tab${active ? " agent-entry-tab-active" : ""}`}
              disabled={running}
              onClick={() => {
                setEntry(e);
                // F-21: 切入口时失效项目查询，readiness（如「项目还没有文献」）按最新数据重推导
                void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
              }}
              title={meta.hint}
            >
              <span className="agent-entry-tab-label">{meta.label}</span>
            </button>
          );
        })}
      </div>
      <p className="agent-entry-hint muted">{entryMeta.hint}</p>

      {showReadiness && readiness && (
        <div className="research-readiness agent-readiness" role={readinessBlocked ? "alert" : "status"}>
          <div className="research-readiness-head">
            <h3 className="research-readiness-title">{readiness.label}</h3>
            <p className="research-readiness-msg">{gateMessage}</p>
          </div>
          <div className="research-readiness-actions">
            <Link className="btn btn-primary" to={readiness.actionHref}>
              {readiness.actionText}
            </Link>
          </div>
        </div>
      )}

      <div className="agent-input-row">
        <textarea
          className="input"
          placeholder={entryMeta.placeholder}
          value={prompt}
          disabled={running}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={handleComposerKeyDown}
          rows={3}
          aria-label="Agent 指令输入"
        />
        <button
          className="btn btn-primary"
          disabled={running || !prompt.trim() || readinessBlocked}
          onClick={() => {
            if (!readinessBlocked) void submit();
          }}
          title={readinessBlocked ? "请先按上方提示完善项目语料" : undefined}
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
            onClick={() => void stop()}
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

      {/* F-07: 历史运行只读折叠区（最近 done run 的指令+产出），位于实时 RunTimeline 之上 */}
      {historyRunIds && historyRunIds.length > 0 && (
        <RunHistory projectId={projectId} runIds={historyRunIds} />
      )}

      <ErrorBoundary key={runCount}>
        <RunTimeline events={events} />
      </ErrorBoundary>

      {pendingConfirm && (
        <ConfirmCard
          toolId={pendingConfirm.toolId}
          action={pendingConfirm.action}
          argsPreview={pendingConfirm.argsPreview}
          pending={confirming}
          onApprove={() => void decide("approve")}
          onReject={() => void decide("reject")}
        />
      )}

      {/* P2-4: 检索候选卡 — 出现在时间线/确认卡之后、追问 chips 之前 */}
      {searchResult && (searchResult.candidates.length > 0 || searchResult.partial) && (
        <SearchCandidateCards
          projectId={projectId}
          candidates={searchResult.candidates}
          query={searchResult.query}
          searchCount={searchResult.searchCount}
          latestCount={searchResult.latestCount}
          partial={searchResult.partial}
          partialReason={searchResult.partialReason}
        />
      )}

      {/* W4 Task 8: run 完成后建议追问 chips（按当前入口给出对应追问） */}
      {showFollowUps && !running && (
        <div className="follow-up-chips" role="group" aria-label="建议追问">
          <span className="follow-up-label">建议继续：</span>
          {entryMeta.followUps.map((text) => (
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
