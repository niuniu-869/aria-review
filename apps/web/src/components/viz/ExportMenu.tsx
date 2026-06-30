/**
 * ExportMenu.tsx — 导出菜单
 *
 * 三类目标（discriminated union by `kind`）：
 * - ECharts：PNG(getDataURL) + SVG(renderToSVGString) + CSV
 * - vis-network：PNG(canvas.toDataURL) + CSV/JSON（节点/边），**无 SVG**
 * - table：仅 CSV（无图像，用于无图表的纯数据表，如高被引参考文献）
 *
 * 点击触发浏览器下载（Blob + a 标签），文件名带时间戳 YYYYMMDD_HHMMSS。
 */
import { useEffect, useRef, useState } from "react";
import type { EChartHandle } from "./EChart";

/** CSV 列定义（导出数据用） */
export interface CsvColumn<T> {
  key: keyof T & string;
  label: string;
}

/** ECharts 目标：通过 getter 拿到 EChartHandle */
interface EChartExportTarget<T> {
  kind: "echart";
  /** 返回 EChart 的 ref handle（用于 getDataURL / renderToSVGString） */
  getHandle: () => EChartHandle | null;
  /** 该图是否用 svg renderer（仅此时 SVG 导出可用；canvas renderer 不显示 SVG 选项） */
  svgCapable?: boolean;
  /** CSV 数据（可选） */
  csv?: { columns: CsvColumn<T>[]; rows: T[] };
}

/** vis-network 目标：通过 getter 拿到底层 canvas（PNG），及节点/边数据（CSV/JSON） */
interface NetworkExportTarget<T> {
  kind: "network";
  /** 返回 vis-network 容器内的 canvas 元素 */
  getCanvas: () => HTMLCanvasElement | null;
  /** CSV 数据（可选） */
  csv?: { columns: CsvColumn<T>[]; rows: T[] };
  /** JSON 数据（可选，通常是 { nodes, edges }） */
  json?: () => unknown;
}

/** 纯表格目标：仅 CSV（无图像/JSON）。用于「高被引参考文献」等无图表的数据表导出。 */
interface TableExportTarget<T> {
  kind: "table";
  /** CSV 数据（表格目标必填，否则导出菜单无意义） */
  csv: { columns: CsvColumn<T>[]; rows: T[] };
}

export type ExportTarget<T> = EChartExportTarget<T> | NetworkExportTarget<T> | TableExportTarget<T>;

export interface ExportMenuProps<T> {
  target: ExportTarget<T>;
  /** 文件名前缀（不含扩展名/时间戳），默认 "chart" */
  filename?: string;
}

/** 生成 YYYYMMDD_HHMMSS 时间戳 */
export function timestamp(d: Date = new Date()): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}` +
    `_${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`
  );
}

/** 文件名清理：去掉路径/非法字符，防导出名异常 */
function sanitizeFilename(name: string): string {
  return name.replace(/[/\\:*?"<>|]/g, "_").trim() || "chart";
}

/** 触发浏览器下载（Blob + a 标签） */
function downloadBlob(content: BlobPart, mime: string, filename: string) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // 延后释放：部分浏览器在下载接管前 revoke 会中断下载（typeof 守卫兼容无该 API 的环境）
  setTimeout(() => {
    if (typeof URL.revokeObjectURL === "function") URL.revokeObjectURL(url);
  }, 0);
}

/** 触发 dataURL 下载（PNG/SVG 已编码为 dataURL/字符串） */
function downloadDataURL(dataURL: string, filename: string) {
  const a = document.createElement("a");
  a.href = dataURL;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/** CSV 字段转义（含逗号/引号/换行时加引号） */
function csvEscape(v: unknown): string {
  const s = v == null ? "" : String(v);
  if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

/** 把 columns/rows 序列化为 CSV 字符串（带 BOM，Excel 中文不乱码） */
function toCsv<T>(columns: CsvColumn<T>[], rows: T[]): string {
  const header = columns.map((c) => csvEscape(c.label)).join(",");
  const body = rows
    .map((row) => columns.map((c) => csvEscape((row as Record<string, unknown>)[c.key])).join(","))
    .join("\n");
  return `﻿${header}\n${body}`;
}

export function ExportMenu<T>({ target, filename = "chart" }: ExportMenuProps<T>) {
  const [open, setOpen] = useState(false);
  const ts = () => timestamp();
  const safeName = sanitizeFilename(filename);
  const rootRef = useRef<HTMLDivElement>(null);

  // 打开时：外部点击 / Escape 关闭（菜单可用性）
  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function exportPng() {
    if (target.kind === "echart") {
      const url = target.getHandle()?.getDataURL({ type: "png", pixelRatio: 2 });
      if (url) downloadDataURL(url, `${safeName}_${ts()}.png`);
    } else if (target.kind === "network") {
      const canvas = target.getCanvas();
      if (canvas) downloadDataURL(canvas.toDataURL("image/png"), `${safeName}_${ts()}.png`);
    }
    // table 目标无 PNG（按钮已隐藏）
    setOpen(false);
  }

  function exportSvg() {
    // 仅 svg renderer 的 ECharts 目标提供 SVG
    if (target.kind !== "echart") return;
    const svg = target.getHandle()?.renderToSVGString();
    if (svg) downloadBlob(svg, "image/svg+xml", `${safeName}_${ts()}.svg`);
    setOpen(false);
  }

  function exportCsv() {
    if (!target.csv) return;
    const csv = toCsv(target.csv.columns, target.csv.rows);
    downloadBlob(csv, "text/csv;charset=utf-8", `${safeName}_${ts()}.csv`);
    setOpen(false);
  }

  function exportJson() {
    if (target.kind !== "network" || !target.json) return;
    const data = JSON.stringify(target.json(), null, 2);
    downloadBlob(data, "application/json", `${safeName}_${ts()}.json`);
    setOpen(false);
  }

  // PNG 仅图像类目标(echart/network)；table 目标只导出 CSV
  const showPng = target.kind === "echart" || target.kind === "network";
  // SVG 仅在 EChart 显式声明 svgCapable(用 svg renderer) 时可用，避免 canvas renderer 点击无反馈
  const showSvg = target.kind === "echart" && target.svgCapable === true;
  const showJson = target.kind === "network" && !!target.json;
  const showCsv = !!target.csv;

  return (
    <div className="viz-export-menu" ref={rootRef}>
      <button
        type="button"
        className="btn btn-ghost viz-export-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        导出 ▾
      </button>
      {open && (
        <div className="viz-export-dropdown" role="menu">
          {showPng && (
            <button type="button" role="menuitem" className="viz-export-item" onClick={exportPng}>
              PNG 图片
            </button>
          )}
          {showSvg && (
            <button type="button" role="menuitem" className="viz-export-item" onClick={exportSvg}>
              SVG 矢量图
            </button>
          )}
          {showCsv && (
            <button type="button" role="menuitem" className="viz-export-item" onClick={exportCsv}>
              CSV 数据
            </button>
          )}
          {showJson && (
            <button type="button" role="menuitem" className="viz-export-item" onClick={exportJson}>
              JSON 数据
            </button>
          )}
        </div>
      )}
    </div>
  );
}
