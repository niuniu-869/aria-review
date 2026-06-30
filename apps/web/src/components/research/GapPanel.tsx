/**
 * GapPanel.tsx — 研究空白发现面板（B2 / 阶段③ UI）。
 *
 * concept-centric：以「主题簇 theme」为骨架分组，组内按 GapCandidate 列出，每条标注
 * lens（概念/方法/理论）与 status；可展开见 GAP 论断 + 支撑证据（逐字引文 + 源坐标
 * anchor + 跳文献详情）+ 反证。复用既有可信视觉语言（badge / 宣纸引文 / 朱砂强调），
 * 拒 AI-slop。
 *
 * 诚信约定（铁律映射）：confidence 是 LLM 自评、仅供排序，**非**价值裁决依据；价值由
 * 确定性核验（ValueVerdict, B4）给出。面板头部如实标注，不把置信度伪装成价值结论。
 *
 * 纯展示组件（fixture / hook 皆可驱动）：列表 + 加载/错误/空态由 props 注入；选中联动
 * 交给上层（B5 接线时驱动 B4 价值卡）。领域无关：lens/status/verdict 文案是纯 UI 常量，
 * 不含任何商科词；领域内容只来自数据。
 */
import { useMemo, useState } from "react";
import type { GapCandidate, GapLens, GapStatus } from "../../types/research";
import type { ValueVerdictKind } from "../../types/research";
import { ErrMsg, Loading } from "../../lib/ui";

// lens / status / verdict → UI 文案与样式（领域无关纯 UI 映射）
const LENS_META: Record<GapLens, { label: string; cls: string }> = {
  concept: { label: "概念", cls: "gap-lens-concept" },
  method: { label: "方法", cls: "gap-lens-method" },
  theory: { label: "理论", cls: "gap-lens-theory" },
};
const STATUS_META: Record<GapStatus, { label: string; cls: string }> = {
  draft: { label: "草稿", cls: "badge-soft" },
  verified: { label: "已核验", cls: "gap-status-verified" },
  accepted: { label: "已采纳", cls: "badge-ok" },
  rejected: { label: "已驳回", cls: "badge-danger" },
};
const VERDICT_META: Record<ValueVerdictKind, { label: string; cls: string }> = {
  valuable: { label: "有研究价值", cls: "gap-verdict-valuable" },
  likely_filled: { label: "疑似伪空白", cls: "gap-verdict-filled" },
  inconclusive: { label: "证据不足", cls: "gap-verdict-incon" },
};

export interface GapPanelProps {
  /** 项目 id（文献详情跳转作用域） */
  projectId: number;
  /** GAP 候选列表（fixture 或 scratchpad.entries 驱动） */
  gaps: GapCandidate[];
  isLoading?: boolean;
  error?: Error | null;
  /** 选中某 GAP（供 B4 价值卡联动；可选） */
  onSelectGap?: (gap: GapCandidate) => void;
  /** 当前选中 gap_id（高亮联动） */
  selectedGapId?: string | null;
}

interface GapCardProps {
  gap: GapCandidate;
  projectId: number;
  selected: boolean;
  onSelect?: (gap: GapCandidate) => void;
}

/** 单条 GAP 卡（可展开）。展开见论断 + 支撑/反证证据。 */
function GapCard({ gap, projectId, selected, onSelect }: GapCardProps) {
  const [open, setOpen] = useState(false);
  const lens = LENS_META[gap.lens];
  const status = STATUS_META[gap.status];
  const verdict = gap.value_verdict ? VERDICT_META[gap.value_verdict.verdict] : null;

  return (
    <li className={`gap-card${selected ? " is-selected" : ""}`} data-gap-id={gap.gap_id}>
      <button
        type="button"
        className="gap-card-head"
        aria-expanded={open}
        onClick={() => {
          setOpen((v) => !v);
          onSelect?.(gap);
        }}
      >
        <span className="gap-card-top">
          <span className="gap-badges">
            <span className={`badge ${lens.cls}`}>{lens.label}</span>
            <span className={`badge ${status.cls}`}>{status.label}</span>
            {verdict && <span className={`badge ${verdict.cls}`}>{verdict.label}</span>}
          </span>
          <span className="gap-meta">
            <span className="gap-conf" title="LLM 自评，仅供排序，非价值裁决依据">
              置信 {gap.confidence.toFixed(2)}
            </span>
            <span className="gap-caret" aria-hidden="true">
              {open ? "▾" : "▸"}
            </span>
          </span>
        </span>
        <span className="gap-statement">{gap.statement}</span>
      </button>

      {open && (
        <div className="gap-card-body">
          <div className="gap-evi-group">
            <div className="gap-evi-label">支撑证据 · {gap.supporting_papers.length}</div>
            <ul className="gap-evi-list">
              {gap.supporting_papers.map((sp) => (
                <li className="gap-evi-row" key={`${sp.paper_id}-${sp.anchor_id}`}>
                  <blockquote className="gap-quote">{sp.quote}</blockquote>
                  <div className="gap-evi-src">
                    <a
                      className="gap-paper-link"
                      href={`/projects/${projectId}/library/${sp.paper_id}`}
                      title="打开文献详情"
                    >
                      Paper #{sp.paper_id}
                    </a>
                    <span className="gap-anchor-chip" data-anchor-id={sp.anchor_id} title="源坐标锚点">
                      {sp.anchor_id}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          </div>

          {gap.counter_evidence.length > 0 && (
            <div className="gap-evi-group gap-evi-counter">
              <div className="gap-evi-label">
                <span className="badge badge-warn">反证</span> 张力证据 · {gap.counter_evidence.length}
              </div>
              <ul className="gap-evi-list">
                {gap.counter_evidence.map((ce) => (
                  <li className="gap-evi-row" key={`${ce.paper_id}-${ce.anchor_id}`}>
                    <p className="gap-counter-note">{ce.note}</p>
                    <div className="gap-evi-src">
                      <a
                        className="gap-paper-link"
                        href={`/projects/${projectId}/library/${ce.paper_id}`}
                        title="打开文献详情"
                      >
                        Paper #{ce.paper_id}
                      </a>
                      <span className="gap-anchor-chip" data-anchor-id={ce.anchor_id} title="源坐标锚点">
                        {ce.anchor_id}
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

export function GapPanel({ projectId, gaps, isLoading, error, onSelectGap, selectedGapId }: GapPanelProps) {
  // 按 theme 分组（concept-centric 骨架）；保持发现顺序
  const grouped = useMemo(() => {
    const m = new Map<string, GapCandidate[]>();
    for (const g of gaps) {
      const arr = m.get(g.theme) ?? [];
      arr.push(g);
      m.set(g.theme, arr);
    }
    return Array.from(m.entries());
  }, [gaps]);

  return (
    <section className="gap-panel" aria-label="研究空白发现">
      <header className="gap-panel-head">
        <div>
          <h3 className="gap-panel-title">研究空白发现</h3>
          <p className="gap-panel-sub">
            按主题簇组织的结构化 GAP；置信度为 LLM 自评、仅供排序，
            <strong>价值由确定性核验给出</strong>（非 LLM 拍脑袋）。
          </p>
        </div>
        {!isLoading && !error && gaps.length > 0 && (
          <span className="gap-panel-count">{gaps.length} 条</span>
        )}
      </header>

      {isLoading && (
        <div className="state">
          <Loading label="发现研究空白中…" />
        </div>
      )}
      {error && <ErrMsg error={error} />}

      {!isLoading && !error && gaps.length === 0 && (
        <div className="gap-empty" role="note">
          <div className="gap-empty-mark" aria-hidden="true">
            ○
          </div>
          <p className="gap-empty-title">尚未发现研究空白</p>
          <p className="gap-empty-sub">运行 GAP 发现后，agent 累积的结构化空白将在此按主题呈现。</p>
        </div>
      )}

      {!isLoading &&
        !error &&
        grouped.map(([theme, items]) => (
          <div className="gap-theme" key={theme}>
            <h4 className="gap-theme-head">
              <span className="gap-theme-dot" aria-hidden="true" />
              <span className="gap-theme-name">{theme}</span>
              <span className="gap-theme-count">{items.length}</span>
            </h4>
            <ul className="gap-list">
              {items.map((g) => (
                <GapCard
                  key={g.gap_id}
                  gap={g}
                  projectId={projectId}
                  selected={selectedGapId === g.gap_id}
                  onSelect={onSelectGap}
                />
              ))}
            </ul>
          </div>
        ))}
    </section>
  );
}
