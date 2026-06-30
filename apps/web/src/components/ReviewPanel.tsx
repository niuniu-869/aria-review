import { useEffect, useState } from "react";
import { createAiJob, getAiJob, listAiJobs, type AiJob, type CiteSummary, type LlmRequestOptions } from "../api/client";
import type { ProvenanceMap } from "../types/provenance";
import { ReviewWithProvenance } from "./review/ReviewWithProvenance";
import { downloadMarkdown } from "../lib/download";
import {
  AiPanel,
  AiToolbar,
  AiField,
  AiTextInput,
  AiMarkdown,
  AiEmpty,
  AiError,
  AiKeyNotice,
} from "./ai";

const TYPES: [string, string][] = [
  ["undergrad", "本科综述"],
  ["master", "硕士综述"],
  ["phd", "博士综述"],
  ["grant", "基金本子"],
  ["proposal", "开题报告"],
  ["sci_intro", "SCI Intro"],
];

export function ReviewPanel({
  projectId,
  corpusId,
  // M5: 可选 LLM 配置 prop，由父组件从 useLlmSettings 读取后注入（不改内部逻辑）
  llm,
  apiKey,
}: {
  projectId: string;
  corpusId?: string;
  llm?: LlmRequestOptions;
  apiKey?: string;
}) {
  const [type, setType] = useState("undergrad");
  const [topic, setTopic] = useState("");
  const [running, setRunning] = useState(false);
  const [text, setText] = useState("");
  const [summary, setSummary] = useState<CiteSummary | null>(null);
  const [annotated, setAnnotated] = useState<string | null>(null);
  const [provenanceMap, setProvenanceMap] = useState<ProvenanceMap | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [jobId, setJobId] = useState<number | null>(null);
  const storageKey = corpusId
    ? `bibliocn.ai.review.${projectId}.${corpusId}`
    : `bibliocn.ai.review.${projectId}`;

  function hydrate(job: AiJob) {
    setJobId(job.id);
    setRunning(job.status === "queued" || job.status === "running");
    setText(job.resultText || "");
    setAnnotated(job.annotatedText || null);
    setProvenanceMap(job.provenanceMap ?? null);
    setSummary((job.summary as CiteSummary | null) || null);
    setErr(job.status === "failed" ? (job.error || "生成失败") : null);
    const req = job.request || {};
    if (typeof req.type === "string") setType(req.type);
    if (typeof req.topic === "string") setTopic(req.topic);
    localStorage.setItem(storageKey, String(job.id));
  }

  useEffect(() => {
    let cancelled = false;
    async function restore() {
      try {
        const saved = Number(localStorage.getItem(storageKey) || 0);
        if (saved) {
          try {
            const job = await getAiJob(projectId, saved);
            if (!cancelled) hydrate(job);
            return;
          } catch {
            // 旧 jobId 失效(DB 重置/项目重建 → 404)：清掉坏缓存并【回退 listAiJobs】，
            // 绝不因 localStorage 残留就让已生成的综述留白(关键回归根因)。
            localStorage.removeItem(storageKey);
          }
        }
        const res = await listAiJobs(projectId, { kind: "review", corpusId: corpusId || undefined, limit: 1 });
        if (!cancelled && res.jobs[0]) hydrate(res.jobs[0]);
      } catch {
        localStorage.removeItem(storageKey);
      }
    }
    restore();
    return () => { cancelled = true; };
  }, [projectId, corpusId]);

  useEffect(() => {
    if (!jobId || !running) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const job = await getAiJob(projectId, jobId);
        if (!cancelled) hydrate(job);
      } catch (e) {
        if (!cancelled) {
          setRunning(false);
          setErr((e as Error).message);
        }
      }
    };
    tick();
    const timer = window.setInterval(tick, 1200);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [projectId, jobId, running]);

  async function generate() {
    if (!topic.trim() || running) return;
    setRunning(true);
    setText("");
    setSummary(null);
    setAnnotated(null);
    setProvenanceMap(null);
    setErr(null);
    try {
      const job = await createAiJob(
        projectId,
        { kind: "review", corpusId: corpusId || undefined, type, topic },
        llm ?? (apiKey ? { apiKey } : {}),
      );
      hydrate(job);
    } catch (e) {
      setErr((e as Error).message);
      setRunning(false);
    }
  }

  const exportText = annotated || text;
  function exportMarkdown() {
    if (!exportText) return;
    const typeLabel = TYPES.find(([v]) => v === type)?.[1] ?? type;
    // 剥离溯源锚点包裹标记(保留内部 [n] 引用), 否则原始 [[anchor:...]] 串泄漏进导出文本。
    const clean = exportText
      .replace(/\[\[anchor:[A-Za-z0-9_-]+\]\]/g, "")
      .replace(/\[\[\/anchor\]\]/g, "");
    downloadMarkdown(
      `AI综述-${typeLabel}-${projectId}`,
      `# AI综述导出\n\n- 论型：${typeLabel}\n- 主题：${topic || "未填写"}\n\n${clean}\n`,
    );
  }

  return (
    <AiPanel title="AI 综述写作" intro="按论型与主题流式生成综述，并对引用做语料核验。">
      <AiToolbar>
        <AiField label="论型" htmlFor="review-type">
          <select id="review-type" className="input" value={type} onChange={(e) => setType(e.target.value)}>
            {TYPES.map(([v, label]) => (
              <option key={v} value={v}>
                {label}
              </option>
            ))}
          </select>
        </AiField>
        <AiField label="研究主题" htmlFor="review-topic">
          <AiTextInput
            id="review-topic"
            value={topic}
            placeholder="例：人工智能在教育中的应用"
            onChange={(e) => setTopic(e.target.value)}
          />
        </AiField>
        <button type="button" className="btn btn-primary" onClick={generate} disabled={!topic.trim() || running}>
          {running ? "生成中…" : "生成综述"}
        </button>
        <button type="button" className="btn" onClick={exportMarkdown} disabled={!exportText}>
          导出 Markdown
        </button>
      </AiToolbar>

      <AiKeyNotice hasKey={!!(llm?.apiKey || apiKey)} />

      <AiError message={err} />

      {/* 引用校验图例: 文字徽标 + 计数, 取代正文里难辨的裸 emoji */}
      {summary && (
        <div className="cite-legend" aria-live="polite">
          <span className="muted">引用校验</span>
          <span className="lg-item">
            <span className="badge badge-ok cite-mark" title="DOI/PMID 精确命中语料">已核验</span>
            <span className="lg-count tnum">{summary.green}</span>
          </span>
          <span className="lg-item">
            <span className="badge badge-warn cite-mark" title="作者+年模糊命中, 或编号待人工复核">待核</span>
            <span className="lg-count tnum">{summary.yellow}</span>
          </span>
          <span className="lg-item">
            <span className="badge badge-danger cite-mark" title="语料中未找到, 疑似虚构">存疑</span>
            <span className="lg-count tnum">{summary.red}</span>
          </span>
        </div>
      )}

      {/* dogfood A2: 轻量可信卡 —— 综述产出处直接呈现零伪造率+溯源覆盖(诚实空态)，
          不再让"可信"只停留在首页营销区。完整 grounding 指标(哈希链等)在历史 run 的 TrustCard。 */}
      {summary && (() => {
        const g = summary.green ?? 0;
        const y = summary.yellow ?? 0;
        const r = summary.red ?? 0;
        const total = g + y + r;
        const zeroFab = total > 0 ? Math.round(((g + y) / total) * 100) : null;
        const provCount = provenanceMap ? Object.keys(provenanceMap).length : 0;
        return (
          <div
            aria-live="polite"
            style={{ display: "flex", alignItems: "center", gap: "0.6rem", margin: "0.1rem 0 0.7rem" }}
          >
            <span
              className={`badge ${zeroFab === null ? "badge-warn" : r > 0 ? "badge-danger" : "badge-ok"}`}
              title="零伪造率 =（已核验+待核）/全部引用；红色为语料中找不到的伪造引用。无引用时不可评分；存在伪造时标红（codex A2-P3）。"
            >
              零伪造率 {zeroFab === null ? "不可评分" : `${zeroFab}%`}
            </span>
            <span className="muted" style={{ fontSize: "0.8rem" }}>
              共 {total} 处引用{provCount > 0 ? ` · ${provCount} 处可点击溯源原文` : ""}
            </span>
          </div>
        );
      })()}

      {/* 有溯源映射 → 可溯源综述(点引用跳原文页/段)优先，reviewMd 取 annotated||text；
          否则 annotated → 带引用徽标 markdown；再否则 text → 流式 markdown */}
      {provenanceMap && Object.keys(provenanceMap).length > 0 ? (
        <div className="ai-review-body">
          <ReviewWithProvenance
            projectId={Number(projectId)}
            reviewMd={annotated || text}
            provenanceMap={provenanceMap}
          />
        </div>
      ) : annotated ? (
        <div className="ai-review-body">
          <AiMarkdown content={annotated} projectId={projectId} />
        </div>
      ) : text ? (
        <div className="ai-review-body">
          <AiMarkdown content={text} streaming live projectId={projectId} />
        </div>
      ) : (
        !running && !err && <AiEmpty>填写研究主题并点击「生成综述」开始。</AiEmpty>
      )}
    </AiPanel>
  );
}
