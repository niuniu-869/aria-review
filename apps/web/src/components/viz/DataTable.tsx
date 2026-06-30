/**
 * DataTable.tsx — 泛型可排序/分页表
 *
 * - columns 含 key/label/align?/sortable?/format?；复用 table.tbl + .tnum
 * - 点击表头排序（数字 vs 字符串自动判定）；底部分页控件
 * - 受控 initialSort 可选；空数据显示 emptyText
 */
import { useMemo, useState } from "react";
import type { ReactNode } from "react";

export interface DataTableColumn<T> {
  /** 行对象上的字段名（也用作 React key） */
  key: keyof T & string;
  label: string;
  align?: "left" | "right" | "center";
  sortable?: boolean;
  /** 单元格渲染（默认直接显示 row[key]） */
  format?: (value: T[keyof T], row: T) => ReactNode;
}

export interface DataTableProps<T> {
  columns: DataTableColumn<T>[];
  rows: T[];
  /** 每页行数，默认 10 */
  pageSize?: number;
  emptyText?: string;
  initialSort?: { key: keyof T & string; dir: "asc" | "desc" };
  /** 按行返回 className（如核心区行高亮）；返回空/undefined 不加类 */
  rowClassName?: (row: T) => string | undefined;
}

type SortState<T> = { key: keyof T & string; dir: "asc" | "desc" } | null;

/** 比较两值：数字按数值，其余按本地化字符串 */
function compare(a: unknown, b: unknown): number {
  if (a == null && b == null) return 0;
  if (a == null) return -1;
  if (b == null) return 1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), "zh-Hans-CN", { numeric: true });
}

export function DataTable<T extends Record<string, unknown>>({
  columns,
  rows,
  pageSize = 10,
  emptyText = "暂无数据",
  initialSort,
  rowClassName,
}: DataTableProps<T>) {
  const [sort, setSort] = useState<SortState<T>>(initialSort ?? null);
  const [page, setPage] = useState(0);

  // pageSize 防御：clamp 为正整数，避免传 0(→Infinity 页数)/负数导致异常分页
  const size = Number.isFinite(pageSize) && pageSize >= 1 ? Math.floor(pageSize) : 10;

  // 排序后的行（不可变，避免改原数组）
  const sortedRows = useMemo(() => {
    if (!sort) return rows;
    const sorted = [...rows].sort((ra, rb) => {
      const r = compare(ra[sort.key], rb[sort.key]);
      return sort.dir === "asc" ? r : -r;
    });
    return sorted;
  }, [rows, sort]);

  const pageCount = Math.max(1, Math.ceil(sortedRows.length / size));
  // 排序/数据变化后页码越界则回退
  const safePage = Math.min(page, pageCount - 1);
  const pageRows = sortedRows.slice(safePage * size, safePage * size + size);

  function toggleSort(key: keyof T & string) {
    setPage(0);
    setSort((prev) => {
      if (!prev || prev.key !== key) return { key, dir: "asc" };
      if (prev.dir === "asc") return { key, dir: "desc" };
      return null; // 第三次点击取消排序
    });
  }

  if (rows.length === 0) {
    return <p className="viz-table-empty muted">{emptyText}</p>;
  }

  return (
    <div className="viz-table-wrap">
      <table className="tbl">
        <thead>
          <tr>
            {columns.map((col) => {
              const active = sort?.key === col.key;
              const arrow = active ? (sort?.dir === "asc" ? " ▲" : " ▼") : "";
              return (
                <th
                  key={col.key}
                  style={{ textAlign: col.align ?? "left" }}
                  aria-sort={
                    active ? (sort?.dir === "asc" ? "ascending" : "descending") : "none"
                  }
                >
                  {col.sortable ? (
                    <button
                      type="button"
                      className="viz-table-sort"
                      onClick={() => toggleSort(col.key)}
                    >
                      {col.label}
                      <span className="viz-table-sort-arrow">{arrow}</span>
                    </button>
                  ) : (
                    col.label
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {pageRows.map((row, i) => (
            <tr key={i} className={rowClassName?.(row)}>
              {columns.map((col) => {
                const v = row[col.key];
                const isNum = typeof v === "number";
                return (
                  <td
                    key={col.key}
                    className={isNum ? "tnum" : undefined}
                    style={{ textAlign: col.align ?? (isNum ? "right" : "left") }}
                  >
                    {col.format ? col.format(v as T[keyof T], row) : (v as ReactNode)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>

      {pageCount > 1 && (
        <div className="viz-table-pager">
          <button
            type="button"
            className="btn btn-ghost"
            disabled={safePage <= 0}
            onClick={() => setPage(safePage - 1)}
          >
            上一页
          </button>
          <span className="viz-table-pager-info tnum">
            {safePage + 1} / {pageCount}
          </span>
          <button
            type="button"
            className="btn btn-ghost"
            disabled={safePage >= pageCount - 1}
            onClick={() => setPage(safePage + 1)}
          >
            下一页
          </button>
        </div>
      )}
    </div>
  );
}
