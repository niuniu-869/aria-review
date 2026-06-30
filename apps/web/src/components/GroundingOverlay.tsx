/**
 * GroundingOverlay — 综述 grounding 溯源叠加层（M4）
 *
 * grounding 粒度说明（诚实文档）：
 *   evidence_refs 中每条 EvidenceRef 包含：
 *     - span:       原文中的引用字符串（如 "Smith (2020)"、"10.xxx/doi"）
 *     - claim:      包含该引用的上下文句子（GuardedStream 填充，可能为 null）
 *     - paper_id:   语料行号（1-based）
 *     - match_quality: green / yellow
 *   对应的 record 来自 RunLog.evidence_refs + paper DB（需要前端查询 /papers）
 *
 *   因此 grounding 粒度为「句级」（来自 claim 字段）——能到哪句引用了哪篇 paper。
 *   不能精确到 char offset；若 claim 为 null 则降级到「工件级」（列出所有 evidence）。
 *
 *   TODO: 若 claim 字段因 GuardedStream 未设置为 null，这里只显示引用列表，
 *         不做句子高亮；等 GuardedStream 填充 claim 后可升级为句级高亮。
 *
 * 使用方式：
 *   <GroundingOverlay
 *     evidenceRefs={runLog.evidence_refs}
 *     markdownHtml={renderedHtml}
 *   />
 */
import { useState } from "react";

/** EvidenceRef 的前端表示（从 RunLog.evidence_refs 反序列化） */
export interface FrontendEvidenceRef {
  paper_id: number;
  span?: string | null;
  claim?: string | null;
  cite_type?: string;
  match_quality: string; // green | yellow
  record_hash?: string;
  corpus_id?: string;
}

/** 从 RunLog 中提取 paper 信息（RunLog.run 的 evidence_refs 里没有完整题录，
 *  只有 record_hash；实际 paper 标题需要从 agent run detail 的 evidence_refs 里读取。
 *  当前实现：显示 span + claim + match_quality，paper 标题显示 "Paper #paper_id"。
 *  TODO: 接入 /projects/{pid}/papers/{paper_id} 端点取完整题录。
 */

interface EvidenceGroup {
  claim: string | null;
  refs: FrontendEvidenceRef[];
}

function groupByClaim(refs: FrontendEvidenceRef[]): EvidenceGroup[] {
  // 按 claim 聚合（null claim 合并为一组）
  const map = new Map<string, FrontendEvidenceRef[]>();
  const NULL_KEY = "__null__";
  for (const ref of refs) {
    const key = ref.claim ?? NULL_KEY;
    if (!map.has(key)) map.set(key, []);
    map.get(key)!.push(ref);
  }
  const groups: EvidenceGroup[] = [];
  // null 组放最后
  for (const [key, groupRefs] of map.entries()) {
    if (key !== NULL_KEY) {
      groups.push({ claim: key, refs: groupRefs });
    }
  }
  if (map.has(NULL_KEY)) {
    groups.push({ claim: null, refs: map.get(NULL_KEY)! });
  }
  return groups;
}

interface EvidenceRefPopoverProps {
  projectId?: number;
  refs: FrontendEvidenceRef[];
  onClose: () => void;
}

function EvidenceRefPopover({ projectId, refs, onClose }: EvidenceRefPopoverProps) {
  return (
    <div className="grounding-popover card" role="tooltip" aria-live="polite">
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "0.4rem" }}>
        <span style={{ fontWeight: 600, fontSize: "0.85rem" }}>引用溯源</span>
        <button
          className="btn btn-ghost"
          style={{ padding: "0 0.3rem", fontSize: "0.75rem" }}
          onClick={(e) => {
            // codex M4-P2#3: 阻止冒泡到父 GroundedClaim 的 onClick(否则关闭后立即重新打开)
            e.stopPropagation();
            onClose();
          }}
          aria-label="关闭溯源面板"
        >
          ×
        </button>
      </div>
      <ul style={{ margin: 0, paddingLeft: "1.2rem", fontSize: "0.82rem" }}>
        {refs.map((ref, i) => (
          <li key={i} style={{ marginBottom: "0.5rem" }}>
            {/* 匹配质量徽章 */}
            <span
              className={ref.match_quality === "green" ? "badge badge-ok" : "badge badge-warn"}
              style={{ marginRight: "0.4rem" }}
            >
              {ref.match_quality === "green" ? "已核验" : "待核"}
            </span>
            {/* paper id（TODO: 接入完整题录后替换为 title） */}
            {projectId ? (
              <a
                href={`/projects/${projectId}/library/${ref.paper_id}`}
                style={{ fontWeight: 500 }}
                title="打开文献详情"
              >
                Paper #{ref.paper_id}
              </a>
            ) : (
              <span style={{ fontWeight: 500 }}>Paper #{ref.paper_id}</span>
            )}
            {/* 引用 span */}
            {ref.span && (
              <span style={{ color: "var(--ink-3)", marginLeft: "0.4rem" }}>
                "{ref.span}"
              </span>
            )}
            {/* 引用类型 */}
            {ref.cite_type && (
              <span style={{ color: "var(--ink-3)", marginLeft: "0.3rem", fontSize: "0.75rem" }}>
                [{ref.cite_type}]
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

interface GroundedClaimProps {
  projectId?: number;
  group: EvidenceGroup;
  quality: "green" | "yellow" | "mixed";
}

function GroundedClaim({ projectId, group, quality }: GroundedClaimProps) {
  const [open, setOpen] = useState(false);
  const borderColor =
    quality === "green" ? "var(--color-ok)" : quality === "yellow" ? "var(--color-warn)" : "#aaa";

  return (
    <span
      className="grounding-claim"
      style={{
        borderBottom: `2px solid ${borderColor}`,
        cursor: "pointer",
        position: "relative",
        display: "inline",
      }}
      role="button"
      tabIndex={0}
      aria-expanded={open}
      onClick={() => setOpen((v) => !v)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") setOpen((v) => !v);
      }}
      title="点击查看引用溯源"
    >
      {group.claim}
      {open && (
        <EvidenceRefPopover
          projectId={projectId}
          refs={group.refs}
          onClose={() => setOpen(false)}
        />
      )}
    </span>
  );
}

interface Props {
  evidenceRefs: FrontendEvidenceRef[] | null | undefined;
  /** 综述的 Markdown 渲染后 HTML（用于无 claim 时回退渲染） */
  markdownHtml: string;
  projectId?: number;
}

export function GroundingOverlay({ evidenceRefs, markdownHtml, projectId }: Props) {
  if (!evidenceRefs || evidenceRefs.length === 0) {
    // 无 evidence：直接渲染原 markdown
    return (
      <div
        className="md grounding-overlay"
        dangerouslySetInnerHTML={{ __html: markdownHtml }}
      />
    );
  }

  const groups = groupByClaim(evidenceRefs);
  const claimGroups = groups.filter((g) => g.claim !== null);
  const noClaimRefs = groups.find((g) => g.claim === null)?.refs ?? [];

  // 若有 claim：尝试在 markdownHtml 中定位并替换为高亮 span。
  // 由于 dangerouslySetInnerHTML 不允许混合 React 节点，这里采用「列出已溯源句子」
  // 作为注释侧栏的方式，不破坏原 markdown 渲染。
  // TODO: 升级为 remark/rehype 插件实现真正的句内高亮。
  return (
    <div className="grounding-overlay-wrapper">
      {/* 原 markdown 正文 */}
      <div
        className="md"
        dangerouslySetInnerHTML={{ __html: markdownHtml }}
      />

      {/* Grounding 侧注 */}
      {(claimGroups.length > 0 || noClaimRefs.length > 0) && (
        <div className="grounding-sidebar">
          <div
            style={{
              fontSize: "0.78rem",
              fontWeight: 600,
              color: "var(--ink-3)",
              marginBottom: "0.5rem",
            }}
          >
            引用溯源（{evidenceRefs.length} 处）
          </div>

          {/* 按 claim 聚合展示 */}
          {claimGroups.map((g, i) => {
            // red 已被 GuardedStream 拦截，不进 evidence_refs，因此只有 green/yellow
            const allGreen = g.refs.every((r) => r.match_quality === "green");
            const quality: "green" | "yellow" | "mixed" = allGreen
              ? "green"
              : g.refs.some((r) => r.match_quality === "green")
              ? "mixed"
              : "yellow";
            return (
              <div key={i} className="grounding-claim-row" style={{ marginBottom: "0.6rem" }}>
                <GroundedClaim projectId={projectId} group={g} quality={quality} />
              </div>
            );
          })}

          {/* 无 claim 的 evidence（降级：只列 paper） */}
          {noClaimRefs.length > 0 && (
            <div className="grounding-noclaim">
              <div style={{ fontSize: "0.75rem", color: "var(--ink-3)", marginBottom: "0.3rem" }}>
                以下引用上下文缺失，已按 paper 列出：
                {/* TODO: GuardedStream 未填充 claim → 段级降级；等 claim 填充后可升级句级 */}
              </div>
              <ul style={{ margin: 0, paddingLeft: "1.2rem", fontSize: "0.8rem" }}>
                {noClaimRefs.map((ref, i) => (
                  <li key={i}>
                    <span
                      className={
                        ref.match_quality === "green" ? "badge badge-ok" : "badge badge-warn"
                      }
                      style={{ marginRight: "0.3rem" }}
                    >
                      {ref.match_quality === "green" ? "已核验" : "待核"}
                    </span>
                    {projectId ? (
                      <a href={`/projects/${projectId}/library/${ref.paper_id}`} title="打开文献详情">
                        Paper #{ref.paper_id}
                      </a>
                    ) : (
                      <>Paper #{ref.paper_id}</>
                    )}
                    {ref.span && <span style={{ color: "var(--ink-3)" }}> "{ref.span}"</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
