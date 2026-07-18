import { useCallback, useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { createAiJob, getAiJob, listAiJobs } from "../api/client";
import type { AiJob, CiteSummary, LlmRequestOptions } from "../api/client";
import type { ProjectDetail } from "../api/agentHooks";
import type { RCorpusId } from "../api/corpusIds";
import type { ProvenanceMap } from "../types/provenance";

export const REVIEW_TYPES: [string, string][] = [
  ["undergrad", "本科综述"],
  ["master", "硕士综述"],
  ["phd", "博士综述"],
  ["grant", "基金本子"],
  ["proposal", "开题报告"],
  ["sci_intro", "SCI Intro"],
];

interface ReviewPrecheck {
  /** 结构化拦截原因（供埋点/引导卡区分，避免按 message 文案字符串匹配）。 */
  reason: "no_included" | "no_fulltext";
  message: string;
  detail: string;
  action: string;
  href: string;
}

interface UseReviewJobOptions {
  projectId: string;
  corpusId?: RCorpusId;
  llm?: LlmRequestOptions;
  apiKey?: string;
  projectStats?: Pick<ProjectDetail, "includedCount" | "readableFulltextCount">;
}

export interface UseReviewJobState {
  type: string;
  setType: (value: string) => void;
  topic: string;
  setTopic: (value: string) => void;
  running: boolean;
  text: string;
  summary: CiteSummary | null;
  annotated: string | null;
  provenanceMap: ProvenanceMap | null;
  err: string | null;
  jobId: number | null;
  precheck: ReviewPrecheck | null;
  exportText: string;
  generate: () => Promise<void>;
  cancel: () => void;
}

export function useReviewJob({
  projectId,
  corpusId,
  llm,
  apiKey,
  projectStats,
}: UseReviewJobOptions): UseReviewJobState {
  const queryClient = useQueryClient();
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

  const pidNum = Number(projectId);
  const cachedProject = Number.isFinite(pidNum)
    ? queryClient.getQueryData<ProjectDetail>(["project", pidNum])
    : undefined;
  const stats = projectStats ?? cachedProject;

  const precheck = useMemo<ReviewPrecheck | null>(() => {
    if (!stats) return null;
    if ((stats.includedCount ?? 0) <= 0) {
      return {
        reason: "no_included",
        message: "先纳入文献",
        detail: "请到文献库完成纳排后再生成综述。",
        action: "去文献库纳排",
        href: `/projects/${projectId}/library`,
      };
    }
    if (typeof stats.readableFulltextCount === "number" && stats.readableFulltextCount <= 0) {
      return {
        reason: "no_fulltext",
        message: "先导入/解析全文",
        detail: "当前纳入文献还没有可读 Markdown 全文。",
        action: "去文献库导入全文",
        href: `/projects/${projectId}/library`,
      };
    }
    return null;
  }, [projectId, stats]);

  const hydrate = useCallback(
    (job: AiJob) => {
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
    },
    [storageKey],
  );

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
            // 旧 jobId 失效(DB 重置/项目重建 → 404)：清掉坏缓存并回退最新 review job。
            localStorage.removeItem(storageKey);
          }
        }
        const res = await listAiJobs(projectId, { kind: "review", corpusId: corpusId || undefined, limit: 1 });
        if (!cancelled && res.jobs[0]) hydrate(res.jobs[0]);
      } catch {
        localStorage.removeItem(storageKey);
      }
    }
    void restore();
    return () => {
      cancelled = true;
    };
  }, [projectId, corpusId, storageKey, hydrate]);

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
    void tick();
    const timer = window.setInterval(tick, 1200);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [projectId, jobId, running, hydrate]);

  const generate = useCallback(async () => {
    if (!topic.trim() || running || precheck) return;
    setRunning(true);
    // 清掉上一轮/恢复的历史 jobId：否则本次 createAiJob 失败时，running true→false 的终态
    // 埋点会用旧 jobId 误报 review_job_failed，把历史成功任务记成失败（codex P1）。
    setJobId(null);
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
  }, [apiKey, corpusId, hydrate, llm, precheck, projectId, running, topic, type]);

  const cancel = useCallback(() => {
    setRunning(false);
  }, []);

  return {
    type,
    setType,
    topic,
    setTopic,
    running,
    text,
    summary,
    annotated,
    provenanceMap,
    err,
    jobId,
    precheck,
    exportText: annotated || text,
    generate,
    cancel,
  };
}
