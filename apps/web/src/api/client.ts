// 类型化 API 客户端。类型由 packages/contracts/openapi.yaml 生成 (单一真源, Codex-10)。
// 生成: pnpm gen:api → src/api/schema.d.ts
import type { components } from "./schema";
import type { StructureResponse, ProvenanceMap } from "../types/provenance";
import type {
  GapCandidate,
  GapDiscoverAccepted,
  GapPatchRequest,
  GapVerdictResult,
  GapVerifyAccepted,
  ScratchpadState,
} from "../types/research";

export type Health = components["schemas"]["Health"];
export type CorpusRef = components["schemas"]["CorpusRef"];
export type OverviewResult = components["schemas"]["OverviewResult"];
export type DbSource = components["schemas"]["DbSource"];
export type SourcesResult = components["schemas"]["SourcesResult"];
export type AuthorsResult = components["schemas"]["AuthorsResult"];
export type DocumentsResult = components["schemas"]["DocumentsResult"];
export type Graph = components["schemas"]["Graph"];
export type NetworkResult = components["schemas"]["NetworkResult"];
export type SocialResult = components["schemas"]["SocialResult"];
export type PrismaRequest = components["schemas"]["PrismaRequest"];
export type PrismaResult = components["schemas"]["PrismaResult"];
export type TextResult = components["schemas"]["TextResult"];
export type ScreenResult = components["schemas"]["ScreenResult"];
export type ChatMessage = components["schemas"]["ChatMessage"];
export type CiteResult = components["schemas"]["CiteResult"];
// A4 高级图信封类型
export type AnalysisUnavailable = components["schemas"]["AnalysisUnavailable"];
export type AnalysisUnavailableReason = AnalysisUnavailable["reason"];
export type AuthorProductionEnvelope = components["schemas"]["AuthorProductionEnvelope"];
export type KeywordTrendEnvelope = components["schemas"]["KeywordTrendEnvelope"];
export type CitedRefsEnvelope = components["schemas"]["CitedRefsEnvelope"];
// A5 高级图② 信封类型
export type ThematicEnvelope = components["schemas"]["ThematicEnvelope"];
export type EvolutionEnvelope = components["schemas"]["EvolutionEnvelope"];
export type HistciteEnvelope = components["schemas"]["HistciteEnvelope"];
export type ThreeFieldEnvelope = components["schemas"]["ThreeFieldEnvelope"];

const DEFAULT_API_BASE = import.meta.env?.DEV ? "http://localhost:8000" : "/api";
const BASE: string =
  ((import.meta.env?.VITE_API_BASE as string | undefined) || "").trim() || DEFAULT_API_BASE;

export class ApiError extends Error {
  constructor(
    public code: string,
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// 网络层失败也归一为 ApiError, 调用方只需处理一种错误类型 (Codex step4-P2)
async function doFetch(input: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(input, init);
  } catch (e) {
    throw new ApiError("NETWORK_ERROR", 0, (e as Error).message || "网络错误");
  }
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let body: { code?: string; message?: string; detail?: unknown } = {};
    try {
      body = await res.json();
    } catch {
      /* 非 JSON 错误体 */
    }
    const validationMessage = Array.isArray(body.detail)
      ? body.detail
          .map((item) => {
            if (!item || typeof item !== "object") return "";
            const detail = item as { loc?: unknown[]; msg?: string };
            const field = Array.isArray(detail.loc) ? detail.loc.slice(1).join(".") : "";
            return field && detail.msg ? `${field}: ${detail.msg}` : detail.msg ?? "";
          })
          .filter(Boolean)
          .slice(0, 3)
          .join("; ")
      : undefined;
    throw new ApiError(
      body.code ?? (res.status === 422 ? "VALIDATION_ERROR" : "INTERNAL"),
      res.status,
      body.message ?? validationMessage ?? res.statusText,
    );
  }
  return (await res.json()) as T;
}

const enc = encodeURIComponent;

export interface LlmRequestOptions {
  apiKey?: string;
  baseUrl?: string;
  model?: string;
}

export interface SciverseRequestOptions {
  apiToken?: string;
  baseUrl?: string;
}

export interface ImageRequestOptions {
  apiKey?: string;
  baseUrl?: string;
  model?: string;
  size?: string;
}

function applyLlmHeaders(headers: Record<string, string>, opts?: LlmRequestOptions): Record<string, string> {
  if (opts?.apiKey) headers["X-LLM-Key"] = opts.apiKey;
  if (opts?.baseUrl) headers["X-LLM-Base-URL"] = opts.baseUrl;
  if (opts?.model) headers["X-LLM-Model"] = opts.model;
  return headers;
}

function applySciverseHeaders(
  headers: Record<string, string>,
  opts?: SciverseRequestOptions,
): Record<string, string> {
  if (opts?.apiToken) headers["X-Sciverse-Token"] = opts.apiToken;
  if (opts?.baseUrl) headers["X-Sciverse-Base-URL"] = opts.baseUrl;
  return headers;
}

function applyImageHeaders(headers: Record<string, string>, opts?: ImageRequestOptions): Record<string, string> {
  if (opts?.apiKey) headers["X-Image-Key"] = opts.apiKey;
  if (opts?.baseUrl) headers["X-Image-Base-URL"] = opts.baseUrl;
  if (opts?.model) headers["X-Image-Model"] = opts.model;
  if (opts?.size) headers["X-Image-Size"] = opts.size;
  return headers;
}

export function apiAssetSrc(path?: string | null): string {
  const value = (path || "").trim();
  if (!value) return "";
  if (/^(data:|blob:|https?:\/\/)/i.test(value)) return value;
  return `${BASE}${value.startsWith("/") ? value : `/${value}`}`;
}

export async function getHealth(): Promise<Health> {
  return handle<Health>(await doFetch(`${BASE}/healthz`));
}

export async function createCorpus(
  projectId: string,
  file: File,
  dbsource: DbSource,
): Promise<CorpusRef> {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("dbsource", dbsource);
  return handle<CorpusRef>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus`, { method: "POST", body: fd }),
  );
}

export async function getCorpus(projectId: string, corpusId: string): Promise<CorpusRef> {
  return handle<CorpusRef>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}`),
  );
}

// 接入响应可能附带匹配统计 (路径 B)
export type IngestRef = CorpusRef & { matched?: number; unmatched?: number; extracted?: number };

// 路径 A: 主题词 → OpenAlex 检索建库
export interface TopicReq { query: string; n?: number; since?: string; withRefs?: boolean }
export async function createFromTopic(projectId: string, req: TopicReq): Promise<IngestRef> {
  return handle<IngestRef>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/from-topic`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  );
}

// 路径 B: 粘贴参考文献 → LLM 抽取 + OpenAlex 反查建库
export async function createFromRefs(
  projectId: string,
  text: string,
  withRefs = true,
  llm?: LlmRequestOptions | string,
): Promise<IngestRef> {
  const headers = applyLlmHeaders(
    { "Content-Type": "application/json" },
    typeof llm === "string" ? { apiKey: llm } : llm,
  );
  return handle<IngestRef>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/from-refs`, {
      method: "POST",
      headers,
      body: JSON.stringify({ text, withRefs }),
    }),
  );
}

const DEMO_WOS_TEXT = `FN Clarivate Analytics Web of Science
VR 1.0
PT J
AU Smith, J
   Doe, A
AF Smith, John
   Doe, Alice
TI Bibliometric methods for science mapping
SO JOURNAL OF SCIENCE MAPPING
LA English
DT Article
DE bibliometrics; science mapping; co-citation
AB This paper reviews bibliometric methods for science mapping analysis.
C1 [Smith, John] Test University, Department of Science, Testville, USA.
CR Brown D, 2010, J SCIENTOMETR, V1, P1
   Green E, 2012, J RES POLICY, V2, P5
NR 2
TC 25
PY 2019
JI J. Sci. Mapp.
UT WOS:000000000000001
ER

PT J
AU Lee, K
AF Lee, Kevin
TI Author collaboration networks in scientometrics
SO SCIENTOMETRICS REVIEW
LA English
DT Article
DE collaboration network; co-authorship; scientometrics
AB An analysis of author collaboration networks.
C1 [Lee, Kevin] Metro College, School of Data, Metro City, Australia.
CR Smith J, 2019, J SCI MAPPING, V1, P10
NR 1
TC 8
PY 2022
JI Scientometr. Rev.
UT WOS:000000000000002
ER

EF
`;

// 路径 D: 一键加载内置合成样例 (生成临时 File → 走上传接入)
export async function loadDemo(projectId: string): Promise<CorpusRef> {
  const file = new File([DEMO_WOS_TEXT], "synthetic_wos_demo.txt", { type: "text/plain" });
  return createCorpus(projectId, file, "wos");
}

export async function getOverview(
  projectId: string,
  corpusId: string,
): Promise<OverviewResult> {
  return handle<OverviewResult>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/overview`),
  );
}

export async function getSources(projectId: string, corpusId: string): Promise<SourcesResult> {
  return handle<SourcesResult>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/sources`),
  );
}

export async function getAuthors(projectId: string, corpusId: string): Promise<AuthorsResult> {
  return handle<AuthorsResult>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/authors`),
  );
}

export async function getDocuments(projectId: string, corpusId: string): Promise<DocumentsResult> {
  return handle<DocumentsResult>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/documents`),
  );
}

// --- A4 高级图 (返回可用性信封; available:false 也是 HTTP 200) ---

export async function getAuthorProduction(
  projectId: string,
  corpusId: string,
): Promise<AuthorProductionEnvelope> {
  return handle<AuthorProductionEnvelope>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/authors/production`),
  );
}

export async function getKeywordTrend(
  projectId: string,
  corpusId: string,
): Promise<KeywordTrendEnvelope> {
  return handle<KeywordTrendEnvelope>(
    await doFetch(
      `${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/documents/keyword-trend`,
    ),
  );
}

export async function getCitedRefs(
  projectId: string,
  corpusId: string,
): Promise<CitedRefsEnvelope> {
  return handle<CitedRefsEnvelope>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/documents/cited-refs`),
  );
}

// --- A5 高级图② (返回可用性信封; available:false 也是 HTTP 200) ---

export async function getThematic(
  projectId: string,
  corpusId: string,
): Promise<ThematicEnvelope> {
  return handle<ThematicEnvelope>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/conceptual/thematic`),
  );
}

export async function getEvolution(
  projectId: string,
  corpusId: string,
): Promise<EvolutionEnvelope> {
  return handle<EvolutionEnvelope>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/conceptual/evolution`),
  );
}

export async function getHistcite(
  projectId: string,
  corpusId: string,
): Promise<HistciteEnvelope> {
  return handle<HistciteEnvelope>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/intellectual/histcite`),
  );
}

export async function getThreeField(
  projectId: string,
  corpusId: string,
): Promise<ThreeFieldEnvelope> {
  return handle<ThreeFieldEnvelope>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/overview/threefield`),
  );
}

// 网络端点请求 top100 (A5 §4.4): 后端给到 100, 前端滑块才能真正切到 100。
export async function getConceptual(projectId: string, corpusId: string): Promise<NetworkResult> {
  return handle<NetworkResult>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/conceptual?limit=100`),
  );
}

export async function getIntellectual(projectId: string, corpusId: string): Promise<NetworkResult> {
  return handle<NetworkResult>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/intellectual?limit=100`),
  );
}

export async function getSocial(projectId: string, corpusId: string): Promise<SocialResult> {
  return handle<SocialResult>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/social?limit=100`),
  );
}

export async function buildPrisma(projectId: string, req: PrismaRequest): Promise<PrismaResult> {
  return handle<PrismaResult>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/prisma`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  );
}

// A7: 报告格式 + 选项 (镜像 openapi ReportOptions)
export type ReportFormat = "md" | "html" | "docx";
export type ReportOptions = components["schemas"]["ReportOptions"];
export type ReportSection = NonNullable<ReportOptions["sections"]>[number];

export function reportUrl(projectId: string, corpusId: string, format: ReportFormat): string {
  return `${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/report?format=${format}`;
}

function filenameFromDisposition(disposition: string | null, fallback: string): string {
  if (!disposition) return fallback;
  const utf8 = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8?.[1]) return decodeURIComponent(utf8[1].replace(/"/g, ""));
  const plain = disposition.match(/filename="?([^";]+)"?/i);
  return plain?.[1] ? plain[1] : fallback;
}

// 用 fetch 取 blob 再触发下载: 失败时能抛 ApiError 给 UI, 而非把用户带到 JSON 错误页 (Codex slice5-P2)
// A7: POST + ReportOptions (title/author/sections/可选 prismaCounts/reviewMarkdown); format 走 query。
export async function downloadReport(
  projectId: string,
  corpusId: string,
  format: ReportFormat,
  options?: ReportOptions,
): Promise<void> {
  const res = await doFetch(reportUrl(projectId, corpusId, format), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(options ?? {}),
  });
  if (!res.ok) {
    await handle(res); // 抛 ApiError (含 503 PANDOC_UNAVAILABLE → UI 据此降级)
    return;
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  const fallback = `report.${format}`;
  const filename = filenameFromDisposition(res.headers.get("Content-Disposition"), fallback);
  a.download = /\.[A-Za-z0-9]{2,5}$/.test(filename) ? filename : fallback;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// --- 综述流式 (SSE over fetch; 需 POST body + X-LLM-Key 头, EventSource 做不到) ---
export type CiteSummary = { green: number; yellow: number; red: number };

export interface ReviewHandlers {
  onMeta?: (d: { template: string; chapters: string[]; docCount: number }) => void;
  onChapter?: (d: { index: number; title: string }) => void;
  onToken?: (text: string) => void;
  onCitations?: (d: { summary: CiteSummary; annotated: string }) => void;
  onDone?: (d: { chapters: number }) => void;
  onError?: (d: { code: string; message: string }) => void;
}

async function _postJson<T>(path: string, body: unknown, llm?: LlmRequestOptions | string): Promise<T> {
  const headers = applyLlmHeaders(
    { "Content-Type": "application/json" },
    typeof llm === "string" ? { apiKey: llm } : llm,
  );
  return handle<T>(await doFetch(`${BASE}${path}`, { method: "POST", headers, body: JSON.stringify(body) }));
}

export function aiTranslate(p: string, text: string, direction: "en2zh" | "zh2en", llm?: LlmRequestOptions | string) {
  return _postJson<TextResult>(`/projects/${enc(p)}/ai/translate`, { text, direction }, llm);
}
export function aiRewrite(p: string, text: string, action: string, llm?: LlmRequestOptions | string) {
  return _postJson<TextResult>(`/projects/${enc(p)}/ai/rewrite`, { text, action }, llm);
}
/**
 * @deprecated 旧「总结」单端点（main.py :595 一带）。**禁用于文献综述**——综述统一走
 * run_review（createAiJob kind:"review"，带 provenance）。
 * 现状（B5 核实）：前端已无任何调用方——综述走 ReviewPanel→createAiJob kind:"review"，
 * AiTools「总结」工具走 createAiJob kind:"summary"。保留本导出仅为向后兼容，勿新增 review 用途。
 */
export function aiSummary(p: string, text: string, llm?: LlmRequestOptions | string) {
  return _postJson<TextResult>(`/projects/${enc(p)}/ai/summary`, { text }, llm);
}

export type AiJobStatus = "queued" | "running" | "done" | "failed" | "cancelled";
export type AiJobKind = "review" | "chat" | "summary" | "translate" | "rewrite" | "infographic_prompt" | "infographic_image" | "gap_discover";

export interface AiJob {
  id: number;
  projectId: number;
  corpusId?: string | null;
  kind: AiJobKind;
  status: AiJobStatus;
  request?: Record<string, unknown> | null;
  resultText: string;
  annotatedText?: string | null;
  summary?: CiteSummary | Record<string, unknown> | null;
  events: Array<{ event: string; data?: Record<string, unknown> }>;
  error?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
  completedAt?: string | null;
  // 引用溯源（契约 §2.3/§5.6）：综述结果附带的 anchor_id → 定位映射。
  // Track A 接入后非空 → 前端渲染可溯源综述；缺省时优雅降级为纯文本。
  provenanceMap?: ProvenanceMap | null;
}

export type AiJobCreate = {
  kind: AiJobKind;
  corpusId?: string | null;
  type?: string;
  topic?: string;
  query?: string;
  history?: ChatMessage[];
  text?: string;
  direction?: "en2zh" | "zh2en";
  action?: string;
  style?: string;
  imagePrompt?: string;
};

export async function createAiJob(
  p: string,
  body: AiJobCreate,
  llm?: LlmRequestOptions | string,
  image?: ImageRequestOptions,
) {
  const headers = applyImageHeaders(
    applyLlmHeaders(
      { "Content-Type": "application/json" },
      typeof llm === "string" ? { apiKey: llm } : llm,
    ),
    image,
  );
  // 入口也归一化：create 直返 done + provenance_map(snake) 时也能进可溯源渲染（codex 复审 P1）
  return normalizeAiJob(
    await handle<AiJob>(
      await doFetch(`${BASE}/projects/${enc(p)}/ai/jobs`, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
      }),
    ),
  );
}

// 后端可能按契约用 snake_case 返回 provenance_map（其余字段 camelCase）。
// 归一化为 provenanceMap，避免真实综述因键名不一致而永不进入可溯源渲染（codex 终审 P1）。
function normalizeAiJob(raw: AiJob & { provenance_map?: ProvenanceMap | null }): AiJob {
  if (raw.provenanceMap == null && raw.provenance_map != null) {
    raw.provenanceMap = raw.provenance_map;
  }
  return raw;
}

export async function getAiJob(p: string, jobId: number) {
  return normalizeAiJob(await handle<AiJob>(await doFetch(`${BASE}/projects/${enc(p)}/ai/jobs/${jobId}`)));
}

export async function listAiJobs(p: string, params: { kind?: AiJobKind; corpusId?: string; limit?: number } = {}) {
  const qs = new URLSearchParams();
  if (params.kind) qs.set("kind", params.kind);
  if (params.corpusId) qs.set("corpusId", params.corpusId);
  if (params.limit) qs.set("limit", String(params.limit));
  const suffix = qs.toString() ? `?${qs}` : "";
  const res = await handle<{ jobs: AiJob[] }>(await doFetch(`${BASE}/projects/${enc(p)}/ai/jobs${suffix}`));
  return { jobs: res.jobs.map(normalizeAiJob) };
}
export function aiScreen(p: string, c: string, topic: string, limit: number, llm?: LlmRequestOptions | string) {
  return _postJson<ScreenResult>(`/projects/${enc(p)}/corpus/${enc(c)}/ai/screen`, { topic, limit }, llm);
}

export async function pingLlm(llm: LlmRequestOptions): Promise<{ ok: boolean; model: string; baseUrl: string; content: string }> {
  const headers = applyLlmHeaders({ "Content-Type": "application/json" }, llm);
  return handle<{ ok: boolean; model: string; baseUrl: string; content: string }>(
    await doFetch(`${BASE}/ai/ping`, { method: "POST", headers, body: "{}" }),
  );
}

export async function pingSciverse(
  sciverse: SciverseRequestOptions,
): Promise<{ ok: boolean; baseUrl: string; resultCount: number }> {
  const headers = applySciverseHeaders({ "Content-Type": "application/json" }, sciverse);
  return handle<{ ok: boolean; baseUrl: string; resultCount: number }>(
    await doFetch(`${BASE}/sciverse/ping`, {
      method: "POST",
      headers,
      body: JSON.stringify({}),
    }),
  );
}

export async function pingImage(
  image: ImageRequestOptions,
): Promise<{ ok: boolean; model: string; baseUrl: string; size: string; detail?: string | null }> {
  const headers = applyImageHeaders({ "Content-Type": "application/json" }, image);
  return handle<{ ok: boolean; model: string; baseUrl: string; size: string; detail?: string | null }>(
    await doFetch(`${BASE}/ai/image/ping`, {
      method: "POST",
      headers,
      body: JSON.stringify({}),
    }),
  );
}

export interface SciverseMetaSearchResult {
  candidates: SearchCandidate[];
  totalCount?: number | null;
  page?: number | null;
  pageSize?: number | null;
  totalPages?: number | null;
  nextCursor?: string | null;
  searchTimeMs?: number | null;
}

export async function searchSciverseMeta(
  req: {
    query?: string;
    filters?: Record<string, unknown>[];
    sort?: Record<string, unknown>[];
    fields?: string[];
    page?: number;
    pageSize?: number;
    cursor?: string;
    freshnessBoost?: "NONE" | "MILD" | "STRONG";
  },
  sciverse?: SciverseRequestOptions,
): Promise<SciverseMetaSearchResult> {
  const headers = applySciverseHeaders({ "Content-Type": "application/json" }, sciverse);
  return handle<SciverseMetaSearchResult>(
    await doFetch(`${BASE}/sciverse/meta-search`, {
      method: "POST",
      headers,
      body: JSON.stringify(req),
    }),
  );
}

export interface SciverseAgenticSearchResult {
  hits: Record<string, unknown>[];
}

export async function searchSciverseAgentic(
  req: { query: string; topK?: number; subQueries?: number },
  sciverse?: SciverseRequestOptions,
): Promise<SciverseAgenticSearchResult> {
  const headers = applySciverseHeaders({ "Content-Type": "application/json" }, sciverse);
  return handle<SciverseAgenticSearchResult>(
    await doFetch(`${BASE}/sciverse/agentic-search`, {
      method: "POST",
      headers,
      body: JSON.stringify(req),
    }),
  );
}

export type SciverseFetchContentResult = components["schemas"]["SciverseFetchContentResult"];

export async function fetchSciverseContent(
  pid: number,
  paperId: number,
  req: { docId?: string; maxChars?: number } = {},
  sciverse?: SciverseRequestOptions,
): Promise<SciverseFetchContentResult> {
  const headers = applySciverseHeaders({ "Content-Type": "application/json" }, sciverse);
  return handle<SciverseFetchContentResult>(
    await doFetch(`${BASE}/projects/${pid}/papers/${paperId}/sciverse/content`, {
      method: "POST",
      headers,
      body: JSON.stringify(req),
    }),
  );
}
export async function getCite(p: string, c: string, style: "gbt7714" | "apa" | "mla"): Promise<CiteResult> {
  return handle<CiteResult>(await doFetch(`${BASE}/projects/${enc(p)}/corpus/${enc(c)}/cite?style=${style}`));
}

// ============================================================
// W1: 文献库统计端点 (Task 5)
// ============================================================

export type LibraryStats = components["schemas"]["LibraryStats"];
export type ProjectLibraryStats = components["schemas"]["ProjectLibraryStats"];
export type OcrBreakdown = components["schemas"]["OcrBreakdown"];
export type InclusionBreakdown = components["schemas"]["InclusionBreakdown"];

export async function getLibraryStats(): Promise<LibraryStats> {
  return handle<LibraryStats>(await doFetch(`${BASE}/library/stats`));
}

export async function getProjectLibraryStats(pid: number): Promise<ProjectLibraryStats> {
  return handle<ProjectLibraryStats>(await doFetch(`${BASE}/projects/${pid}/library/stats`));
}

// ============================================================
// 项目管理 & SLR Agent 端点类型与函数 (P1-9)
// ============================================================

export interface Project {
  id: number;
  name: string;
  createdAt: string;
}

/**
 * M2: 项目当前 active corpus 摘要。
 * corpusId   — Postgres DB corpus.id（整数），物化/stale 重算时使用。
 * rCorpusId  — R 字符串 ID，调分析端点时透传；status != ready 时为 null。
 * stale      — 当前 included 集合与本 corpus 的 contentHash 不同 → 需重算。
 */
export interface ActiveCorpus {
  corpusId: number;
  rCorpusId: string | null;
  status: "parsing" | "ready" | "failed";
  documentCount: number;
  contentHash: string;
  stale: boolean;
}

export interface ProjectDetail {
  id: number;
  name: string;
  researchQuestion?: string;
  description?: string;
  paperCount: number;
  includedCount: number;
  /** M2: 项目当前 active corpus（最新 ready corpus；无则 null） */
  activeCorpus?: ActiveCorpus | null;
}

/**
 * M2: POST /projects/{pid}/corpus/materialize 响应体。
 * corpusId/rCorpusId 同 ActiveCorpus；rCorpusId 在 parsing/failed 时为 null。
 */
export interface CorpusMaterializeResult {
  corpusId: number;
  rCorpusId: string | null;
  status: "parsing" | "ready" | "failed";
  documentCount: number;
  contentHash: string;
}

export type InclusionStatus = "candidate" | "included" | "excluded" | "maybe";

export type ProjectPaperItem = components["schemas"]["ProjectPaperItem"];

// 作者既可能是纯字符串, 也可能是 CSL-JSON 对象 ({literal} 或 {family,given})
export type Creator = string | { family?: string; given?: string; literal?: string };

// PaperExtractionDto 和 PaperDetail 使用生成类型（消除手写漂移，B-fix）
export type PaperExtractionDto = components["schemas"]["PaperExtractionDto"];
export type PaperDetail = components["schemas"]["PaperDetail"];

export interface RunRef {
  runId: string;
  projectId: number;
  status: string;
}

export interface RunDetail {
  runId: string;
  status: string;
  roundsLog?: unknown[];
  finalOutput?: string;
  evidenceRefs?: unknown[];
}

// M2: 物化语料端点 — 从项目 included 论文构建 R 分析语料（幂等）
export async function materializeCorpus(pid: number): Promise<CorpusMaterializeResult> {
  return handle<CorpusMaterializeResult>(
    await doFetch(`${BASE}/projects/${pid}/corpus/materialize`, { method: "POST" }),
  );
}

export async function listProjects(): Promise<{ projects: Project[] }> {
  return handle<{ projects: Project[] }>(await doFetch(`${BASE}/projects`));
}

export async function createProject(body: {
  name: string;
  researchQuestion?: string;
  description?: string;
}): Promise<Project> {
  return handle<Project>(
    await doFetch(`${BASE}/projects`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function getProject(pid: number): Promise<ProjectDetail> {
  return handle<ProjectDetail>(await doFetch(`${BASE}/projects/${pid}`));
}

export async function getProjectPapers(pid: number): Promise<{ papers: ProjectPaperItem[] }> {
  return handle<{ papers: ProjectPaperItem[] }>(await doFetch(`${BASE}/projects/${pid}/papers`));
}

export async function getPaperDetail(pid: number, paperId: number): Promise<PaperDetail> {
  return handle<PaperDetail>(await doFetch(`${BASE}/projects/${pid}/papers/${paperId}`));
}

export async function patchInclusion(
  pid: number,
  paperId: number,
  body: { inclusionStatus: InclusionStatus; exclusionReason?: string; screeningScore?: number },
): Promise<ProjectPaperItem> {
  return handle<ProjectPaperItem>(
    await doFetch(`${BASE}/projects/${pid}/papers/${paperId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

// ---- M1: 文献导入端点 ----
export interface ImportResult {
  imported: number;
  skipped: number;
  failed: Array<{ name: string; reason: string }>;
  paperIds: number[];
}

export async function importPapers(
  pid: number,
  files: File[],
  defaultStatus: "candidate" | "included" | "excluded" | "maybe" = "candidate",
): Promise<ImportResult> {
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  fd.append("default_status", defaultStatus);
  return handle<ImportResult>(
    await doFetch(`${BASE}/projects/${pid}/papers/import`, { method: "POST", body: fd }),
  );
}

export async function listRuns(pid: number): Promise<{ runs: RunRef[] }> {
  return handle<{ runs: RunRef[] }>(await doFetch(`${BASE}/projects/${pid}/agent/runs`));
}

export async function createRun(
  pid: number,
  body: { prompt: string; autoConfirm?: boolean },
  llm?: LlmRequestOptions,
  sciverse?: SciverseRequestOptions,
): Promise<RunRef> {
  const headers = applySciverseHeaders(
    applyLlmHeaders({ "Content-Type": "application/json" }, llm),
    sciverse,
  );
  return handle<RunRef>(
    await doFetch(`${BASE}/projects/${pid}/agent/runs`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    }),
  );
}

export async function getRun(pid: number, rid: string): Promise<RunDetail> {
  return handle<RunDetail>(await doFetch(`${BASE}/projects/${pid}/agent/runs/${enc(rid)}`));
}

// ============================================================
// P2-4: 检索候选 + from-search 入库
// ============================================================

/**
 * 来自 Agent SearchTool 的单条候选文献，字段与后端 SearchTool emit 事件一致。
 * candidate_id 是前端勾选/去重用的本地 ID（openalexId 或 hash）。
 */
export interface SearchCandidate {
  candidate_id: string;
  openalexId?: string | null;
  title: string;
  authors?: string[];
  year?: number | null;
  doi?: string | null;
  containerTitle?: string | null;
  url?: string | null;
  publicationDate?: string | null;
  abstract?: string | null;
  keywords?: string | null;
  citedByCount?: number | null;
  source?: string | null;
  provider?: string | null;
  sciverseDocId?: string | null;
  sciverseUniqueId?: string | null;
  references?: string[];
  externalIds?: Record<string, unknown>[];
  raw?: Record<string, unknown> | null;
}

export type FromSearchResult = components["schemas"]["FromSearchResult"];
type FromSearchCandidatePayload = components["schemas"]["FromSearchCandidate"];

const FROM_SEARCH_LIMITS = {
  title: 1000,
  doi: 255,
  authors: 100,
  abstract: 20000,
  keywords: 4000,
  containerTitle: 1000,
  url: 2000,
  openalexId: 64,
  source: 40,
  provider: 40,
  sciverseDocId: 255,
  sciverseUniqueId: 255,
  references: 1000,
  externalIds: 20,
  rawBytes: 100000,
} as const;

function cleanString(value: unknown, maxLength: number): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  return trimmed.length > maxLength ? trimmed.slice(0, maxLength) : trimmed;
}

function cleanYear(value: unknown): number | undefined {
  if (typeof value !== "number" || !Number.isFinite(value)) return undefined;
  const year = Math.trunc(value);
  return year >= 1500 && year <= 2100 ? year : undefined;
}

function cleanObjectArray(value: unknown, maxLength: number): Record<string, unknown>[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const items = value
    .filter((item): item is Record<string, unknown> => !!item && typeof item === "object" && !Array.isArray(item))
    .slice(0, maxLength);
  return items.length > 0 ? items : undefined;
}

function cleanRaw(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  try {
    return JSON.stringify(value).length <= FROM_SEARCH_LIMITS.rawBytes
      ? (value as Record<string, unknown>)
      : undefined;
  } catch {
    return undefined;
  }
}

export function sanitizeSearchCandidateForImport(c: SearchCandidate): FromSearchCandidatePayload {
  const title = cleanString(c.title, FROM_SEARCH_LIMITS.title) ?? "Untitled";
  const authors = (Array.isArray(c.authors) ? c.authors : [])
    .map((a) => cleanString(a, 255))
    .filter((a): a is string => !!a)
    .slice(0, FROM_SEARCH_LIMITS.authors);

  return {
    title,
    doi: cleanString(c.doi, FROM_SEARCH_LIMITS.doi),
    authors: authors.length > 0 ? authors : undefined,
    year: cleanYear(c.year),
    abstract: cleanString(c.abstract, FROM_SEARCH_LIMITS.abstract),
    keywords: cleanString(c.keywords, FROM_SEARCH_LIMITS.keywords),
    containerTitle: cleanString(c.containerTitle, FROM_SEARCH_LIMITS.containerTitle),
    url: cleanString(c.url, FROM_SEARCH_LIMITS.url),
    openalexId: cleanString(c.openalexId, FROM_SEARCH_LIMITS.openalexId),
    source: cleanString(c.source, FROM_SEARCH_LIMITS.source),
    provider: cleanString(c.provider, FROM_SEARCH_LIMITS.provider),
    sciverseDocId: cleanString(c.sciverseDocId, FROM_SEARCH_LIMITS.sciverseDocId),
    sciverseUniqueId: cleanString(c.sciverseUniqueId, FROM_SEARCH_LIMITS.sciverseUniqueId),
    references: Array.isArray(c.references)
      ? c.references.map((r) => cleanString(r, 1000)).filter((r): r is string => !!r).slice(0, FROM_SEARCH_LIMITS.references)
      : undefined,
    externalIds: cleanObjectArray(c.externalIds, FROM_SEARCH_LIMITS.externalIds),
    raw: cleanRaw(c.raw),
  };
}

/**
 * POST /projects/{pid}/papers/from-search
 * 把选中候选批量入库。defaultStatus 控制 inclusion 状态。
 */
export async function addPapersFromSearch(
  pid: number,
  candidates: SearchCandidate[],
  defaultStatus: "candidate" | "included" = "candidate",
): Promise<FromSearchResult> {
  // 映射 SearchCandidate → FromSearchCandidate（schema 字段对齐）
  const mapped = candidates.map(sanitizeSearchCandidateForImport);
  return handle<FromSearchResult>(
    await doFetch(`${BASE}/projects/${pid}/papers/from-search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ candidates: mapped, defaultStatus }),
    }),
  );
}

// P3-T2: 元数据补全端点
export type BackfillMetadataResult = components["schemas"]["BackfillMetadataResult"];

export async function backfillMetadata(
  pid: number,
  opts: { limit?: number; onlyMissing?: boolean } = {},
): Promise<BackfillMetadataResult> {
  return handle<BackfillMetadataResult>(
    await doFetch(`${BASE}/projects/${pid}/papers/backfill-metadata`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ limit: opts.limit ?? 20, onlyMissing: opts.onlyMissing ?? true }),
    }),
  );
}

// P3-T3/T4: 结构化抽取端点
export type ExtractStructuredResult = components["schemas"]["ExtractStructuredResult"];

export async function extractStructured(
  pid: number,
  opts: { limit?: number; reextract?: boolean } = {},
): Promise<ExtractStructuredResult> {
  return handle<ExtractStructuredResult>(
    await doFetch(`${BASE}/projects/${pid}/papers/extract-structured`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ limit: opts.limit ?? 15, reextract: opts.reextract ?? false }),
    }),
  );
}

// P2-3: 写确认端点。awaiting_confirmation 时由 UI 调用 → 后端在同一条已打开的流上继续发后续事件。
export async function confirmRun(
  pid: number,
  rid: string,
  body: { toolCallId: string; decision: "approve" | "reject" },
): Promise<{ status: string }> {
  return handle<{ status: string }>(
    await doFetch(`${BASE}/projects/${pid}/agent/runs/${enc(rid)}/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

// Phase 5: 取消运行端点。运行中点「停止」时调用 → 后端终止 run，避免孤儿 run 继续烧 token。
export async function cancelRun(pid: number, rid: string): Promise<unknown> {
  return handle<unknown>(
    await doFetch(`${BASE}/projects/${pid}/agent/runs/${enc(rid)}/cancel`, {
      method: "POST",
    }),
  );
}

// P2-3: 可验证运行日志 (runlog/v1)。返回 RunLog JSON, 由 UI 序列化后触发浏览器下载。
// RunLog 体很大（messages/events/...），UI 只强类型 manifest（其余按需取），其它键宽松。
export interface RunLogManifest {
  event_count: number;
  tool_invocation_count: number;
  evidence_count: number;
  fabricated_count: number;
  chain_head: string;
  content_sha256: string;
}
export interface RunLog {
  schema_version: string;
  manifest: RunLogManifest;
  run?: { id: number; project_id: number; status: string; model_used?: string };
  [key: string]: unknown;
}
export async function getRunLog(pid: number, rid: string): Promise<RunLog> {
  return handle<RunLog>(await doFetch(`${BASE}/projects/${pid}/agent/runs/${enc(rid)}/runlog`));
}

// Phase 2: grounding 可信凭证摘要（TrustCard 数据源）。camelCase，三率可为 null（不可评分）。
export interface RunGroundingSummary {
  runId: number;
  status: string;
  modelUsed: string;
  createdAt: string | null;
  manifest: {
    eventCount: number;
    toolInvocationCount: number;
    evidenceCount: number;
    fabricatedCount: number;
    chainHead: string;
    contentSha256: string;
  };
  metrics: {
    groundingAccuracy: number | null;
    provenanceHitRate: number | null;
    zeroFabricationRate: number | null;
    insufficientEvidence: boolean;
    scoreable: boolean;
    evidenceCount: number;
    fabricatedCount: number;
    greenCount: number;
    yellowCount: number;
  };
  corpusHashCount: number;
  verifyHint: string;
}
export async function getGrounding(
  pid: number,
  rid: number | string,
): Promise<RunGroundingSummary> {
  return handle<RunGroundingSummary>(
    await doFetch(`${BASE}/projects/${pid}/agent/runs/${enc(String(rid))}/grounding`),
  );
}

// P3: MinerU 解析全文（Markdown）—— 文献详情按需展开拉取。
export interface PaperMarkdown {
  available: boolean;
  markdown: string;
  length: number;
  truncated: boolean;
  sha256: string | null;
}
export async function getPaperMarkdown(
  pid: number,
  paperId: number,
): Promise<PaperMarkdown> {
  return handle<PaperMarkdown>(
    await doFetch(`${BASE}/projects/${pid}/papers/${paperId}/markdown`),
  );
}

// 引用溯源链：文档结构（MinerU content_list 接入）。项目作用域路径（契约 §5.2）。
// 类型见 src/types/provenance.ts（契约 §1 唯一真相）；404 = 该 paper 无 OCR-done 附件。
export async function getStructure(
  pid: number,
  paperId: number,
): Promise<StructureResponse> {
  return handle<StructureResponse>(
    await doFetch(`${BASE}/projects/${pid}/papers/${paperId}/structure`),
  );
}

// 语料质检报告（F5）。项目作用域；by_type 为问题分类计数，issues 可点回链 paper。
export interface QualityIssue {
  paper_id: number;
  type: string; // missing_metadata | duplicate | not_parsed | ...
  detail: string;
}
export interface QualityReport {
  total: number;
  by_type: Record<string, number>;
  issues: QualityIssue[];
}
export async function getQualityReport(pid: number): Promise<QualityReport> {
  return handle<QualityReport>(await doFetch(`${BASE}/projects/${pid}/quality-report`));
}

// ============================================================
// M4: 工件 (Artifact) CRUD
// ============================================================

export interface ArtifactItem {
  id: number;
  projectId: number;
  runId?: number | null;
  type: string; // review|analysis|extraction|paperset
  title: string;
  sourceEventSeq?: number | null;
  contentRef?: string | null;
  pinned: boolean;
  userAnnotation?: string | null;
  order: number;
  createdAt?: string | null;
}

export interface ArtifactCreateBody {
  type?: string;
  title?: string;
  runId?: number | null;
  sourceEventSeq?: number | null;
  contentRef?: string | null;
  pinned?: boolean;
  userAnnotation?: string | null;
  order?: number;
}

export interface ArtifactPatchBody {
  title?: string;
  pinned?: boolean;
  userAnnotation?: string | null;
  order?: number;
}

export async function listArtifacts(
  pid: number,
  pinned?: boolean,
): Promise<{ artifacts: ArtifactItem[] }> {
  const q = pinned !== undefined ? `?pinned=${pinned}` : "";
  return handle<{ artifacts: ArtifactItem[] }>(
    await doFetch(`${BASE}/projects/${pid}/artifacts${q}`),
  );
}

export async function createArtifact(
  pid: number,
  body: ArtifactCreateBody,
): Promise<ArtifactItem> {
  return handle<ArtifactItem>(
    await doFetch(`${BASE}/projects/${pid}/artifacts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function patchArtifact(
  pid: number,
  aid: number,
  body: ArtifactPatchBody,
): Promise<ArtifactItem> {
  return handle<ArtifactItem>(
    await doFetch(`${BASE}/projects/${pid}/artifacts/${aid}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function deleteArtifact(pid: number, aid: number): Promise<void> {
  const res = await doFetch(`${BASE}/projects/${pid}/artifacts/${aid}`, { method: "DELETE" });
  if (!res.ok) {
    await handle(res);
  }
}

// ============================================================
// Agent Run SSE 流事件类型 (P1-10)
// ============================================================

export interface AgentRunStartEvent {
  type: "run_start";
  max_rounds: number;
  model: string;
  seq: number;
}

export interface AgentLlmStartEvent {
  type: "llm_start";
  round: number;
  is_final: boolean;
  context_tokens: number;
  seq: number;
}

export interface AgentToolCall {
  id: string;
  name: string;
  args_preview: string;
}

export interface AgentToolsStartEvent {
  type: "tools_start";
  round: number;
  thinking: string;
  tool_calls: AgentToolCall[];
  seq: number;
}

export interface AgentToolResult {
  tool_id: string;
  action: string;
  success: boolean;
  summary: string;
  data_source?: string;
  error?: string;
}

export interface AgentRoundCompleteEvent {
  type: "round_complete";
  round: number;
  thinking: string;
  tool_calls: AgentToolCall[];
  tool_results: AgentToolResult[];
  is_final: boolean;
  seq: number;
}

export interface AgentRunCompleteEvent {
  type: "run_complete";
  status: string;
  final_output: string;
  seq: number;
}

export interface AgentErrorEvent {
  type: "error";
  error: string;
  seq: number;
}

// P3-1: 运行生命周期事件。paused/resumed 为非终态信息事件（SSE 不据此关流）；
// cancelled 为终态（SSE 收到即关流）。
export interface AgentPausedEvent {
  type: "paused";
  status: string;
  seq: number;
}

export interface AgentResumedEvent {
  type: "resumed";
  status: string;
  seq: number;
}

export interface AgentCancelledEvent {
  type: "cancelled";
  status: string;
  seq: number;
}

// P2-3: 写工具需确认时发出此信号。非终态——SSE 流保持打开, 等用户批准/拒绝。
export interface AgentToolConfirmRequiredEvent {
  type: "tool_confirm_required";
  toolCallId: string;
  toolId: string;
  action: string;
  argsPreview: string;
  seq: number;
}

// P2-4: SearchTool emit 的检索候选事件。非终态——SSE 流继续。
export interface AgentSearchResultsEvent {
  type: "search_results";
  candidates: SearchCandidate[];
  query: string;
  seq: number;
}

export type AgentSseEvent =
  | AgentRunStartEvent
  | AgentLlmStartEvent
  | AgentToolsStartEvent
  | AgentRoundCompleteEvent
  | AgentRunCompleteEvent
  | AgentErrorEvent
  | AgentPausedEvent
  | AgentResumedEvent
  | AgentCancelledEvent
  | AgentToolConfirmRequiredEvent
  | AgentSearchResultsEvent;

export interface AgentRunHandlers {
  onRunStart?: (d: AgentRunStartEvent) => void;
  onLlmStart?: (d: AgentLlmStartEvent) => void;
  onToolsStart?: (d: AgentToolsStartEvent) => void;
  onRoundComplete?: (d: AgentRoundCompleteEvent) => void;
  onRunComplete?: (d: AgentRunCompleteEvent) => void;
  onError?: (d: AgentErrorEvent) => void;
  onPaused?: (d: AgentPausedEvent) => void;
  onResumed?: (d: AgentResumedEvent) => void;
  onCancelled?: (d: AgentCancelledEvent) => void;
  onToolConfirmRequired?: (d: AgentToolConfirmRequiredEvent) => void;
  /** P2-4: 收到检索候选事件（非终态，流继续）*/
  onSearchResults?: (d: AgentSearchResultsEvent) => void;
}

export async function streamAgentRun(
  pid: number,
  rid: string,
  opts: { lastEventId?: number; signal?: AbortSignal },
  handlers: AgentRunHandlers,
): Promise<void> {
  const headers: Record<string, string> = {};
  if (opts.lastEventId !== undefined) {
    headers["Last-Event-ID"] = String(opts.lastEventId);
  }
  let res: Response;
  try {
    res = await fetch(`${BASE}/projects/${pid}/agent/runs/${enc(rid)}/events`, {
      headers,
      signal: opts.signal,
    });
  } catch (e) {
    if (e instanceof Error && e.name === "AbortError") throw e;
    throw new ApiError("NETWORK_ERROR", 0, (e as Error).message || "网络错误");
  }

  // 修复2: 先检查 res.ok。非 2xx（如 404 RUN_NOT_FOUND）走 onError，不当 SSE 处理。
  if (!res.ok) {
    let body: { code?: string; message?: string } = {};
    try {
      body = await res.json();
    } catch {
      /* 非 JSON 错误体 */
    }
    const errEvt: AgentErrorEvent = {
      type: "error",
      error: body.message ?? res.statusText,
      seq: -1,
    };
    handlers.onError?.(errEvt);
    return;
  }

  if (!res.body) {
    const errEvt: AgentErrorEvent = { type: "error", error: "响应无 body", seq: -1 };
    handlers.onError?.(errEvt);
    return;
  }

  // 修复2/3: 跟踪终态事件和最大 seq
  let receivedTerminal = false;
  let lastSeq = opts.lastEventId ?? -1;

  try {
    await consumeSse(res, (event, data) => {
      // 忽略心跳注释帧（consumeSse 已过滤无 data 的帧，此处防御性过滤空 event）
      if (!data) return;

      let parsed: AgentSseEvent;
      try {
        parsed = JSON.parse(data) as AgentSseEvent;
      } catch {
        return;
      }

      // 修复2: 用 data 里的 seq 记录最大 lastEventId，供潜在重连
      if (typeof parsed.seq === "number" && parsed.seq > lastSeq) {
        lastSeq = parsed.seq;
      }

      switch (event) {
        case "run_start":
          handlers.onRunStart?.(parsed as AgentRunStartEvent);
          break;
        case "llm_start":
          handlers.onLlmStart?.(parsed as AgentLlmStartEvent);
          break;
        case "tools_start":
          handlers.onToolsStart?.(parsed as AgentToolsStartEvent);
          break;
        case "round_complete":
          handlers.onRoundComplete?.(parsed as AgentRoundCompleteEvent);
          break;
        case "run_complete":
          receivedTerminal = true;
          handlers.onRunComplete?.(parsed as AgentRunCompleteEvent);
          break;
        case "error":
          receivedTerminal = true;
          handlers.onError?.(parsed as AgentErrorEvent);
          break;
        // P3-1: paused/resumed 非终态（流保持打开，等 resume）；cancelled 终态（关流）。
        case "paused":
          handlers.onPaused?.(parsed as AgentPausedEvent);
          break;
        case "resumed":
          handlers.onResumed?.(parsed as AgentResumedEvent);
          break;
        case "cancelled":
          receivedTerminal = true;
          handlers.onCancelled?.(parsed as AgentCancelledEvent);
          break;
        // P2-3: 写确认信号为非终态——不改 receivedTerminal, 流保持打开等待 confirm。
        case "tool_confirm_required":
          handlers.onToolConfirmRequired?.(parsed as AgentToolConfirmRequiredEvent);
          break;
        // P2-4: 检索候选事件——非终态，流继续；候选渲染在 AgentChat 侧处理。
        case "search_results":
          handlers.onSearchResults?.(parsed as AgentSearchResultsEvent);
          break;
      }
    });
  } catch (e) {
    if (e instanceof ApiError) throw e;
    if (e instanceof Error && e.name === "AbortError") throw e;
    throw new ApiError("STREAM_ERROR", 0, (e as Error)?.message || "流中断");
  }

  // 修复3: 流结束但从未收到终态事件 → 通知 UI 连接中断
  if (!receivedTerminal) {
    const errEvt: AgentErrorEvent = {
      type: "error",
      error: "连接中断，运行可能未完成",
      seq: lastSeq + 1,
    };
    handlers.onError?.(errEvt);
  }
}

// ============================================================

// 通用 SSE 帧读取 (chat/review 共用)
// onFrame(event, data, id?) — id 由 "id:" 行解析，保持现有调用兼容
async function consumeSse(res: Response, onFrame: (event: string, data: string, id?: string) => void): Promise<void> {
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      let event = "message";
      let frameId: string | undefined;
      const dl: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dl.push(line.slice(5).trim());
        else if (line.startsWith("id:")) frameId = line.slice(3).trim();
      }
      if (dl.length) onFrame(event, dl.join("\n"), frameId);
    }
  }
}

export async function streamChat(
  projectId: string,
  corpusId: string,
  req: { query: string; history: ChatMessage[] },
  opts: LlmRequestOptions,
  handlers: { onToken?: (t: string) => void; onDone?: () => void; onError?: (d: { code: string; message: string }) => void },
): Promise<void> {
  const headers = applyLlmHeaders({ "Content-Type": "application/json" }, opts);
  const res = await doFetch(
    `${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/ai/chat`,
    { method: "POST", headers, body: JSON.stringify(req) },
  );
  if (!res.ok || !res.body) {
    await handle(res);
    return;
  }
  let captured: { code: string; message: string } | null = null;
  try {
    await consumeSse(res, (event, data) => {
      let parsed: Record<string, unknown> = {};
      try {
        parsed = JSON.parse(data);
      } catch {
        parsed = {};
      }
      if (event === "token") handlers.onToken?.((parsed as { text: string }).text ?? "");
      else if (event === "done") handlers.onDone?.();
      else if (event === "error") {
        captured = parsed as { code: string; message: string };
        handlers.onError?.(captured);
      }
    });
  } catch (e) {
    if (e instanceof ApiError) throw e;
    throw new ApiError("STREAM_ERROR", 0, (e as Error)?.message || "流中断");
  }
  if (captured) throw new ApiError((captured as { code: string }).code, 0, (captured as { message: string }).message);
}

function dispatchSse(event: string, data: string, h: ReviewHandlers) {
  let parsed: unknown = {};
  try {
    parsed = JSON.parse(data);
  } catch {
    parsed = {};
  }
  switch (event) {
    case "meta": h.onMeta?.(parsed as never); break;
    case "chapter": h.onChapter?.(parsed as never); break;
    case "token": h.onToken?.((parsed as { text: string }).text ?? ""); break;
    case "citations": h.onCitations?.(parsed as never); break;
    case "done": h.onDone?.(parsed as never); break;
    case "error": h.onError?.(parsed as never); break;
  }
}

export async function streamReview(
  projectId: string,
  corpusId: string,
  req: { type: string; topic: string },
  opts: LlmRequestOptions & { signal?: AbortSignal },
  handlers: ReviewHandlers,
): Promise<void> {
  const headers = applyLlmHeaders({ "Content-Type": "application/json" }, opts);
  const res = await doFetch(
    `${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/review`,
    { method: "POST", headers, body: JSON.stringify(req), signal: opts.signal },
  );
  if (!res.ok || !res.body) {
    await handle(res); // 非 200 → 抛 ApiError
    return;
  }
  // 捕获 error 事件: 仍回调给 UI, 但流结束后让 streamReview reject (Codex slice2-P2)
  let captured: { code: string; message: string } | null = null;
  const wrapped: ReviewHandlers = {
    ...handlers,
    onError: (d) => {
      captured = d;
      handlers.onError?.(d);
    },
  };
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      let event = "message";
      const dataLines: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (dataLines.length) dispatchSse(event, dataLines.join("\n"), wrapped);
    }
  }
  if (captured) {
    throw new ApiError((captured as { code: string }).code, 0, (captured as { message: string }).message);
  }
}

// ============================================================
// 研究副驾 · GAP 发现 + 价值核验（HITL）
// 异步：discover/verify 返回 run id；前端轮询 scratchpad / 拉 verdict。
// 路径含 Google AIP 风格自定义方法（:discover / :verify），逐字照契约。
// ============================================================

/** POST :discover — 启动 GAP 发现 run（异步，202 → run_id）。 */
export async function discoverGaps(pid: number, cid: string): Promise<GapDiscoverAccepted> {
  return handle<GapDiscoverAccepted>(
    await doFetch(`${BASE}/projects/${pid}/corpus/${enc(cid)}/gaps:discover`, { method: "POST" }),
  );
}

/** GET scratchpad — 拉取本 run 实时工作记忆快照（HITL 轮询视图）。 */
export async function getScratchpad(pid: number, rid: string): Promise<ScratchpadState> {
  return handle<ScratchpadState>(
    await doFetch(`${BASE}/projects/${pid}/agent/runs/${enc(rid)}/scratchpad`),
  );
}

/** POST :verify — 启动该 GAP 价值核验（异步，202 → verify_run_id）。 */
export async function verifyGap(pid: number, gapId: string): Promise<GapVerifyAccepted> {
  return handle<GapVerifyAccepted>(
    await doFetch(`${BASE}/projects/${pid}/gaps/${enc(gapId)}:verify`, { method: "POST" }),
  );
}

/** GET verdict — 取价值裁决 + 证据包（裁决 decided_by 恒为 deterministic）。 */
export async function getGapVerdict(pid: number, gapId: string): Promise<GapVerdictResult> {
  return handle<GapVerdictResult>(
    await doFetch(`${BASE}/projects/${pid}/gaps/${enc(gapId)}/verdict`),
  );
}

/** PATCH gap — HITL accept/reject/revise（返回更新后的 GapCandidate）。 */
export async function patchGap(
  pid: number,
  gapId: string,
  body: GapPatchRequest,
): Promise<GapCandidate> {
  return handle<GapCandidate>(
    await doFetch(`${BASE}/projects/${pid}/gaps/${enc(gapId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}
