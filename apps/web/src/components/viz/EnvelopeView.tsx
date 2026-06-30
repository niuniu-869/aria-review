/**
 * EnvelopeView — A4 统一可用性信封消费器 (纯函数 + 薄组件)
 *
 * 把 react-query 的查询态 + AnalysisEnvelope 三态归一为四种渲染分支:
 *   - loading  → ChartCard 的 loading (spinner)
 *   - error    → ChartCard 的 error (.state-err, 网络/HTTP 失败)
 *   - unavailable (available:false) → InsufficientData (按 reason 文案)
 *   - available (available:true)    → 渲染 data (children(data))
 *
 * 关键: 区分 error(HTTP 失败) 与 unavailable(后端诚实降级, HTTP 200 但 available:false),
 * 二者不混淆 (spec §4.0)。
 */
import type { ReactNode } from "react";
import { InsufficientData } from "./InsufficientData";
import type { AnalysisUnavailableReason } from "./InsufficientData";

/** 与契约对齐的最小信封形状 (available:true 携带 data; false 携带 reason/message) */
export type Envelope<T> =
  | ({ available: true; data: T } & Record<string, unknown>)
  | ({
      available: false;
      reason: AnalysisUnavailableReason;
      missingField?: string | null;
      message: string;
      howto?: string | null;
    } & Record<string, unknown>);

/** 纯函数: 把查询态映射为渲染分支判定 (供单测直接断言) */
export type EnvelopeBranch =
  | { kind: "loading" }
  | { kind: "error" }
  | { kind: "unavailable" }
  | { kind: "available" };

export function resolveEnvelopeBranch<T>(args: {
  isLoading: boolean;
  isError: boolean;
  data: Envelope<T> | undefined;
}): EnvelopeBranch {
  if (args.isLoading) return { kind: "loading" };
  if (args.isError) return { kind: "error" };
  if (!args.data) return { kind: "loading" }; // 无数据且未报错 → 仍视为加载中
  if (args.data.available === false) return { kind: "unavailable" };
  return { kind: "available" };
}

export interface EnvelopeBodyProps<T> {
  isLoading: boolean;
  isError: boolean;
  error?: unknown;
  data: Envelope<T> | undefined;
  /** available:true 时渲染 data */
  children: (data: T) => ReactNode;
}

/**
 * 信封内容渲染器 (不含 ChartCard 外壳)。
 * loading/error 交给外层 ChartCard 的 loading/error 槽更合适, 但当面板把 ChartCard
 * 的三态自管时, 也可用此组件统一处理 unavailable/available, loading/error 由调用方决定。
 *
 * 用法: 通常配合 ChartCard, 把 loading/error 传给 ChartCard, 把 unavailable 作为 empty 节点,
 * available 作为 children。见 envelopeChartProps() 辅助。
 */
export function EnvelopeBody<T>({ data, children }: { data: Envelope<T> | undefined; children: (data: T) => ReactNode }) {
  if (!data || data.available === false) return null;
  return <>{children(data.data)}</>;
}

/**
 * 辅助: 由查询态 + 信封算出 ChartCard 所需的 { loading, error, empty } 三态属性。
 * available:false → empty 用 InsufficientData; available:true → empty 为 undefined (渲染 children)。
 */
export function envelopeChartProps<T>(args: {
  isLoading: boolean;
  isError: boolean;
  error?: unknown;
  data: Envelope<T> | undefined;
}): { loading: boolean; error: unknown; empty: ReactNode | undefined } {
  const branch = resolveEnvelopeBranch(args);
  if (branch.kind === "unavailable" && args.data && args.data.available === false) {
    const d = args.data;
    return {
      loading: false,
      error: undefined,
      empty: (
        <InsufficientData
          reason={d.reason}
          missingField={d.missingField ?? undefined}
          message={d.message}
          howto={d.howto ?? undefined}
        />
      ),
    };
  }
  return {
    loading: branch.kind === "loading",
    error: branch.kind === "error" ? (args.error ?? new Error("加载失败")) : undefined,
    empty: undefined,
  };
}
