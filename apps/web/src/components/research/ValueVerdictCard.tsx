/**
 * ValueVerdictCard.tsx — 研究方向价值裁决卡（B4 / 阶段④ UI）。
 *
 * 反「LLM 拍脑袋」的命门可视化：把确定性 resolver 的裁决依据全部摊开——
 *  - 反向检索命中数 vs 透明阈值（低≤真空白 / 高≥疑伪空白）的刻度对比；
 *  - 计量结构佐证（共现断层/低耦合）；
 *  - `decided_by=deterministic` 徽标（强调裁决由确定性代码出，非 LLM）。
 * 扩展既有可信视觉语言（TrustCard 盾形 + 朱砂 + 诚实标注）。
 *
 * HITL 控件：accept / reject / revise（调 PATCH）。revise 内联改写 statement 后回写。
 * 绝不自动定稿：裁决只是「证据 + 规则结论」，是否采纳由人决定。
 *
 * 纯展示（result 驱动）：裁决/证据由 props 注入；PATCH 由 onDecide 上抛（B5 接 usePatchGap）。
 */
import { useState } from "react";
import type { GapCandidate, GapPatchAction, GapVerdictResult } from "../../types/research";
import type { ValueVerdictKind } from "../../types/research";
import { ErrMsg, Loading } from "../../lib/ui";

const VERDICT_META: Record<ValueVerdictKind, { label: string; cls: string; note: string }> = {
  valuable: { label: "有研究价值", cls: "vv-valuable", note: "低命中 + 计量结构断层 → 判定为真空白" },
  likely_filled: { label: "疑似伪空白", cls: "vv-filled", note: "高命中 → 已有大量研究，疑为检索不全" },
  inconclusive: { label: "证据不足", cls: "vv-incon", note: "命中介于阈值之间且无明确断层 → 待人工复核" },
};

const METRIC_LABEL: Record<string, string> = {
  cooccurrence_gap: "共现断层",
  low_coupling: "低耦合",
};

/** 盾形图标（搬自 TrustCard，统一可信视觉）。 */
function ShieldIcon() {
  return (
    <span className="vv-shield" aria-hidden="true">
      <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8">
        <path d="M12 2 4 5v6c0 5 3.5 8.5 8 11 4.5-2.5 8-6 8-11V5l-8-3Z" />
        <path d="m9 12 2 2 4-4" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </span>
  );
}

/** 反向检索命中刻度：命中数相对透明阈值 [低, 高] 的位置 + 三区着色。 */
function ReverseHitScale({ hit, low, high }: { hit: number; low: number; high: number }) {
  // 阈值异常兜底（codex B4-P2）：low>=high 时三区语义矛盾，降级为单值呈现，不画错刻度
  if (!(low < high)) {
    return (
      <div className="vv-scale-wrap">
        <div
          className="vv-scale-degraded"
          role="img"
          aria-label={`反向检索命中 ${hit} 篇；阈值异常 低 ${low} 高 ${high}`}
        >
          命中 <strong>{hit}</strong> 篇 · 阈值异常（低 {low} ≥ 高 {high}），请人工核验
        </div>
      </div>
    );
  }
  // 刻度上界：留出余量，保证高阈值与命中点都可见
  const max = Math.max(Math.round(high * 1.6), hit + 1, high + 2);
  const frac = (n: number) => Math.min(Math.max(n / max, 0), 1);
  const pct = (n: number) => `${frac(n) * 100}%`;
  // marker 夹到 [4%,96%]，避免端值(hit=0 / hit>>high)被裁切或溢出卡片（真值由数字呈现，codex B4-P2）
  const markerLeft = `${Math.min(Math.max(frac(hit), 0.04), 0.96) * 100}%`;
  return (
    <div className="vv-scale-wrap">
      <div className="vv-scale" role="img" aria-label={`反向检索命中 ${hit} 篇；阈值 低 ${low}、高 ${high}`}>
        <div className="vv-scale-track">
          <div className="vv-zone vv-zone-void" style={{ left: 0, width: pct(low) }} />
          <div className="vv-zone vv-zone-mid" style={{ left: pct(low), width: `calc(${pct(high)} - ${pct(low)})` }} />
          <div className="vv-zone vv-zone-filled" style={{ left: pct(high), right: 0 }} />
          <div className="vv-tick" style={{ left: pct(low) }} />
          <div className="vv-tick" style={{ left: pct(high) }} />
        </div>
        <div className="vv-marker" style={{ left: markerLeft }}>
          <span className="vv-marker-dot" />
          <span className="vv-marker-num">{hit}</span>
        </div>
      </div>
      <div className="vv-scale-legend">
        <span className="vv-leg vv-leg-void">≤{low} 真空白</span>
        <span className="vv-leg vv-leg-mid">存疑</span>
        <span className="vv-leg vv-leg-filled">≥{high} 疑填补</span>
      </div>
    </div>
  );
}

export interface ValueVerdictCardProps {
  /** 裁决 + 证据包（GET .../verdict） */
  result?: GapVerdictResult | null;
  /** 当前 GAP（供 revise 预填 statement / 展示当前状态） */
  gap?: GapCandidate | null;
  /** HITL 决策回调（accept/reject 无 statement；revise 带新 statement）。
   *  可返回 Promise：revise 仅在其 resolve 后关闭编辑态，reject 时保留草稿（codex B4-P2）。 */
  onDecide?: (action: GapPatchAction, statement?: string) => void | Promise<unknown>;
  isDeciding?: boolean;
  decideError?: Error | null;
  /** 裁决加载/错误（尚未核验时调用方可不渲染本卡） */
  isLoading?: boolean;
  error?: Error | null;
}

export function ValueVerdictCard({
  result,
  gap,
  onDecide,
  isDeciding,
  decideError,
  isLoading,
  error,
}: ValueVerdictCardProps) {
  const [revising, setRevising] = useState(false);
  const [draft, setDraft] = useState("");

  if (isLoading) {
    return (
      <div className="card vv-card">
        <Loading label="加载价值裁决…" />
      </div>
    );
  }
  if (error) {
    return (
      <div className="card vv-card">
        <ErrMsg error={error} />
      </div>
    );
  }
  if (!result) return null;

  const { verdict, evidence } = result;
  const meta = VERDICT_META[verdict.verdict];
  const rs = evidence.reverse_search;
  const bs = evidence.biblio_structure;
  const decided = gap?.status === "accepted" || gap?.status === "rejected";

  function startRevise() {
    setDraft(gap?.statement ?? "");
    setRevising(true);
  }
  async function submitRevise() {
    const next = draft.trim();
    if (!next) return;
    try {
      await onDecide?.("revise", next);
      setRevising(false); // 仅 PATCH 成功后关闭编辑态（codex B4-P2）
    } catch {
      /* 失败：保留 textarea 与 draft 供继续改写重试；错误由父级 decideError 展示 */
    }
  }

  return (
    <div className={`card vv-card ${meta.cls}`} aria-label="价值裁决卡">
      <div className="vv-head">
        <ShieldIcon />
        <h3 className="vv-title">研究方向价值裁决</h3>
        {/* decided_by 徽标：仅 deterministic 才声称「非 LLM」；其它值如实标注待核，绝不伪装（codex B4-P1） */}
        {verdict.decided_by === "deterministic" ? (
          <span className="vv-decided" title="裁决由确定性 resolver 给出，非 LLM 生成">
            确定性裁决 · 非 LLM
          </span>
        ) : (
          <span className="vv-decided vv-decided-warn" title="裁决来源非确定性，请人工核验">
            来源待核 · 由 {verdict.decided_by} 决定
          </span>
        )}
      </div>

      <div className="vv-verdict-row">
        <span className={`vv-verdict-pill ${meta.cls}`}>{meta.label}</span>
        {verdict.score != null && <span className="vv-score">价值分 {verdict.score.toFixed(2)}</span>}
      </div>
      <p className="vv-rationale">{verdict.rationale}</p>

      {/* 反向检索命中对比（透明阈值） */}
      <div className="vv-section">
        <div className="vv-section-label">
          反向检索证伪 · {rs.provider === "sciverse" ? "Sciverse" : "OpenAlex"}
        </div>
        <ReverseHitScale hit={rs.hit_count} low={verdict.thresholds.reverse_hit_low} high={verdict.thresholds.reverse_hit_high} />
        {rs.top_hits.length > 0 && (
          <ul className="vv-hits">
            {rs.top_hits.map((h, i) => (
              <li className="vv-hit" key={`${h.doi ?? "nodoi"}-${i}`}>
                <span className="vv-hit-title">{h.title}</span>
                <span className="vv-hit-meta">
                  {h.year ?? "年份缺失"}
                  {h.doi ? (
                    <a className="vv-hit-doi" href={`https://doi.org/${h.doi}`} target="_blank" rel="noreferrer">
                      {h.doi}
                    </a>
                  ) : (
                    <span className="vv-hit-nodoi">无 DOI</span>
                  )}
                  <span className="vv-hit-rel">相关 {h.relevance.toFixed(2)}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* 计量结构佐证 */}
      <div className="vv-section">
        <div className="vv-section-label">计量结构佐证</div>
        <div className="vv-struct">
          <span className="badge badge-soft">{METRIC_LABEL[bs.metric] ?? bs.metric}</span>
          <span className="vv-struct-val">{bs.value.toFixed(2)}</span>
          {bs.source_view && <span className="vv-struct-src">取自「{bs.source_view}」视图</span>}
        </div>
        <p className="vv-struct-interp">{bs.interpretation}</p>
      </div>

      {/* fail-loud：跳过项显式列出 */}
      {evidence.skipped.length > 0 && (
        <div className="vv-skipped" role="note">
          已跳过：{evidence.skipped.map((s) => s.reason).join("；")}
        </div>
      )}

      {/* HITL 控件：accept / reject / revise（不自动定稿） */}
      {onDecide && (
        <div className="vv-actions">
          {decided ? (
            <span className="vv-decided-note">
              已{gap?.status === "accepted" ? "采纳" : "驳回"}（人工定稿，可重新决策）
            </span>
          ) : null}
          {revising ? (
            <div className="vv-revise">
              <textarea
                className="vv-revise-input"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                rows={3}
                aria-label="改写 GAP 论断"
                placeholder="改写后的研究空白论断…"
              />
              <div className="vv-revise-btns">
                <button type="button" className="btn btn-primary" disabled={isDeciding || !draft.trim()} onClick={submitRevise}>
                  提交改写
                </button>
                <button type="button" className="btn btn-ghost" disabled={isDeciding} onClick={() => setRevising(false)}>
                  取消
                </button>
              </div>
            </div>
          ) : (
            <div className="vv-decide-btns">
              <button type="button" className="btn btn-primary" disabled={isDeciding} onClick={() => onDecide("accept")}>
                采纳
              </button>
              <button type="button" className="btn btn-ghost" disabled={isDeciding} onClick={() => onDecide("reject")}>
                驳回
              </button>
              <button type="button" className="btn btn-ghost" disabled={isDeciding} onClick={startRevise}>
                改写
              </button>
            </div>
          )}
          {decideError && <ErrMsg error={decideError} />}
        </div>
      )}
    </div>
  );
}
