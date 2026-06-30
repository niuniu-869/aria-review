import { useEffect, useState } from "react";
import { createAiJob, getAiJob, listAiJobs, type AiJob, type ChatMessage, type LlmRequestOptions } from "../api/client";
import { downloadMarkdown } from "../lib/download";
import {
  AiPanel,
  AiResultBox,
  AiMarkdown,
  AiEmpty,
  AiKeyNotice,
  AiTextInput,
  AiActions,
} from "./ai";

export function ChatPanel({
  projectId,
  corpusId,
  // M5: 可选 LLM 配置 prop，由父组件从 useLlmSettings 读取后注入（不改内部逻辑）
  llm,
  apiKey,
}: {
  projectId: string;
  corpusId: string;
  llm?: LlmRequestOptions;
  apiKey?: string;
}) {
  const [history, setHistory] = useState<ChatMessage[]>([]);
  const [query, setQuery] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [current, setCurrent] = useState("");
  const [jobId, setJobId] = useState<number | null>(null);
  const storageKey = `bibliocn.ai.chat.${projectId}.${corpusId}`;

  function hydrate(job: AiJob) {
    const req = job.request || {};
    const prior = Array.isArray(req.history) ? (req.history as ChatMessage[]) : [];
    const q = typeof req.query === "string" ? req.query : "";
    const base = q ? [...prior, { role: "user", content: q } as ChatMessage] : prior;
    const active = job.status === "queued" || job.status === "running";
    setJobId(job.id);
    setStreaming(active);
    setHistory(active || !job.resultText ? base : [...base, { role: "assistant", content: job.resultText }]);
    setCurrent(active ? job.resultText || "" : "");
    localStorage.setItem(storageKey, String(job.id));
  }

  useEffect(() => {
    let cancelled = false;
    async function restore() {
      try {
        const saved = Number(localStorage.getItem(storageKey) || 0);
        if (saved) {
          const job = await getAiJob(projectId, saved);
          if (!cancelled) hydrate(job);
          return;
        }
        const res = await listAiJobs(projectId, { kind: "chat", corpusId, limit: 1 });
        if (!cancelled && res.jobs[0]) hydrate(res.jobs[0]);
      } catch {
        localStorage.removeItem(storageKey);
      }
    }
    restore();
    return () => { cancelled = true; };
  }, [projectId, corpusId]);

  useEffect(() => {
    if (!jobId || !streaming) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const job = await getAiJob(projectId, jobId);
        if (!cancelled) hydrate(job);
      } catch (e) {
        if (!cancelled) {
          setStreaming(false);
          setHistory((h) => [...h, { role: "assistant", content: `(出错: ${(e as Error).message})` }]);
        }
      }
    };
    tick();
    const timer = window.setInterval(tick, 1200);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [projectId, jobId, streaming]);

  async function send() {
    const q = query.trim();
    if (!q || streaming) return;
    const priorHistory = history;
    setHistory([...priorHistory, { role: "user", content: q }]);
    setQuery("");
    setStreaming(true);
    setCurrent("");
    try {
      // M5: 将父组件传入的 apiKey 注入 opts（不上传服务器，仅作 X-LLM-Key 头）
      const effectiveLlm = llm ?? (apiKey ? { apiKey } : {});
      const job = await createAiJob(projectId, { kind: "chat", corpusId, query: q, history: priorHistory }, effectiveLlm);
      hydrate(job);
    } catch (e) {
      setHistory((h) => [...h, { role: "assistant", content: `(出错: ${(e as Error).message})` }]);
      setStreaming(false);
    }
  }

  function exportMarkdown() {
    const turns = history.map((m) => {
      const role = m.role === "user" ? "用户" : "助手";
      return `## ${role}\n\n${m.content}`;
    });
    if (current) turns.push(`## 助手（生成中）\n\n${current}`);
    downloadMarkdown(`语料对话-${projectId}`, `# 语料对话导出\n\n${turns.join("\n\n")}\n`);
  }

  return (
    <AiPanel title="与语料对话" intro="就语料内容提问，回答会标注是否在语料中发现。">
      <AiResultBox scroll>
        {history.length === 0 && !current && (
          <AiEmpty>开始提问，例如「这批文献的核心研究主题是什么？」</AiEmpty>
        )}
        {history.map((m, i) => (
          <div key={i} className="ai-chat-turn">
            <strong className="ai-chat-role">{m.role === "user" ? "你" : "助手"}</strong>
            {m.role === "user" ? (
              <span className="ai-chat-user-text">{m.content}</span>
            ) : (
              <AiMarkdown content={m.content} projectId={projectId} />
            )}
          </div>
        ))}
        {current && (
          <div className="ai-chat-turn" aria-live="polite">
            <strong className="ai-chat-role">助手</strong>
            <AiMarkdown content={current} streaming live projectId={projectId} />
          </div>
        )}
      </AiResultBox>

      <AiActions>
        <AiTextInput
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") send(); }}
          placeholder="输入问题，回车发送"
          disabled={streaming}
        />
        <button type="button" className="btn btn-primary" onClick={send} disabled={!query.trim() || streaming}>
          {streaming ? "回答中…" : "发送"}
        </button>
        <button
          type="button"
          className="btn"
          onClick={exportMarkdown}
          disabled={history.length === 0 && !current}
        >
          导出 Markdown
        </button>
      </AiActions>

      <AiKeyNotice hasKey={!!(llm?.apiKey || apiKey)} />
    </AiPanel>
  );
}
