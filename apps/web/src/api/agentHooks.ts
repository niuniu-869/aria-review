// react-query v5 hooks for project management & SLR agent (P1-9)
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addPapersFromSearch,
  backfillMetadata,
  createArtifact,
  createProject,
  createRun,
  deleteArtifact,
  extractStructured,
  getPaperDetail,
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
  discoverGaps,
  getScratchpad,
  verifyGap,
  getGapVerdict,
  patchGap,
} from "./client";
import type {
  GapCandidate,
  GapDiscoverAccepted,
  GapPatchRequest,
  GapVerdictResult,
  GapVerifyAccepted,
  ScratchpadState,
} from "../types/research";
import type {
  ActiveCorpus,
  ArtifactCreateBody,
  ArtifactItem,
  ArtifactPatchBody,
  BackfillMetadataResult,
  CorpusMaterializeResult,
  ExtractStructuredResult,
  FromSearchResult,
  ImportResult,
  InclusionStatus,
  LibraryStats,
  ProjectLibraryStats,
  SearchCandidate,
} from "./client";

// 重新导出工件类型，供页面层直接使用
export type { ArtifactItem };

// 重新导出，供页面层直接使用（避免从 client 多层引入）
export type { ActiveCorpus, CorpusMaterializeResult, LibraryStats, ProjectLibraryStats };


// ---- query hooks ----

export function useProjects() {
  return useQuery({
    queryKey: ["projects"],
    queryFn: listProjects,
  });
}

export function useProject(pid: number) {
  return useQuery({
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
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["projectPapers", pid] });
      // 纳排变化会影响 included/candidate/excluded 计数 — 刷新库统计
      void qc.invalidateQueries({ queryKey: ["projectLibraryStats", pid] });
      void qc.invalidateQueries({ queryKey: ["globalLibraryStats"] });
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

// M2: corpus 物化 mutation hook
// 成功后使 projectDetail 缓存失效，触发 activeCorpus 刷新
export function useMaterializeCorpus(pid: number) {
  const qc = useQueryClient();
  return useMutation<CorpusMaterializeResult, Error, void>({
    mutationFn: () => materializeCorpus(pid),
    onSuccess: () => {
      // 失效 project 详情缓存，使 activeCorpus 从服务端重新读取
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
  return useMutation<GapDiscoverAccepted, Error, { cid: string }>({
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
    // 契约级停轮询信号（codex B1-P1）：run_status 进入终态（completed/failed）即停，
    // 不再依赖调用方手动把 live 置 false。首拉(undefined)/running 时继续按 pollMs 轮询。
    refetchInterval: (query) => {
      if (!live) return false;
      const status = query.state.data?.run_status;
      return status === "completed" || status === "failed" ? false : pollMs;
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
  opts?: { enabled?: boolean; poll?: boolean },
) {
  return useQuery<GapVerdictResult, Error>({
    queryKey: ["gapVerdict", pid, gapId],
    queryFn: () => getGapVerdict(pid, gapId as string),
    enabled: pid > 0 && !!gapId && (opts?.enabled ?? true),
    retry: false,
    // poll: 裁决异步产出，拿到数据前（含 verify 后短暂 404）按 2s 轮询；拿到即停（codex B5-P2）。
    refetchInterval: opts?.poll ? (query) => (query.state.data ? false : 2000) : false,
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
