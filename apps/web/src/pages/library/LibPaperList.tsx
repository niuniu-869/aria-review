/**
 * LibPaperList.tsx — 文献库中栏虚拟列表
 *
 * - 使用 @tanstack/react-virtual 虚拟滚动（大库性能保障）
 * - 列排序（标题/年份/评分）
 * - 多选 checkbox + 批量操作工具条
 * - 顶部「导入文献」按钮
 * - 「进入筛选模式」开关
 */
import { useRef } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import type { BackfillMetadataResult, ExtractStructuredResult, InclusionStatus, ProjectPaperItem } from "../../api/client";
import type { ExtractionFilter, SortDir, SortField } from "../LibraryView";
import { PaperStatusBadges } from "../../components/PaperStatusBadges";

/** 每行高度（固定） */
const ROW_HEIGHT = 44;

const INCLUSION_LABELS: Record<InclusionStatus, string> = {
  candidate: "待筛选",
  included: "已纳入",
  excluded: "已排除",
  maybe: "待定",
};

function StatusBadge({ status }: { status: InclusionStatus }) {
  return (
    <span className={`lib-status-badge lib-status-${status}`}>
      {INCLUSION_LABELS[status]}
    </span>
  );
}

interface Props {
  papers: ProjectPaperItem[];          // 过滤+排序后
  allPapers: ProjectPaperItem[];       // 全量（用于显示总数对比）
  selected: Set<number>;
  selectedPaperId: number | null;
  sortField: SortField;
  sortDir: SortDir;
  onSort: (f: SortField) => void;
  onSelectRow: (paperId: number) => void;
  onToggleSelect: (paperId: number) => void;
  onSelectAll: () => void;
  onBulkStatus: (status: InclusionStatus) => void;
  onStartScreening: () => void;
  onShowImport: () => void;
  isBulkPending: boolean;
  // P3-T2/T4: AI 解析动作
  extractionFilter: ExtractionFilter;
  onExtractionFilter: (f: ExtractionFilter) => void;
  isBackfilling: boolean;
  isExtracting: boolean;
  backfillResult: BackfillMetadataResult | null;
  extractResult: ExtractStructuredResult | null;
  onBackfill: () => void;
  onExtract: () => void;
  onClearBackfillResult: () => void;
  onClearExtractResult: () => void;
}

function SortIcon({ field, sortField, sortDir }: { field: SortField; sortField: SortField; sortDir: SortDir }) {
  if (field !== sortField) return <span style={{ opacity: 0.3 }}>↕</span>;
  return <span>{sortDir === "asc" ? "↑" : "↓"}</span>;
}

export function LibPaperList({
  papers,
  allPapers,
  selected,
  selectedPaperId,
  sortField,
  sortDir,
  onSort,
  onSelectRow,
  onToggleSelect,
  onSelectAll,
  onBulkStatus,
  onStartScreening,
  onShowImport,
  isBulkPending,
  extractionFilter,
  onExtractionFilter,
  isBackfilling,
  isExtracting,
  backfillResult,
  extractResult,
  onBackfill,
  onExtract,
  onClearBackfillResult,
  onClearExtractResult,
}: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);

  const virtualizer = useVirtualizer({
    count: papers.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 5,
  });

  const isAllSelected = papers.length > 0 && selected.size === papers.length;
  const hasSomeSelected = selected.size > 0;
  const isFiltered = papers.length !== allPapers.length;

  return (
    <>
      {/* 工具条 */}
      <div className="lib-toolbar">
        <button className="btn btn-primary" onClick={onShowImport} style={{ fontSize: "0.85rem", padding: "0.4rem 0.85rem" }}>
          + 导入文献
        </button>
        <button
          className="btn"
          onClick={onStartScreening}
          disabled={papers.length === 0}
          style={{ fontSize: "0.85rem", padding: "0.4rem 0.85rem" }}
        >
          进入筛选模式
        </button>
        <button
          className="btn"
          onClick={onBackfill}
          disabled={isBackfilling}
          aria-busy={isBackfilling}
          title="对缺元数据的文献用 AI 自动补全摘要、作者等字段（仅补空字段，不覆盖已有）"
          style={{ fontSize: "0.85rem", padding: "0.4rem 0.85rem" }}
        >
          {isBackfilling ? "补全中…" : "AI 补全元数据"}
        </button>
        <button
          className="btn"
          onClick={onExtract}
          disabled={isExtracting}
          aria-busy={isExtracting}
          title="对 OCR 完成的文献用 AI 抽取研究问题/方法/结论/数据集/贡献（可反复点直到 available=0）"
          style={{ fontSize: "0.85rem", padding: "0.4rem 0.85rem" }}
        >
          {isExtracting ? "解析中…" : "AI 解析（结构化）"}
        </button>
        <div className="lib-toolbar-spacer" />
        <span className="muted" style={{ fontSize: "0.8rem" }}>
          {isFiltered ? `${papers.length} / ${allPapers.length}` : allPapers.length} 篇
        </span>
      </div>

      {/* AI 动作反馈条 */}
      {backfillResult && (
        <div className="lib-ai-feedback" role="status" aria-live="polite">
          <span>
            AI 补全元数据：补全 <strong>{backfillResult.updated}</strong> 篇 / 跳过{" "}
            {backfillResult.skipped} / 失败 {backfillResult.failed}
            {backfillResult.available > 0 && (
              <span className="muted">（待补 {backfillResult.available} 篇）</span>
            )}
          </span>
          <button
            className="btn-close-feedback"
            onClick={onClearBackfillResult}
            aria-label="关闭补全反馈"
            style={{ marginLeft: "0.5rem", fontSize: "0.8rem", cursor: "pointer", background: "none", border: "none", color: "var(--ink-3)" }}
          >
            ×
          </button>
        </div>
      )}
      {extractResult && (
        <div className="lib-ai-feedback" role="status" aria-live="polite">
          <span>
            AI 解析：抽取 <strong>{extractResult.extracted}</strong> 篇 / 跳过{" "}
            {extractResult.skipped} / 失败 {extractResult.failed}
            {extractResult.available > 0 && (
              <span className="muted">（待解析 {extractResult.available} 篇，可再次点击）</span>
            )}
          </span>
          <button
            className="btn-close-feedback"
            onClick={onClearExtractResult}
            aria-label="关闭解析反馈"
            style={{ marginLeft: "0.5rem", fontSize: "0.8rem", cursor: "pointer", background: "none", border: "none", color: "var(--ink-3)" }}
          >
            ×
          </button>
        </div>
      )}

      {/* 已解析过滤 chip 组 */}
      <div className="lib-extraction-chips" role="group" aria-label="已解析过滤">
        {(["all", "extracted", "not-extracted"] as ExtractionFilter[]).map((f) => (
          <button
            key={f}
            className={`lib-extraction-chip${extractionFilter === f ? " active" : ""}`}
            onClick={() => onExtractionFilter(f)}
            aria-pressed={extractionFilter === f}
          >
            {f === "all" ? "全部" : f === "extracted" ? "已解析" : "未解析"}
          </button>
        ))}
      </div>

      {/* 批量操作条（有选中时显示） */}
      {hasSomeSelected && (
        <div className="lib-bulk-bar">
          <span>已选 {selected.size} 篇</span>
          <button
            className="btn"
            disabled={isBulkPending}
            onClick={() => onBulkStatus("included")}
            style={{ background: "rgba(47,125,79,0.85)", borderColor: "transparent", color: "#fff" }}
          >
            纳入
          </button>
          <button
            className="btn"
            disabled={isBulkPending}
            onClick={() => onBulkStatus("excluded")}
            style={{ background: "rgba(192,67,43,0.85)", borderColor: "transparent", color: "#fff" }}
          >
            排除
          </button>
          <button
            className="btn"
            disabled={isBulkPending}
            onClick={() => onBulkStatus("candidate")}
            style={{ background: "rgba(90,90,90,0.7)", borderColor: "transparent", color: "#fff" }}
          >
            待筛选
          </button>
          <button
            className="btn"
            disabled={isBulkPending}
            onClick={() => onBulkStatus("maybe")}
            style={{ background: "rgba(184,121,26,0.7)", borderColor: "transparent", color: "#fff" }}
          >
            待定
          </button>
        </div>
      )}

      {/* 列头 */}
      <div className="lib-row-header">
        {/* checkbox 全选 */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
          <input
            type="checkbox"
            checked={isAllSelected}
            onChange={onSelectAll}
            aria-label="全选"
            style={{ cursor: "pointer" }}
          />
        </div>
        {/* 标题列（可排序） */}
        <button className="lib-col-sort" onClick={() => onSort("title")}>
          标题 <SortIcon field="title" sortField={sortField} sortDir={sortDir} />
        </button>
        {/* 年份列 */}
        <button className="lib-col-sort" onClick={() => onSort("year")}>
          年份 <SortIcon field="year" sortField={sortField} sortDir={sortDir} />
        </button>
        {/* 评分列 */}
        <button className="lib-col-sort" onClick={() => onSort("screeningScore")}>
          评分 <SortIcon field="screeningScore" sortField={sortField} sortDir={sortDir} />
        </button>
        {/* 状态列 */}
        <span>状态</span>
      </div>

      {/* 虚拟滚动区 */}
      <div className="lib-list-scroll" ref={scrollRef}>
        {papers.length === 0 ? (
          <div className="lib-empty">
            <p>暂无文献</p>
            <p style={{ fontSize: "0.82rem" }}>
              请点击「导入文献」或通过 Agent 对话导入
            </p>
          </div>
        ) : (
          <div
            className="lib-list-inner"
            style={{ height: `${virtualizer.getTotalSize()}px` }}
          >
            {virtualizer.getVirtualItems().map((vItem) => {
              const p = papers[vItem.index];
              const isSelected = selected.has(p.paperId);
              const isFocused = selectedPaperId === p.paperId;
              return (
                <div
                  key={p.paperId}
                  className={`lib-row${isSelected ? " selected" : ""}${isFocused ? " focused" : ""}`}
                  style={{
                    height: `${vItem.size}px`,
                    transform: `translateY(${vItem.start}px)`,
                  }}
                  onClick={() => onSelectRow(p.paperId)}
                  role="row"
                  aria-selected={isFocused}
                >
                  {/* checkbox（P1-3 修复：外层 div 阻泡 + 触发 toggle；
                      input 仅 onChange 触发，onClick 阻泡防止行点击传下来再触发一次）。
                      直接点 checkbox → onChange 触发一次 → toggle；
                      点 checkbox 区域外层 div → stopPropagation 阻止行 onSelectRow，
                      再由外层 div 的 onClick 调用 onToggleSelect（已阻泡，不冒至行）。*/}
                  <div
                    style={{ display: "flex", alignItems: "center", justifyContent: "center" }}
                    onClick={(e) => { e.stopPropagation(); onToggleSelect(p.paperId); }}
                  >
                    <input
                      type="checkbox"
                      checked={isSelected}
                      onChange={() => onToggleSelect(p.paperId)}
                      onClick={(e) => e.stopPropagation()}
                      aria-label={`选择 ${p.title}`}
                      style={{ cursor: "pointer" }}
                    />
                  </div>
                  {/* 标题 */}
                  <span
                    style={{
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      color: isFocused ? "var(--cinnabar-2)" : "var(--ink)",
                      fontWeight: isFocused ? 600 : 400,
                    }}
                    title={p.title ?? undefined}
                  >
                    {p.title || "（无标题）"}
                  </span>
                  {/* 年份 */}
                  <span className="tnum muted" style={{ fontSize: "0.82rem" }}>
                    {p.year ?? "—"}
                  </span>
                  {/* 评分 */}
                  <span className="tnum muted" style={{ fontSize: "0.82rem" }}>
                    {p.screeningScore != null ? p.screeningScore.toFixed(0) : "—"}
                  </span>
                  {/* PDF/OCR/元数据状态徽章（Task 6） */}
                  <PaperStatusBadges
                    hasPdf={p.hasPdf}
                    ocrStatus={p.ocrStatus}
                  />
                  {/* 纳排状态徽章 */}
                  <StatusBadge status={p.inclusionStatus as InclusionStatus} />
                </div>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}
