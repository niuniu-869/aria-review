// react-query v5 hooks for project management & SLR agent (P1-9)
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addPapersFromSearch,
  ApiError,
  backfillFulltext,
  backfillMetadata,
  createArtifact,
  createProject,
  createRun,
  deleteArtifact,
  deleteProject,
  extractStructured,
  getPaperDetail,
  getAiJob,
  getProject,
  getProjectPapers,
  getProjectLibraryStats,
  getLibraryStats,
  getRun,
  importPapers,
  listAiJobs,
  listArtifacts,
  listProjects,
  listRuns,
  materializeCorpus,
  patchArtifact,
  patchInclusion,
  renameProject,
  discoverGaps,
  getScratchpad,
  verifyGap,
  getGapVerdict,
  verifyGapFeasibility,
  getGapFeasibilityVerdict,
  patchGap,
} from "./client";
import { isTerminalScratchpadRunStatus } from "./runStatus";
import { asRCorpusId } from "./corpusIds";
import type {
  GapCandidate,
  GapDiscoverAccepted,
  GapFeasibilityAccepted,
  GapFeasibilityVerdictResult,
  GapPatchRequest,
  GapVerdictResult,
  GapVerifyAccepted,
  ScratchpadState,
} from "../types/research";
import type {
  ArtifactCreateBody,
  ArtifactItem,
  ArtifactPatchBody,
  BackfillMetadataResult,
  CorpusMaterializeResult,
  ExtractStructuredResult,
  FulltextBackfillResult,
  FromSearchResult,
  ImportResult,
  InclusionStatus,
  LibraryStats,
  ProjectLibraryStats,
  SearchCandidate,
  SciverseRequestOptions,
  AiJob,
} from "./client";
import type { components } from "./schema";
import type { BrandCorpusIdFields, RCorpusId } from "./corpusIds";

// 重新导出工件类型，供页面层直接使用
export type { ArtifactItem };

export type ActiveCorpus = BrandCorpusIdFields<components["schemas"]["ActiveCorpusDetail"]>;
export type LatestCorpus = BrandCorpusIdFields<components["schemas"]["LatestCorpusDetail"]>;
export type ProjectDetail = Omit<components["schemas"]["ProjectDetail"], "activeCorpus" | "latestCorpus"> & {
  activeCorpus?: ActiveCorpus | null;
  latestCorpus?: LatestCorpus | null;
};

// 重新导出，供页面层直接使用（避免从 client 多层引入）
export type { CorpusMaterializeResult, LibraryStats, ProjectLibraryStats };

/** activeCorpus → R corpus id 的唯一解析出口。 */
export function getActiveRCorpusId(activeCorpus: ActiveCorpus | null | undefined): RCorpusId | null {
  if (activeCorpus?.status !== "ready" || !activeCorpus.rCorpusId) return null;
  return asRCorpusId(activeCorpus.rCorpusId);
}

/** 部分无语料面板仍沿用空字符串占位；品牌化集中在这里，避免调用点散落 as。 */
export function getPanelRCorpusId(activeCorpus: ActiveCorpus | null | undefined): RCorpusId {
  return getActiveRCorpusId(activeCorpus) ?? asRCorpusId("");
}


// ---- query hooks ----

export function useProjects(enabled = true) {
  return useQuery({
    queryKey: ["projects"],
    queryFn: listProjects,
    enabled,
  });
}

export function useProject(pid: number) {
  return useQuery<ProjectDetail>({
    queryKey: ["project", pid],
    queryFn: () => getProject(pid),
    enabled: pid > 0,
  });
}

export function useProjectPapers(pid: number) {
  return useQuery({
    queryKey: ["projectPapers", pid],
    queryFn: () => getProjectPapers(pid),
    enabled: pid > 0,
  });
}

export function usePaper(pid: number, paperId: number) {
  return useQuery({
    queryKey: ["paper", pid, paperId],
    queryFn: () => getPaperDetail(pid, paperId),
    enabled: pid > 0 && paperId > 0,
  });
}

export function useRuns(pid: number) {
  return useQuery({
    queryKey: ["runs", pid],
    queryFn: () => listRuns(pid),
    enabled: pid > 0,
  });
}

// ---- W1: 文献库统计 hooks ----

/** 项目作用域的文献库统计（Task 5） */
export function useProjectLibraryStats(pid: number) {
  return useQuery<ProjectLibraryStats>({
    queryKey: ["projectLibraryStats", pid],
    queryFn: () => getProjectLibraryStats(pid),
    enabled: pid > 0,
    staleTime: 30_000, // 30 秒内无需重拉
  });
}

/** 全局共享库统计（Task 5）
 *  staleTime 5 分钟 — 全局库变化频率低，切项目时避免每次重拉。
 */
export function useGlobalLibraryStats() {
  return useQuery<LibraryStats>({
    queryKey: ["globalLibraryStats"],
    queryFn: getLibraryStats,
    staleTime: 5 * 60 * 1000,
  });
}

// ---- mutation hooks ----

export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: createProject,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["projects"] });
    },
  });
}

/** 项目改名（qa-20260717 F-15）。 */
export function useRenameProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ pid, name }: { pid: number; name: string }) => renameProject(pid, name),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["projects"] });
    },
  });
}

/** 项目删除（qa-20260717 F-15）。 */
export function useDeleteProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (pid: number) => deleteProject(pid),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["projects"] });
    },
  });
}

export function usePatchInclusion(pid: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      paperId,
      inclusionStatus,
      exclusionReason,
      screeningScore,
    }: {
      paperId: number;
      inclusionStatus: InclusionStatus;
      exclusionReason?: string;
      screeningScore?: number;
    }) => patchInclusion(pid, paperId, { inclusionStatus, exclusionReason, screeningScore }),
    onSuccess: (_data, variables) => {
      void qc.invalidateQueries({ queryKey: ["projectPapers", pid] });
      // 纳排变化会影响 included/candidate/excluded 计数 — 刷新库统计
      void qc.invalidateQueries({ queryKey: ["projectLibraryStats", pid] });
      void qc.invalidateQueries({ queryKey: ["globalLibraryStats"] });
      void qc.invalidateQueries({ queryKey: ["project", pid] });
      void qc.invalidateQueries({ queryKey: ["paper", pid, variables.paperId] });
    },
  });
}

export function useImportPapers(pid: number) {
  const qc = useQueryClient();
  return useMutation<
    ImportResult,
    Error,
    { files: File[]; defaultStatus?: "candidate" | "included" | "excluded" | "maybe" }
  >({
    mutationFn: ({ files, defaultStatus }) => importPapers(pid, files, defaultStatus),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["projectPapers", pid] });
      // 入库后全局总量与项目文献数量均变化 — 刷新库统计
      void qc.invalidateQueries({ queryKey: ["projectLibraryStats", pid] });
      void qc.invalidateQueries({ queryKey: ["globalLibraryStats"] });
      void qc.invalidateQueries({ queryKey: ["project", pid] });
    },
  });
}

export function useCreateRun(pid: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { prompt: string; autoConfirm?: boolean }) => createRun(pid, body),
    onSuccess: () => {
      // 只失效 run 列表（run 刚启动，统计/项目状态尚未变化）。
      // 统计失效（projectLibraryStats / globalLibraryStats / project）在 SSE run_complete
      // 真正完成后由 ChatWorkbench.handleRunComplete 触发，避免开始就刷新读到旧值。
      void qc.invalidateQueries({ queryKey: ["runs", pid] });
    },
  });
}

export function useRun(pid: number, rid: string) {
  return useQuery({
    queryKey: ["run", pid, rid],
    queryFn: () => getRun(pid, rid),
    enabled: pid > 0 && !!rid,
  });
}

function isTerminalAiJobStatus(status: AiJob["status"] | undefined): boolean {
  return status === "done" || status === "failed" || status === "cancelled";
}

export function useAiJob(
  pid: number,
  jobId: number | null,
  opts?: { enabled?: boolean; pollMs?: number },
) {
  const pollMs = opts?.pollMs ?? 4000;
  return useQuery<AiJob, Error>({
    queryKey: ["aiJob", pid, jobId],
    queryFn: () => getAiJob(String(pid), jobId as number),
    enabled: pid > 0 && jobId != null && (opts?.enabled ?? true),
    refetchInterval: (query) => (
      isTerminalAiJobStatus(query.state.data?.status) ? false : pollMs
    ),
  });
}

// M2: corpus 物化 mutation hook
// 完成后使 projectDetail 缓存失效，触发 activeCorpus/latestCorpus 刷新
export function useMaterializeCorpus(pid: number) {
  const qc = useQueryClient();
  return useMutation<CorpusMaterializeResult, Error, void>({
    mutationFn: () => materializeCorpus(pid),
    onSettled: () => {
      // 同步端点失败时后端也可能写入 latestCorpus.failed，需要重拉项目详情展示原因。
      void qc.invalidateQueries({ queryKey: ["project", pid] });
      // 语料就绪状态变化也需要反映在库统计条
      void qc.invalidateQueries({ queryKey: ["projectLibraryStats", pid] });
    },
  });
}

// ---- M4: 工件 hooks ----

/** 列出项目工件，可按 pinned 过滤 */
export function useArtifacts(pid: number, pinned?: boolean) {
  return useQuery({
    queryKey: ["artifacts", pid, pinned],
    queryFn: () => listArtifacts(pid, pinned),
    enabled: pid > 0,
  });
}

/** 创建工件 */
export function useCreateArtifact(pid: number) {
  const qc = useQueryClient();
  return useMutation<ArtifactItem, Error, ArtifactCreateBody>({
    mutationFn: (body) => createArtifact(pid, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["artifacts", pid] });
    },
  });
}

/** 更新工件（title/pinned/annotation/order） */
export function usePatchArtifact(pid: number) {
  const qc = useQueryClient();
  return useMutation<ArtifactItem, Error, { aid: number } & ArtifactPatchBody>({
    mutationFn: ({ aid, ...body }) => patchArtifact(pid, aid, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["artifacts", pid] });
    },
  });
}

/** 删除工件 */
export function useDeleteArtifact(pid: number) {
  const qc = useQueryClient();
  return useMutation<void, Error, number>({
    mutationFn: (aid) => deleteArtifact(pid, aid),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["artifacts", pid] });
    },
  });
}

// ---- P3-T2: AI 元数据补全 hook ----

/**
 * useBackfillMetadata — 对项目内缺元数据的文献批量 LLM 补全。
 * onSuccess 失效论文列表、库统计（completed 数量可能变化）、项目详情。
 */
export function useBackfillMetadata(pid: number) {
  const qc = useQueryClient();
  return useMutation<BackfillMetadataResult, Error, { limit?: number; onlyMissing?: boolean }>({
    mutationFn: (opts) => backfillMetadata(pid, opts),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["projectPapers", pid] });
      void qc.invalidateQueries({ queryKey: ["projectLibraryStats", pid] });
      void qc.invalidateQueries({ queryKey: ["globalLibraryStats"] });
      void qc.invalidateQueries({ queryKey: ["project", pid] });
    },
  });
}

export function useBackfillFulltext(pid: number) {
  const qc = useQueryClient();
  return useMutation<
    FulltextBackfillResult,
    Error,
    { paperIds?: number[] | null; maxPapers?: number; excludePaperIds?: number[]; sciverse?: SciverseRequestOptions }
  >({
    mutationFn: ({ paperIds, maxPapers, excludePaperIds, sciverse }) =>
      backfillFulltext(pid, { paperIds, maxPapers, excludePaperIds }, sciverse),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["projectPapers", pid] });
      void qc.invalidateQueries({ queryKey: ["projectLibraryStats", pid] });
      void qc.invalidateQueries({ queryKey: ["globalLibraryStats"] });
      void qc.invalidateQueries({ queryKey: ["project", pid] });
      void qc.invalidateQueries({ queryKey: ["paper", pid] });
    },
  });
}

// ---- P3-T4: 结构化抽取 hook ----

/**
 * useExtractStructured — 对项目内 OCR-done 文献批量 LLM 抽取结构化字段。
 * onSuccess 失效论文列表（hasExtraction 字段变化）、库统计、项目详情。
 */
export function useExtractStructured(pid: number) {
  const qc = useQueryClient();
  return useMutation<ExtractStructuredResult, Error, { limit?: number; reextract?: boolean }>({
    mutationFn: (opts) => extractStructured(pid, opts),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["projectPapers", pid] });
      void qc.invalidateQueries({ queryKey: ["projectLibraryStats", pid] });
      void qc.invalidateQueries({ queryKey: ["globalLibraryStats"] });
      void qc.invalidateQueries({ queryKey: ["project", pid] });
      // 失效 paper 级详情缓存（extraction 字段已变化）—— 用前缀匹配所有 paper 缓存
      void qc.invalidateQueries({ queryKey: ["paper", pid] });
    },
  });
}

// ---- P2-4: 检索候选入库 hook ----

/**
 * useAddFromSearch — 把 Agent 检索到的候选批量入库。
 * onSuccess 失效文献库相关缓存（论文列表、库统计、项目详情）。
 */
export function useAddFromSearch(pid: number) {
  const qc = useQueryClient();
  return useMutation<
    FromSearchResult,
    Error,
    { pid: number; candidates: SearchCandidate[]; defaultStatus: "candidate" | "included" }
  >({
    mutationFn: ({ pid: p, candidates, defaultStatus }) =>
      addPapersFromSearch(p, candidates, defaultStatus),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["projectPapers", pid] });
      void qc.invalidateQueries({ queryKey: ["projectLibraryStats", pid] });
      void qc.invalidateQueries({ queryKey: ["globalLibraryStats"] });
      void qc.invalidateQueries({ queryKey: ["project", pid] });
    },
  });
}

// ============================================================
// 研究副驾 hooks（GAP 发现 + 价值核验，HITL）
// discover/verify 为异步 mutation（返回 run id）；scratchpad 轮询；verdict 查询；
// PATCH 为人在环上裁决（不自动定稿）。
// ============================================================

/** 启动 GAP 发现 run（异步）。成功后调用方拿 run_id 去轮询 scratchpad。 */
export function useDiscoverGaps(pid: number) {
  return useMutation<GapDiscoverAccepted, Error, { cid: RCorpusId }>({
    mutationFn: ({ cid }) => discoverGaps(pid, cid),
  });
}

/**
 * 取本项目最近一次 gap_discover run（ResearchView 挂载/刷新时回填 GAP）。
 * dogfood A3-P1：此前 runId 仅存组件 state，刷新即丢→已发现的 GAP 全消失需重跑。
 * 复用已有 GET /projects/{pid}/ai/jobs?kind=gap_discover&limit=1（后端数据持久化完好）。
 */
export function useLatestGapDiscoverRun(pid: number) {
  return useQuery({
    queryKey: ["latestGapRun", pid],
    queryFn: () => listAiJobs(String(pid), { kind: "gap_discover", limit: 1 }),
    enabled: pid > 0,
    staleTime: 30_000,
  });
}

/**
 * 轮询本 run scratchpad（HITL 实时视图）。
 * - rid 为 null 时不发请求（enabled 守卫）。
 * - live!==false 时按 pollMs（默认 1500ms）轮询；调用方据 run 完成态把 live 置 false 停轮询，
 *   避免 run 结束后无谓空转（与 ChatWorkbench SSE 完成后停轮询同理）。
 */
export function useScratchpad(
  pid: number,
  rid: string | null,
  opts?: { pollMs?: number; live?: boolean },
) {
  const pollMs = opts?.pollMs ?? 1500;
  const live = opts?.live ?? true;
  return useQuery<ScratchpadState, Error>({
    queryKey: ["scratchpad", pid, rid],
    queryFn: () => getScratchpad(pid, rid as string),
    enabled: pid > 0 && !!rid,
    // 契约级停轮询信号（codex B1-P1）：run_status 进入终态（done/failed）即停，
    // 不再依赖调用方手动把 live 置 false。首拉(undefined)/running 时继续按 pollMs 轮询。
    refetchInterval: (query) => {
      if (!live) return false;
      const status = query.state.data?.run_status;
      return isTerminalScratchpadRunStatus(status) ? false : pollMs;
    },
  });
}

/** 启动该 GAP 价值核验（异步）。成功后失效该 GAP 的 verdict 缓存以触发重拉。 */
export function useVerifyGap(pid: number) {
  const qc = useQueryClient();
  return useMutation<GapVerifyAccepted, Error, { gapId: string }>({
    mutationFn: ({ gapId }) => verifyGap(pid, gapId),
    onSuccess: (_data, { gapId }) => {
      void qc.invalidateQueries({ queryKey: ["gapVerdict", pid, gapId] });
    },
  });
}

/**
 * 取某 GAP 的价值裁决 + 证据包。
 * retry:false — 裁决尚未产生时后端 404；不重试，由调用方据 ApiError.status===404 静默处理
 * （与 TrustCard 同款诚实策略：未核验不伪装裁决）。
 */
export function useGapVerdict(
  pid: number,
  gapId: string | null,
  opts?: { enabled?: boolean; poll?: boolean; pollMs?: number },
) {
  const pollMs = opts?.pollMs ?? 4000;
  return useQuery<GapVerdictResult, Error>({
    queryKey: ["gapVerdict", pid, gapId],
    queryFn: () => getGapVerdict(pid, gapId as string),
    enabled: pid > 0 && !!gapId && (opts?.enabled ?? true),
    retry: false,
    // poll: 裁决异步产出，拿到数据前（含 verify 后短暂 404）按 3-5s 轮询；拿到即停（codex B5-P2）。
    refetchInterval: opts?.poll ? (query) => (query.state.data ? false : pollMs) : false,
  });
}

/** 启动该 GAP 可行性核验；与价值核验完全独立。 */
export function useFeasibilityVerify(pid: number) {
  const qc = useQueryClient();
  return useMutation<GapFeasibilityAccepted, Error, { gapId: string }>({
    mutationFn: ({ gapId }) => verifyGapFeasibility(pid, gapId),
    onSuccess: (_data, { gapId }) => {
      void qc.invalidateQueries({ queryKey: ["gapFeasibilityVerdict", pid, gapId] });
    },
  });
}

/**
 * 取可行性裁决；启动任务后按 4 秒轮询，拿到裁决或 job 失败后由调用方关闭 poll。
 * F-20: 未核验的 409/兼容旧描述中的 404 不再以错误上抛（轮询期间刷屏），
 * 静默为 { pending: true } 哨兵；调用方按“未就绪”处理（只显示核验按钮）。
 */
export type FeasibilityVerdictData = GapFeasibilityVerdictResult | { pending: true };

/** 判断可行性裁决查询数据是否为「未就绪」哨兵。 */
export function isFeasibilityVerdictPending(
  data: FeasibilityVerdictData | undefined,
): data is { pending: true } {
  return !!data && "pending" in data && (data as { pending: unknown }).pending === true;
}

export function useFeasibilityVerdict(
  pid: number,
  gapId: string | null,
  opts?: { enabled?: boolean; poll?: boolean; pollMs?: number },
) {
  const pollMs = opts?.pollMs ?? 4000;
  return useQuery<FeasibilityVerdictData, Error>({
    queryKey: ["gapFeasibilityVerdict", pid, gapId],
    queryFn: async () => {
      try {
        return await getGapFeasibilityVerdict(pid, gapId as string);
      } catch (err) {
        if (err instanceof ApiError && (err.status === 409 || err.status === 404)) {
          return { pending: true };
        }
        throw err;
      }
    },
    enabled: pid > 0 && !!gapId && (opts?.enabled ?? true),
    retry: false,
    // pending 哨兵视为尚未拿到裁决，继续轮询；拿到真实裁决即停（codex B5-P2）
    refetchInterval: opts?.poll
      ? (query) => (query.state.data && !isFeasibilityVerdictPending(query.state.data) ? false : pollMs)
      : false,
  });
}

/**
 * HITL accept/reject/revise（调 PATCH，返回更新后的 GapCandidate）。
 * 成功后失效 scratchpad（状态/论断变化需反映到实时视图）。绝不自动定稿。
 */
export function usePatchGap(pid: number) {
  const qc = useQueryClient();
  return useMutation<GapCandidate, Error, { gapId: string } & GapPatchRequest>({
    // GapPatchRequest 为 oneOf 判别联合（codex B1-P2）：按 action 收窄重建 body，
    // revise 分支携带必填 statement，accept/reject 分支不带 → 契约层杜绝缺字段。
    mutationFn: (vars) => {
      const body: GapPatchRequest =
        vars.action === "revise"
          ? { action: "revise", statement: vars.statement }
          : { action: vars.action };
      return patchGap(pid, vars.gapId, body);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["scratchpad", pid] });
    },
  });
}
