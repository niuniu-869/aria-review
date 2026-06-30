/**
 * LibFilterPanel.tsx — 文献库左栏筛选面板
 *
 * - 纳排状态分面（全部/待筛选/已纳入/已排除/待定）+ 计数
 * - 搜索框（客户端过滤）
 * - 标签筛选：ProjectPaperItem 暂无 tags 字段，跳过（TODO）
 * - 手动集合(collections)：后端无此概念，暂不做（YAGNI）
 */
import type { StatusFilter } from "../LibraryView";

interface StatusCount {
  all: number;
  candidate: number;
  included: number;
  excluded: number;
  maybe: number;
}

interface Props {
  counts: StatusCount;
  statusFilter: StatusFilter;
  onStatusFilter: (s: StatusFilter) => void;
  search: string;
  onSearch: (s: string) => void;
}

const STATUS_OPTIONS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "全部" },
  { value: "candidate", label: "待筛选" },
  { value: "included", label: "已纳入" },
  { value: "excluded", label: "已排除" },
  { value: "maybe", label: "待定" },
];

export function LibFilterPanel({ counts, statusFilter, onStatusFilter, search, onSearch }: Props) {
  return (
    <>
      {/* 搜索框 */}
      <div className="lib-filter-section">
        <div className="lib-filter-title">搜索</div>
        <input
          type="search"
          className="lib-search input"
          placeholder="按标题搜索…"
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          aria-label="搜索文献"
        />
      </div>

      {/* 纳排状态分面 */}
      <div className="lib-filter-section">
        <div className="lib-filter-title">纳排状态</div>
        {STATUS_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            className={`lib-facet-item${statusFilter === opt.value ? " active" : ""}`}
            onClick={() => onStatusFilter(opt.value)}
            aria-pressed={statusFilter === opt.value}
          >
            <span>{opt.label}</span>
            <span className="lib-facet-count">
              {counts[opt.value]}
            </span>
          </button>
        ))}
      </div>

      {/* 标签筛选 — ProjectPaperItem 暂无 tags 字段，跳过 */}
      {/* TODO: 当 GET /projects/{pid}/papers 返回 tags 时，实现标签筛选面板 */}

      {/* 手动集合 — 后端无 collections 概念，YAGNI */}
      {/* TODO: M2+ 后端支持 collections 后在此实现集合树 */}
    </>
  );
}
