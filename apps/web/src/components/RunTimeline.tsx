// RunTimeline — 渲染 Agent Run SSE 事件列表为可视化时间线 (P1-10)
import type {
  AgentSseEvent,
  AgentLlmStartEvent,
  AgentToolsStartEvent,
  AgentRoundCompleteEvent,
  AgentRunCompleteEvent,
  AgentErrorEvent,
} from "../api/client";
import { renderMarkdown } from "../lib/markdown";

interface Props {
  events: AgentSseEvent[];
}

// ---- 各卡片子组件 ----

function LlmStartCard({ e }: { e: AgentLlmStartEvent }) {
  return (
    <div className="timeline-card tl-llm-start">
      <div className="tl-label">
        第 {e.round} 轮 · 思考中{e.is_final ? " (最终)" : ""}
      </div>
      <div style={{ fontSize: "0.82rem", color: "var(--ink-3)" }}>
        上下文 tokens: {e.context_tokens.toLocaleString()}
      </div>
    </div>
  );
}

function ToolsStartCard({ e }: { e: AgentToolsStartEvent }) {
  return (
    <div className="timeline-card tl-tools-start">
      <div className="tl-label">第 {e.round} 轮 · 调用工具</div>
      {e.thinking && (
        <div
          className="tl-thinking"
          dangerouslySetInnerHTML={{ __html: renderMarkdown(e.thinking, { streaming: true }) }}
        />
      )}
      {(e.tool_calls?.length ?? 0) > 0 && (
        <ul className="tl-tool-list">
          {e.tool_calls.map((tc) => (
            <li key={tc.id} className="tl-tool-item">
              <span className="tl-tool-name">{tc.name}</span>
              <span className="tl-tool-args">{tc.args_preview}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function RoundCompleteCard({ e }: { e: AgentRoundCompleteEvent }) {
  return (
    <div className="timeline-card tl-round-complete">
      <div className="tl-label">
        第 {e.round} 轮 · 完成{e.is_final ? " (末轮)" : ""}
      </div>
      {(e.tool_results?.length ?? 0) > 0 && (
        <ul className="tl-result-list">
          {e.tool_results.map((r, idx) => (
            <li
              key={`${e.seq}-${idx}`}
              className={`tl-result-item ${r.success ? "ok" : "fail"}`}
            >
              <span className="tl-result-action">{r.action}</span>
              {r.success ? (
                <span className="tl-result-summary">{r.summary}</span>
              ) : (
                <span className="tl-result-error">{r.error ?? r.summary}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function RunCompleteCard({ e }: { e: AgentRunCompleteEvent }) {
  return (
    <div className="timeline-card tl-run-complete tl-final-output">
      <div className="tl-label">运行完成 · {e.status}</div>
      {e.final_output && (
        <div
          className="md"
          dangerouslySetInnerHTML={{ __html: renderMarkdown(e.final_output) }}
        />
      )}
    </div>
  );
}

function ErrorCard({ e }: { e: AgentErrorEvent }) {
  return (
    <div className="timeline-card tl-error">
      <div className="tl-error-msg">错误: {e.error}</div>
    </div>
  );
}

// Phase 5: 运行被用户取消的终态卡（灰色，区别于红色 error / 成功 run_complete）。
function CancelledCard() {
  return (
    <div className="timeline-card tl-cancelled">
      <div className="tl-label" style={{ color: "var(--ink-3)" }}>
        运行已取消
      </div>
    </div>
  );
}

// ---- 主组件 ----

export function RunTimeline({ events }: Props) {
  if (events.length === 0) return null;

  return (
    <div className="timeline" role="log" aria-label="Agent 运行时间线" aria-live="polite">
      {events.map((ev) => {
        switch (ev.type) {
          case "run_start":
            return (
              <div key={`rs-${ev.seq}`} className="timeline-card">
                <div className="tl-label">
                  运行开始 · {ev.model} · 最多 {ev.max_rounds} 轮
                </div>
              </div>
            );
          case "llm_start":
            return <LlmStartCard key={`ls-${ev.seq}`} e={ev} />;
          case "tools_start":
            return <ToolsStartCard key={`ts-${ev.seq}`} e={ev} />;
          case "round_complete":
            return <RoundCompleteCard key={`rc-${ev.seq}`} e={ev} />;
          case "run_complete":
            return <RunCompleteCard key={`rnc-${ev.seq}`} e={ev} />;
          case "error":
            return <ErrorCard key={`err-${ev.seq}`} e={ev} />;
          case "cancelled":
            return <CancelledCard key={`cancel-${ev.seq}`} />;
          default:
            return null;
        }
      })}
    </div>
  );
}
