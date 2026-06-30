/**
 * ChartCard.tsx — 统一图表卡
 *
 * 一卡一图：宋体标题 + 副标题 + 右上角操作槽（actions，放 ExportMenu/控件）。
 * 承载三态：loading（spinner）/ error（.state-err）/ empty（传入空态节点，如 InsufficientData）。
 * 复用既有 .card 外观。
 */
import type { ReactNode } from "react";
import { Loading, ErrMsg } from "../../lib/ui";

export interface ChartCardProps {
  title: string;
  subtitle?: string;
  /** 右上角操作槽（ExportMenu / 滑块 / 切换等） */
  actions?: ReactNode;
  loading?: boolean;
  /** 错误对象（网络/HTTP 失败）→ 渲染 .state-err */
  error?: unknown;
  /** 空态节点（有值则渲染它而非 children），通常是 <InsufficientData/> */
  empty?: ReactNode;
  /** 卡片底部解释/提示文字（--ink-3） */
  hint?: string;
  children?: ReactNode;
}

export function ChartCard({
  title,
  subtitle,
  actions,
  loading,
  error,
  empty,
  hint,
  children,
}: ChartCardProps) {
  return (
    <section className="card viz-chart-card">
      <header className="viz-chart-card-head">
        <div className="viz-chart-card-titles">
          <h3 className="viz-chart-card-title">{title}</h3>
          {subtitle && <p className="viz-chart-card-subtitle">{subtitle}</p>}
        </div>
        {actions && <div className="viz-chart-card-actions">{actions}</div>}
      </header>

      <div className="viz-chart-card-body">
        {loading ? (
          <Loading label="加载中…" />
        ) : error ? (
          <ErrMsg error={error} />
        ) : empty ? (
          empty
        ) : (
          children
        )}
      </div>

      {hint && !loading && !error && !empty && (
        <p className="viz-chart-card-hint muted">{hint}</p>
      )}
    </section>
  );
}
