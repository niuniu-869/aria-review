/**
 * LibraryStatusBar.tsx — 文献库状态栏（Task 5）
 *
 * chip 行显示：
 *   全局共享库 N · 本项目 M(纳入 K) · 元数据 · PDF · 已OCR · 待OCR · 失败 · 语料状态徽章
 *
 * 语料状态读自 corpus prop（来自 useProject 的 activeCorpus）。
 * ⓘ 触发 LibraryModelInfo 弹层（无障碍完整）。
 */
import { useRef, useState } from "react";
import type { ProjectLibraryStats } from "../api/agentHooks";
import { LibraryModelInfo } from "./LibraryModelInfo";

interface CorpusSummary {
  status: "parsing" | "ready" | "failed";
  documentCount: number;
  stale: boolean;
}

interface Props {
  stats: ProjectLibraryStats | null;
  globalTotal: number | null;
  corpus: CorpusSummary | null;
}

function Chip({
  label,
  value,
  variant,
  title,
}: {
  label: string;
  value?: string | number;
  variant?: "default" | "ok" | "warn" | "danger" | "muted";
  title?: string;
}) {
  const cls = `lib-status-chip lib-status-chip--${variant ?? "default"}`;
  return (
    <span className={cls} title={title}>
      {label}
      {value !== undefined && <strong className="lib-status-chip-value">{value}</strong>}
    </span>
  );
}

function CorpusBadge({ corpus }: { corpus: CorpusSummary | null }) {
  if (!corpus) return null;
  if (corpus.status === "parsing") {
    return <Chip label="语料解析中…" variant="warn" title="正在构建分析语料" />;
  }
  if (corpus.status === "failed") {
    return <Chip label="语料失败" variant="danger" title="语料构建失败，请重试" />;
  }
  // ready
  if (corpus.stale) {
    return (
      <Chip
        label="需更新"
        value={corpus.documentCount}
        variant="warn"
        title="纳排状态已变更，语料需重建"
      />
    );
  }
  return (
    <Chip
      label="就绪"
      value={corpus.documentCount}
      variant="ok"
      title={`分析语料就绪，共 ${corpus.documentCount} 篇`}
    />
  );
}

export function LibraryStatusBar({ stats, globalTotal, corpus }: Props) {
  const [infoOpen, setInfoOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);

  // Loading skeleton
  if (stats === null && globalTotal === null) {
    return (
      <div className="lib-status-bar lib-status-bar--loading" aria-busy="true">
        <span className="lib-status-chip lib-status-chip--muted">加载中…</span>
      </div>
    );
  }

  const included = stats?.inclusion?.included ?? 0;
  const ocrDone = stats?.ocr?.done ?? 0;
  const ocrPending = (stats?.ocr?.pending ?? 0) + (stats?.ocr?.processing ?? 0);
  const ocrFailed = stats?.ocr?.failed ?? 0;

  return (
    <div className="lib-status-bar" role="region" aria-label="文献库状态">
      {/* 全局共享库 */}
      {globalTotal !== null && (
        <Chip
          label="全局共享库 "
          value={globalTotal}
          variant="default"
          title="全平台所有项目共享的文献总数（按 DOI/标题去重）"
        />
      )}

      {/* 本项目 */}
      {stats && (
        <Chip
          label="本项目 "
          value={`${stats.projectPapers}（纳入 ${included}）`}
          variant="default"
          title={`项目文献共 ${stats.projectPapers} 篇，已纳入 ${included} 篇`}
        />
      )}

      {/* 元数据 */}
      {stats && stats.withMetadata > 0 && (
        <Chip
          label="元数据 "
          value={stats.withMetadata}
          variant="muted"
          title="含摘要或 CSL-JSON 的完整题录数"
        />
      )}

      {/* PDF */}
      {stats && stats.withPdf > 0 && (
        <Chip label="PDF " value={stats.withPdf} variant="muted" title="已上传 PDF 全文附件数" />
      )}

      {/* 已OCR */}
      {ocrDone > 0 && (
        <Chip label="已OCR " value={ocrDone} variant="ok" title="PDF 已完成 OCR 解析，可作综述语料" />
      )}

      {/* 待OCR */}
      {ocrPending > 0 && (
        <Chip label="待OCR " value={ocrPending} variant="warn" title="PDF 等待或正在 OCR 解析" />
      )}

      {/* OCR 失败 */}
      {ocrFailed > 0 && (
        <Chip label="失败 " value={ocrFailed} variant="danger" title="OCR 解析失败，可重新上传" />
      )}

      {/* 语料状态 */}
      <CorpusBadge corpus={corpus} />

      {/* ⓘ 库说明 */}
      <button
        ref={triggerRef}
        className="lib-status-info-btn"
        onClick={() => setInfoOpen((v) => !v)}
        aria-label="库说明"
        aria-expanded={infoOpen}
        title="了解文献库模型（全局共享 + 项目纳排）"
      >
        ⓘ 库说明
      </button>

      <LibraryModelInfo
        open={infoOpen}
        onClose={() => setInfoOpen(false)}
        triggerRef={triggerRef}
      />
    </div>
  );
}
