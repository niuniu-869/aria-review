/**
 * InsufficientData.tsx — 诚实空态
 *
 * 由 §4.0 envelope 的 available:false 驱动：
 * reason ∈ missing_field | not_enough_data | computed_empty | analysis_error
 * 统一图标 + 按 reason 的中文默认标题 + message + howto 提示。走 .muted/--ink-3，居中。
 */
import type { ReactNode } from "react";

/** 与 §4.0 AnalysisUnavailableReason 对齐 */
export type AnalysisUnavailableReason =
  | "missing_field"
  | "not_enough_data"
  | "computed_empty"
  | "analysis_error";

export interface InsufficientDataProps {
  reason: AnalysisUnavailableReason;
  /** missing_field 时缺失的字段名（如 "CR"/"DE"） */
  missingField?: string;
  /** 详细说明（覆盖默认） */
  message?: ReactNode;
  /** 如何补足的提示 */
  howto?: ReactNode;
}

/** 各 reason 的默认图标 + 标题 */
const REASON_META: Record<AnalysisUnavailableReason, { icon: string; title: string }> = {
  missing_field: { icon: "🗂", title: "缺少所需字段" },
  not_enough_data: { icon: "📉", title: "数据样本不足" },
  computed_empty: { icon: "∅", title: "计算结果为空" },
  analysis_error: { icon: "⚠", title: "分析计算出错" },
};

export function InsufficientData({
  reason,
  missingField,
  message,
  howto,
}: InsufficientDataProps) {
  const meta = REASON_META[reason];
  // missing_field 且给了字段名时，标题更具体
  const title =
    reason === "missing_field" && missingField
      ? `缺少字段「${missingField}」`
      : meta.title;

  return (
    <div className="viz-insufficient" role="status">
      <div className="viz-insufficient-icon" aria-hidden="true">
        {meta.icon}
      </div>
      <p className="viz-insufficient-title">{title}</p>
      {message && <p className="viz-insufficient-msg muted">{message}</p>}
      {howto && <p className="viz-insufficient-howto muted">{howto}</p>}
    </div>
  );
}
