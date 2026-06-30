import { useEffect, useState } from "react";
import {
  apiAssetSrc,
  createAiJob,
  getAiJob,
  listAiJobs,
  type AiJob,
  type LlmRequestOptions,
} from "../api/client";
import { useImageSettings } from "../api/useImageSettings";
import { downloadMarkdown } from "../lib/download";
import {
  AiPanel,
  AiToolbar,
  AiField,
  AiTextarea,
  AiActions,
  AiResultBox,
  AiEmpty,
  AiError,
  AiKeyNotice,
  AiMarkdown,
} from "./ai";

type ToolMode = "summary" | "translate" | "rewrite" | "infographic";
type TextToolMode = Exclude<ToolMode, "infographic">;

function imageUrlFromJob(job: AiJob): string {
  const summary = (job.summary || {}) as Record<string, unknown>;
  return typeof summary.url === "string" ? summary.url : "";
}

export function AiToolsPanel({
  projectId,
  corpusId,
  llm,
  apiKey,
}: {
  projectId: string;
  corpusId?: string;
  llm?: LlmRequestOptions;
  apiKey?: string;
}) {
  const { settings: imageSettings } = useImageSettings();
  const [mode, setMode] = useState<ToolMode>("summary");
  const [text, setText] = useState("");
  const [topic, setTopic] = useState("");
  const [style, setStyle] = useState("学术信息图，宣纸质感，墨色线条，朱砂强调，适合论文汇报");
  const [direction, setDirection] = useState<"en2zh" | "zh2en">("en2zh");
  const [action, setAction] = useState("compress");
  const [result, setResult] = useState("");
  const [imagePrompt, setImagePrompt] = useState("");
  const [imageUrl, setImageUrl] = useState("");
  const [running, setRunning] = useState(false);
  const [imageRunning, setImageRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [jobId, setJobId] = useState<number | null>(null);
  const [imageJobId, setImageJobId] = useState<number | null>(null);

  const effectiveLlm = llm ?? apiKey;
  const hasLlmKey = !!(llm?.apiKey || apiKey);
  const modeLabel =
    mode === "summary" ? "总结" :
    mode === "translate" ? "翻译" :
    mode === "rewrite" ? "重写" : "一图读懂";
  const storageKey = `bibliocn.ai.tool.${projectId}.${mode}`;
  const imageStorageKey = `bibliocn.ai.tool.${projectId}.infographic_image`;

  function hydrate(job: AiJob) {
    setJobId(job.id);
    setRunning(job.status === "queued" || job.status === "running");
    setErr(job.status === "failed" ? (job.error || "处理失败") : null);
    const req = job.request || {};
    if (typeof req.text === "string") setText(req.text);
    if (typeof req.topic === "string") setTopic(req.topic);
    if (typeof req.style === "string") setStyle(req.style);
    if (typeof req.direction === "string") setDirection(req.direction as "en2zh" | "zh2en");
    if (typeof req.action === "string") setAction(req.action);
    if (job.kind === "infographic_prompt") {
      setImagePrompt(job.resultText || "");
    } else {
      setResult(job.resultText || "");
    }
    localStorage.setItem(storageKey, String(job.id));
  }

  function hydrateImage(job: AiJob) {
    setImageJobId(job.id);
    setImageRunning(job.status === "queued" || job.status === "running");
    setErr(job.status === "failed" ? (job.error || "生图失败") : null);
    const req = job.request || {};
    if (typeof req.imagePrompt === "string") setImagePrompt(req.imagePrompt);
    setResult(job.resultText || "");
    setImageUrl(imageUrlFromJob(job));
    localStorage.setItem(imageStorageKey, String(job.id));
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
        const kind = mode === "infographic" ? "infographic_prompt" : mode;
        const res = await listAiJobs(projectId, { kind, limit: 1 });
        if (!cancelled && res.jobs[0]) hydrate(res.jobs[0]);
      } catch {
        localStorage.removeItem(storageKey);
      }
    }
    restore();
    return () => { cancelled = true; };
  }, [projectId, mode]);

  useEffect(() => {
    if (mode !== "infographic") return;
    let cancelled = false;
    async function restoreImage() {
      try {
        const saved = Number(localStorage.getItem(imageStorageKey) || 0);
        if (saved) {
          const job = await getAiJob(projectId, saved);
          if (!cancelled) hydrateImage(job);
          return;
        }
        const res = await listAiJobs(projectId, { kind: "infographic_image", limit: 1 });
        if (!cancelled && res.jobs[0]) hydrateImage(res.jobs[0]);
      } catch {
        localStorage.removeItem(imageStorageKey);
      }
    }
    restoreImage();
    return () => { cancelled = true; };
  }, [projectId, mode]);

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

  useEffect(() => {
    if (!imageJobId || !imageRunning) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const job = await getAiJob(projectId, imageJobId);
        if (!cancelled) hydrateImage(job);
      } catch (e) {
        if (!cancelled) {
          setImageRunning(false);
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
  }, [projectId, imageJobId, imageRunning]);

  async function runTextTool() {
    if (!text.trim() || running) return;
    if (mode === "infographic") return;
    setRunning(true);
    setResult("");
    setErr(null);
    try {
      const kind: TextToolMode = mode;
      const job = await createAiJob(
        projectId,
        { kind, text, direction, action },
        effectiveLlm,
      );
      hydrate(job);
    } catch (e) {
      setErr((e as Error).message);
      setRunning(false);
    }
  }

  async function generateImagePrompt() {
    if (running) return;
    setRunning(true);
    setImagePrompt("");
    setImageUrl("");
    setResult("");
    setErr(null);
    try {
      const job = await createAiJob(
        projectId,
        { kind: "infographic_prompt", corpusId, topic, text, style },
        effectiveLlm,
      );
      hydrate(job);
    } catch (e) {
      setErr((e as Error).message);
      setRunning(false);
    }
  }

  async function generateImage() {
    if (!imagePrompt.trim() || imageRunning) return;
    setImageRunning(true);
    setImageUrl("");
    setErr(null);
    try {
      const job = await createAiJob(
        projectId,
        { kind: "infographic_image", topic, imagePrompt },
        effectiveLlm,
        imageSettings,
      );
      hydrateImage(job);
    } catch (e) {
      setErr((e as Error).message);
      setImageRunning(false);
    }
  }

  const canRunText = !!text.trim() && !running;
  const canGeneratePrompt = !!(text.trim() || corpusId) && !running;
  const resolvedImageUrl = apiAssetSrc(imageUrl);

  return (
    <AiPanel title="AI 工具" intro="对文本与项目语料做总结、翻译、重写，并生成可审计的一图读懂。">
      <AiToolbar>
        <AiField label="功能">
          <select className="input" value={mode} onChange={(e) => setMode(e.target.value as ToolMode)}>
            <option value="summary">总结</option>
            <option value="translate">翻译</option>
            <option value="rewrite">重写</option>
            <option value="infographic">一图读懂</option>
          </select>
        </AiField>
        {mode === "translate" && (
          <AiField label="方向">
            <select className="input" value={direction} onChange={(e) => setDirection(e.target.value as typeof direction)}>
              <option value="en2zh">英→中</option>
              <option value="zh2en">中→英</option>
            </select>
          </AiField>
        )}
        {mode === "rewrite" && (
          <AiField label="动作">
            <select className="input" value={action} onChange={(e) => setAction(e.target.value)}>
              <option value="compress">压缩</option>
              <option value="expand">扩写</option>
              <option value="counter">反驳</option>
              <option value="casual">口语化</option>
            </select>
          </AiField>
        )}
      </AiToolbar>

      {mode === "infographic" && (
        <div className="infographic-brief">
          <AiField label="研究主题">
            <input
              className="input"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="例如：慢性病管理中的数字健康干预"
            />
          </AiField>
          <AiField label="视觉风格">
            <input
              className="input"
              value={style}
              onChange={(e) => setStyle(e.target.value)}
              placeholder="例如：学术信息图，四象限结构，墨色+金色强调"
            />
          </AiField>
        </div>
      )}

      <AiTextarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={mode === "infographic" ? "可粘贴综述初稿/分析摘要；若当前项目已构建分析语料，也会自动补充 R 分析与文献结果。" : "粘贴文本…"}
        rows={mode === "infographic" ? 5 : 6}
      />

      <AiActions>
        {mode === "infographic" ? (
          <>
            <button type="button" className="btn btn-primary" onClick={generateImagePrompt} disabled={!canGeneratePrompt}>
              {running ? "正在生成提示词..." : "生成生图提示词"}
            </button>
            {imagePrompt.trim() && (
              <button type="button" className="btn" onClick={generateImage} disabled={imageRunning}>
                {imageRunning ? "AI 生图中..." : "AI 生图"}
              </button>
            )}
            <button
              type="button"
              className="btn"
              disabled={!imagePrompt && !resolvedImageUrl}
              onClick={() => downloadMarkdown(
                `一图读懂-${projectId}`,
                `# 一图读懂\n\n## 研究主题\n\n${topic || "(未填写)"}\n\n## 生图提示词\n\n${imagePrompt}\n\n${resolvedImageUrl ? `## 图片\n\n![一图读懂](${resolvedImageUrl})\n` : ""}`,
              )}
            >
              导出 Markdown
            </button>
          </>
        ) : (
          <>
            <button type="button" className="btn btn-primary" onClick={runTextTool} disabled={!canRunText}>
              {running ? "处理中..." : "运行"}
            </button>
            <button
              type="button"
              className="btn"
              disabled={!result}
              onClick={() => {
                if (!result) return;
                downloadMarkdown(
                  `AI工具-${modeLabel}-${projectId}`,
                  `# AI工具导出：${modeLabel}\n\n## 输入\n\n${text}\n\n## 输出\n\n${result}\n`,
                );
              }}
            >
              导出 Markdown
            </button>
          </>
        )}
      </AiActions>

      <AiKeyNotice hasKey={hasLlmKey} />
      {mode === "infographic" && !imageSettings.apiKey && (
        <p className="ai-key-notice">未配置生图模型 key 时，AI 生图会降级为结构化 SVG 占位图，便于离线演示。</p>
      )}

      <AiError message={err} />

      {mode === "infographic" ? (
        <div className="infographic-workflow">
          <AiResultBox>
            <h3>生图提示词</h3>
            {imagePrompt ? (
              <AiTextarea value={imagePrompt} onChange={(e) => setImagePrompt(e.target.value)} rows={8} />
            ) : (
              !running && <AiEmpty>先点击“生成生图提示词”，系统会基于综述文本、R 分析和文献结果生成可复核提示词。</AiEmpty>
            )}
          </AiResultBox>
          {(resolvedImageUrl || imageRunning) && (
            <AiResultBox>
              <h3>一图读懂预览</h3>
              {resolvedImageUrl ? (
                <div className="infographic-preview">
                  <img src={resolvedImageUrl} alt="一图读懂" />
                  <a className="btn" href={resolvedImageUrl} download target="_blank" rel="noreferrer">下载图片</a>
                </div>
              ) : (
                <AiEmpty>图片生成中，请稍候...</AiEmpty>
              )}
            </AiResultBox>
          )}
        </div>
      ) : result ? (
        <AiResultBox>
          <AiMarkdown content={result} projectId={projectId} />
        </AiResultBox>
      ) : (
        !running && !err && <AiEmpty>输入文本并点击“运行”查看结果。</AiEmpty>
      )}
    </AiPanel>
  );
}
