/**
 * ScreenPanel — 相关性筛选（A6）
 *
 * 用 AI 对语料逐篇按研究主题相关性 0-10 评分并排序：
 *  - 输入区：研究主题 + 条数 + 开始筛选（宣纸表单），无 LLM key 时温和提示。
 *  - 结果区：ChartCard 包裹 → 顶部统计条 + DataTable（序号 / 相关度条 / 理由）。
 *    相关度渲染成分级配色水平条 + 数值；理由过长截断可展开；默认按相关度降序。
 *  - 三态：loading / error / 空结果均走 ChartCard / InsufficientData 友好处理。
 *
 * 纯前端：不改 aiScreen 签名 / 后端 / schema。relevance 为 0-10 整数或 null（未评估）。
 */
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { aiScreen } from "../api/client";
import type { ScreenResult } from "../api/client";
import { useLlmSettings } from "../api/useLlmSettings";
import { ChartCard, DataTable, InsufficientData } from "./viz";
import type { DataTableColumn } from "./viz";

// ============================================================
// 纯函数（导出供单测）
// ============================================================

/** 相关度分级。≥8 高 / 5-7 中 / <5 低 / null 未评估。 */
export type RelevanceTier = "high" | "mid" | "low" | "none";

export function relevanceTier(rel: number | null | undefined): RelevanceTier {
  if (rel == null || Number.isNaN(rel)) return "none";
  if (rel >= 8) return "high";
  if (rel >= 5) return "mid";
  return "low";
}

/** 各分级的条形配色（宣纸 token）与可读标签。 */
const TIER_META: Record<RelevanceTier, { color: string; label: string }> = {
  high: { color: "var(--ok)", label: "高相关" },
  mid: { color: "var(--gold)", label: "中相关" },
  low: { color: "var(--ink-3)", label: "低相关" },
  none: { color: "var(--line-2)", label: "未评估" },
};

/** 结果集小结：总数 + 高相关数 + 已评估均分（保留一位小数，无评估则 null）。 */
export interface ScreenStats {
  total: number;
  high: number;
  scored: number;
  avg: number | null;
}

export function screenStats(results: ScreenResult["results"]): ScreenStats {
  const total = results.length;
  let high = 0;
  let sum = 0;
  let scored = 0;
  for (const r of results) {
    if (r.relevance != null && !Number.isNaN(r.relevance)) {
      scored += 1;
      sum += r.relevance;
      if (r.relevance >= 8) high += 1;
    }
  }
  return { total, high, scored, avg: scored > 0 ? Math.round((sum / scored) * 10) / 10 : null };
}

/** 理由截断阈值：超过此长度才显示展开/收起。 */
export const REASON_TRUNCATE = 60;

// ============================================================
// 子组件
// ============================================================

/** 相关度水平条：宽度 ∝ rel/10，分级配色，旁注「8/10」；null → 灰条 + 「未评估」。 */
function RelevanceBar({ rel }: { rel: number | null | undefined }) {
  const tier = relevanceTier(rel);
  const meta = TIER_META[tier];
  const has = tier !== "none";
  const pct = has ? Math.max(4, Math.min(100, ((rel as number) / 10) * 100)) : 100;

  return (
    <div className="screen-bar-cell" title={meta.label}>
      <div className="screen-bar-track" role="img"
           aria-label={has ? `相关度 ${rel}/10（${meta.label}）` : "未评估"}>
        <div
          className="screen-bar-fill"
          style={{ width: `${pct}%`, background: meta.color, opacity: has ? 1 : 0.4 }}
        />
      </div>
      <span className={`screen-bar-num tnum${has ? "" : " muted"}`}>
        {has ? `${rel}/10` : "—"}
      </span>
    </div>
  );
}

/** 理由单元格：过长时截断 + 展开/收起（本地 state，每行独立）。 */
function ReasonCell({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const long = text.length > REASON_TRUNCATE;
  if (!long) return <span className="screen-reason">{text}</span>;
  return (
    <span className="screen-reason">
      {open ? text : `${text.slice(0, REASON_TRUNCATE)}…`}{" "}
      <button type="button" className="screen-reason-toggle" onClick={() => setOpen((o) => !o)}>
        {open ? "收起" : "展开"}
      </button>
    </span>
  );
}

// ============================================================
// 主面板
// ============================================================

// DataTable 行类型（加索引签名以满足泛型约束）
type ScreenRow = { idx: number; relevance: number | null; reason: string; [k: string]: unknown };

const columns: DataTableColumn<ScreenRow>[] = [
  { key: "idx", label: "序号", align: "right", sortable: true },
  {
    key: "relevance",
    label: "相关度",
    sortable: true,
    format: (v) => <RelevanceBar rel={v as number | null} />,
  },
  {
    key: "reason",
    label: "理由",
    format: (v) => <ReasonCell text={(v as string) ?? ""} />,
  },
];

export function ScreenPanel({ projectId, corpusId }: { projectId: string; corpusId: string }) {
  const [topic, setTopic] = useState("");
  const [limit, setLimit] = useState(10);

  // M5: 注入 LLM key（从 localStorage 读，不上传服务器）
  const { settings: llm } = useLlmSettings();
  const llmOptions = {
    apiKey: llm.apiKey || undefined,
    baseUrl: llm.baseUrl || undefined,
    model: llm.model || undefined,
  };

  const mut = useMutation({ mutationFn: () => aiScreen(projectId, corpusId, topic, limit, llmOptions) });

  const rows: ScreenRow[] = (mut.data?.results ?? []).map((r) => ({
    idx: r.idx,
    relevance: r.relevance ?? null,
    reason: r.reason,
  }));
  const stats = mut.data ? screenStats(mut.data.results) : null;
  const canRun = topic.trim().length > 0 && !mut.isPending;

  return (
    <section className="screen-panel">
      <h2>相关性筛选</h2>
      <p className="muted screen-intro">
        用 AI 对当前语料逐篇按研究主题相关性 <strong>0-10 评分并排序</strong>，最相关的排在最前，
        辅助你快速完成纳排初筛。
      </p>

      {/* ---------- 输入区 ---------- */}
      <div className="card screen-form">
        <div className="screen-form-row">
          <div className="screen-form-topic">
            <label htmlFor="screen-topic">研究主题</label>
            <input
              id="screen-topic"
              className="input"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="例：深度学习在医学影像分割中的应用"
              onKeyDown={(e) => {
                if (e.key === "Enter" && canRun) mut.mutate();
              }}
            />
          </div>
          <div className="screen-form-limit">
            <label htmlFor="screen-limit">筛选条数</label>
            <input
              id="screen-limit"
              className="input"
              type="number"
              min={1}
              max={50}
              value={limit}
              onChange={(e) => setLimit(Math.max(1, Math.min(50, Number(e.target.value) || 10)))}
            />
          </div>
          <div className="screen-form-action">
            <button type="button" className="btn btn-primary" onClick={() => mut.mutate()} disabled={!canRun}>
              {mut.isPending ? "筛选中…" : "开始筛选"}
            </button>
          </div>
        </div>

        {!llmOptions.apiKey && (
          <p className="muted screen-key-hint">
            未配置 LLM key，将使用占位评分（仍可体验流程）。可在「设置」中填入 key 获得真实 AI 评分。
          </p>
        )}
      </div>

      {/* ---------- 结果区 ---------- */}
      {(mut.isPending || mut.isError || mut.data) && (
        <ChartCard
          title="筛选结果"
          subtitle={
            stats
              ? `共筛选 ${stats.total} 篇 · 高相关(≥8) ${stats.high} 篇${
                  stats.avg != null ? ` · 均分 ${stats.avg}` : ""
                }`
              : "按主题相关性 0-10 排序"
          }
          loading={mut.isPending}
          error={mut.isError ? mut.error : undefined}
          empty={
            mut.data && rows.length === 0 ? (
              <InsufficientData
                reason="computed_empty"
                message="本次筛选未返回结果"
                howto="可换一个更具体的研究主题，或增大筛选条数后重试。"
              />
            ) : undefined
          }
          hint="批量纳入/排除回写文献库将在后续版本支持。"
        >
          {/* 顶部统计条 */}
          {stats && (
            <div className="screen-summary">
              <span className="lib-status-badge lib-status-included">高相关 {stats.high}</span>
              <span className="lib-status-badge lib-status-maybe">已评估 {stats.scored}/{stats.total}</span>
              {stats.avg != null && (
                <span className="lib-status-badge lib-status-candidate">均分 {stats.avg}/10</span>
              )}
            </div>
          )}
          <DataTable
            columns={columns}
            rows={rows}
            pageSize={10}
            initialSort={{ key: "relevance", dir: "desc" }}
            emptyText="暂无筛选结果"
          />
        </ChartCard>
      )}
    </section>
  );
}
