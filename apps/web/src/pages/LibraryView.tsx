/**
 * LibraryView.tsx — 文献库三栏视图（M1）
 *
 * 布局：左栏筛选 + 中栏虚拟列表 + 右栏详情
 * 路由：/projects/:pid/library（Outlet 容器已由此组件直接渲染，
 *       原子路由 library/:paperId 现通过 URL 同步到右栏，不再独立页面）
 *
 * 说明：本组件直接实现三栏，不再只渲染 <Outlet />，
 *       因此 routes.tsx 的 library/:paperId 子路由仍保留兼容性
 *       但右栏内容由内部 selectedPaperId 状态控制，并与 URL 同步。
 */
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  useBackfillMetadata,
  useExtractStructured,
  useImportPapers,
  usePatchInclusion,
  useProject,
  useProjectPapers,
} from "../api/agentHooks";
import type { BackfillMetadataResult, ExtractStructuredResult, InclusionStatus, ProjectPaperItem } from "../api/client";
import { ErrMsg, Loading } from "../lib/ui";
import { ImportDialog } from "./library/ImportDialog";
import { LibFilterPanel } from "./library/LibFilterPanel";
import { LibPaperDetail } from "./library/LibPaperDetail";
import { LibPaperList } from "./library/LibPaperList";
import { ScreeningMode } from "./library/ScreeningMode";

/** 排序字段 */
export type SortField = "title" | "year" | "screeningScore";
export type SortDir = "asc" | "desc";

/** 筛选面板选中的状态过滤 */
export type StatusFilter = InclusionStatus | "all";

/** 已解析过滤（元索引雏形） */
export type ExtractionFilter = "all" | "extracted" | "not-extracted";

/** 过滤+排序后的列表 */
function applyFilter(
  papers: ProjectPaperItem[],
  search: string,
  status: StatusFilter,
  extractionFilter: ExtractionFilter,
  sortField: SortField,
  sortDir: SortDir,
): ProjectPaperItem[] {
  let list = papers;

  // 状态过滤
  if (status !== "all") {
    list = list.filter((p) => p.inclusionStatus === status);
  }

  // 已解析过滤（元索引雏形）
  if (extractionFilter === "extracted") {
    list = list.filter((p) => p.hasExtraction);
  } else if (extractionFilter === "not-extracted") {
    list = list.filter((p) => !p.hasExtraction);
  }

  // 搜索过滤（客户端，按标题）
  if (search.trim()) {
    const q = search.trim().toLowerCase();
    list = list.filter((p) => (p.title ?? "").toLowerCase().includes(q));
  }

  // 排序
  list = [...list].sort((a, b) => {
    let cmp = 0;
    if (sortField === "title") {
      cmp = (a.title ?? "").localeCompare(b.title ?? "", "zh");
    } else if (sortField === "year") {
      cmp = (a.year ?? 0) - (b.year ?? 0);
    } else if (sortField === "screeningScore") {
      cmp = (a.screeningScore ?? -1) - (b.screeningScore ?? -1);
    }
    return sortDir === "asc" ? cmp : -cmp;
  });

  return list;
}

/** 计算各状态计数 */
function countByStatus(papers: ProjectPaperItem[]) {
  const counts: Record<StatusFilter, number> = { all: 0, candidate: 0, included: 0, excluded: 0, maybe: 0 };
  for (const p of papers) {
    counts.all++;
    counts[p.inclusionStatus]++;
  }
  return counts;
}

export function LibraryView() {
  const { pid, paperId: paperIdParam } = useParams<{ pid: string; paperId?: string }>();
  const pidNum = Number(pid);
  const navigate = useNavigate();

  const { data, isLoading, error } = useProjectPapers(pidNum);
  const { data: project } = useProject(pidNum);
  const patch = usePatchInclusion(pidNum);
  const importMut = useImportPapers(pidNum);
  const backfillMut = useBackfillMetadata(pidNum);
  const extractMut = useExtractStructured(pidNum);

  // ---- AI 动作反馈状态 ----
  const [backfillResult, setBackfillResult] = useState<BackfillMetadataResult | null>(null);
  const [extractResult, setExtractResult] = useState<ExtractStructuredResult | null>(null);

  // ---- 筛选/排序状态 ----
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [extractionFilter, setExtractionFilter] = useState<ExtractionFilter>("all");
  const [sortField, setSortField] = useState<SortField>("year");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  // ---- 多选状态 ----
  const [selected, setSelected] = useState<Set<number>>(new Set());

  // ---- 选中详情的 paperId（URL 同步） ----
  const [selectedPaperId, setSelectedPaperId] = useState<number | null>(
    paperIdParam ? Number(paperIdParam) : null,
  );

  // ---- 筛选模式 ----
  const [screeningMode, setScreeningMode] = useState(false);
  const [screeningIndex, setScreeningIndex] = useState(0);
  // P1-4：进入筛选模式时冻结 paperId 队列快照，筛选全程基于此快照，
  // 避免 PATCH 后 filtered 列表重排/缩减导致 index+1 跳篇。
  const [screeningQueue, setScreeningQueue] = useState<number[]>([]);

  // ---- 导入弹层 ----
  const [showImport, setShowImport] = useState(false);

  // paperIdParam 变化时同步右栏（支持深链接）
  useEffect(() => {
    if (paperIdParam) setSelectedPaperId(Number(paperIdParam));
  }, [paperIdParam]);

  // 选中行 → 更新 URL
  const handleSelectRow = useCallback(
    (pid2: number) => {
      setSelectedPaperId(pid2);
      navigate(`/projects/${pid}/library/${pid2}`, { replace: true });
    },
    [pid, navigate],
  );

  // 切换排序
  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortField(field);
      setSortDir("asc");
    }
  };

  // 全选/取消
  const handleSelectAll = (filteredPapers: ProjectPaperItem[]) => {
    if (selected.size === filteredPapers.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(filteredPapers.map((p) => p.paperId)));
    }
  };

  // 批量设状态 — P2-a：只对「当前过滤列表 ∩ selected」执行，避免跨过滤误操作隐藏文献
  const handleBulkStatus = async (status: InclusionStatus, currentFiltered: ProjectPaperItem[]) => {
    // 取交集：selected 中且在当前过滤列表里的 id
    const filteredIds = new Set(currentFiltered.map((p) => p.paperId));
    const ids = Array.from(selected).filter((id) => filteredIds.has(id));
    if (ids.length === 0) return;
    const results = await Promise.allSettled(
      ids.map((paperId) => patch.mutateAsync({ paperId, inclusionStatus: status })),
    );
    const failed = results.filter((r) => r.status === "rejected");
    if (failed.length > 0) {
      alert(`批量操作：${ids.length - failed.length} 篇成功，${failed.length} 篇失败`);
    }
    // 只清除已操作的 id，保留过滤掉的选中
    setSelected((prev) => {
      const next = new Set(prev);
      ids.forEach((id) => next.delete(id));
      return next;
    });
  };

  // 进入筛选模式时，从过滤后列表第一篇开始
  // P1-4：冻结 paperId 快照，筛选全程基于此快照，不随 filtered 动态变化。
  const startScreening = (filteredPapers: ProjectPaperItem[]) => {
    if (filteredPapers.length === 0) return;
    setScreeningQueue(filteredPapers.map((p) => p.paperId));
    setScreeningIndex(0);
    setScreeningMode(true);
  };

  if (isLoading) return <Loading label="加载文献库…" />;
  if (error) return <ErrMsg error={error} />;

  const papers = data?.papers ?? [];
  const counts = countByStatus(papers);
  const filtered = applyFilter(papers, search, statusFilter, extractionFilter, sortField, sortDir);

  // P1-4：筛选模式下的当前文献——从冻结快照（screeningQueue）取 paperId，
  // 再从全量 papers 中找详情，不受过滤条件动态变化影响，确保不跳篇。
  const screeningPaperId = screeningMode ? screeningQueue[screeningIndex] : null;
  const screeningPaper = screeningPaperId != null
    ? (papers.find((p) => p.paperId === screeningPaperId) ?? null)
    : null;

  return (
    <>
      {/* 筛选模式遮罩层（全屏） */}
      {screeningMode && screeningPaper && (
        <ScreeningMode
          paper={screeningPaper}
          current={screeningIndex}
          total={screeningQueue.length}
          researchQuestion={project?.researchQuestion ?? ""}
          onDecide={async (status, exclusionReason) => {
            await patch.mutateAsync({
              paperId: screeningPaper.paperId,
              inclusionStatus: status,
              exclusionReason,
            });
            // P1-4：基于快照长度判断是否到末尾，不依赖实时 filtered.length
            if (screeningIndex + 1 < screeningQueue.length) {
              setScreeningIndex((i) => i + 1);
            } else {
              setScreeningMode(false);
            }
          }}
          onClose={() => setScreeningMode(false)}
        />
      )}

      {/* 导入弹层 */}
      {showImport && (
        <ImportDialog
          importing={importMut.isPending}
          result={importMut.data}
          error={importMut.error}
          onImport={(files) => importMut.mutate({ files })}
          onClose={() => {
            setShowImport(false);
            importMut.reset();
          }}
        />
      )}

      {/* 三栏主体（窄屏选中文献时加 --detail-open，详情覆盖层浮起，dogfood A1） */}
      <div className={selectedPaperId ? "lib-shell lib-shell--detail-open" : "lib-shell"}>
        {/* 左栏：筛选 */}
        <div className="lib-shell-filter">
          <LibFilterPanel
            counts={counts}
            statusFilter={statusFilter}
            onStatusFilter={setStatusFilter}
            search={search}
            onSearch={setSearch}
            // tags 筛选：PaperDetail 有 tags，但 ProjectPaperItem 没有 tags 字段
            // TODO: 若 GET /projects/{pid}/papers 返回 tags，则在此实现标签筛选
          />
        </div>

        {/* 中栏：列表 */}
        <div className="lib-shell-list">
          <LibPaperList
            papers={filtered}
            allPapers={papers}
            selected={selected}
            selectedPaperId={selectedPaperId}
            sortField={sortField}
            sortDir={sortDir}
            onSort={handleSort}
            onSelectRow={handleSelectRow}
            onToggleSelect={(id) => {
              setSelected((prev) => {
                const s = new Set(prev);
                s.has(id) ? s.delete(id) : s.add(id);
                return s;
              });
            }}
            onSelectAll={() => handleSelectAll(filtered)}
            onBulkStatus={(status) => handleBulkStatus(status, filtered)}
            onStartScreening={() => startScreening(filtered)}
            onShowImport={() => setShowImport(true)}
            isBulkPending={patch.isPending}
            extractionFilter={extractionFilter}
            onExtractionFilter={setExtractionFilter}
            isBackfilling={backfillMut.isPending}
            isExtracting={extractMut.isPending}
            backfillResult={backfillResult}
            extractResult={extractResult}
            onBackfill={() => {
              setBackfillResult(null);
              backfillMut.mutate({ onlyMissing: true }, {
                onSuccess: (r) => setBackfillResult(r),
              });
            }}
            onExtract={() => {
              setExtractResult(null);
              extractMut.mutate({ reextract: false }, {
                onSuccess: (r) => setExtractResult(r),
              });
            }}
            onClearBackfillResult={() => setBackfillResult(null)}
            onClearExtractResult={() => setExtractResult(null)}
          />
        </div>

        {/* 右栏：详情 */}
        <div className="lib-shell-detail">
          {selectedPaperId ? (
            <LibPaperDetail
              pid={pidNum}
              paperId={selectedPaperId}
              onBack={() => {
                // codex A1-P2: 同步 URL 回列表，否则刷新/重建会经 paperIdParam 重新打开覆盖层
                setSelectedPaperId(null);
                navigate(`/projects/${pid}/library`, { replace: true });
              }}
            />
          ) : (
            <div className="lib-empty" style={{ padding: "2rem 1rem" }}>
              <p style={{ fontSize: "0.9rem", color: "var(--ink-3)" }}>
                选择左侧文献查看详情
              </p>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
