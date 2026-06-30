/**
 * ScreeningMode.tsx — I/E/M 键盘快捷键筛选模式（全屏遮罩）
 *
 * 功能：
 * - 单篇聚焦：标题 + 摘要大字展示
 * - 键盘快捷键：I=纳入 / E=排除（触发排除理由弹层） / M=待定
 * - 按完自动跳下一篇
 * - 摘要按 researchQuestion 关键词分色高亮（客户端简单分词）
 * - 排除时弹中文排除理由下拉 → 调用 PATCH exclusionReason（后端支持）
 *
 * PATCH 字段实测：
 *   - exclusionReason: ✅ 支持（InclusionPatchRequest 有此字段，写入 DB exclusion_reason）
 *   - screeningNotes:  ❌ 未暴露（DB 有 screening_notes，但 REST 不接受此字段）
 *   → 排除理由直接写 exclusionReason（不做 screeningNotes 回退）
 */
import { useEffect, useRef, useState } from "react";
import type { Creator, InclusionStatus, ProjectPaperItem } from "../../api/client";
import { usePaper } from "../../api/agentHooks";
import { formatCreators } from "../../lib/ui";

/** 中文排除理由选项 */
const EXCLUSION_REASONS = [
  "研究设计不符",
  "非目标主题",
  "重复文献",
  "语言不符",
  "其他",
];

/** 从研究问题中提取关键词（简单空格/标点分词） */
function extractKeywords(researchQuestion: string): string[] {
  if (!researchQuestion) return [];
  // 按空格、标点分词，过滤长度 < 2 的词
  return researchQuestion
    .split(/[\s，。、；：！？,.;:!?]+/)
    .map((w) => w.trim())
    .filter((w) => w.length >= 2);
}

/** 将摘要文本按关键词列表高亮（返回 React 节点列表） */
function highlightAbstract(text: string, keywords: string[]): React.ReactNode {
  if (!text || keywords.length === 0) return text;
  // 构建正则，转义特殊字符
  const escaped = keywords.map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const re = new RegExp(`(${escaped.join("|")})`, "gi");
  const parts = text.split(re);
  return parts.map((part, i) => {
    const isMatch = escaped.some((e) => new RegExp(`^${e}$`, "i").test(part));
    return isMatch ? (
      <mark key={i} className="hl-keyword">{part}</mark>
    ) : (
      <span key={i}>{part}</span>
    );
  });
}

interface Props {
  paper: ProjectPaperItem;
  current: number;       // 0-based 当前索引
  total: number;
  researchQuestion: string;
  onDecide: (status: InclusionStatus, exclusionReason?: string) => Promise<void>;
  onClose: () => void;
}

interface PaperFullData {
  abstract?: string;
  creators?: Creator[];
  year?: number;
}

export function ScreeningMode({ paper, current, total, researchQuestion, onDecide, onClose }: Props) {
  // P1-5：从 URL 解析 pid，改用 usePaper（react-query）替换裸 fetch。
  // react-query 自带缓存、去竞态（键切换时旧 query 立即作废），消除"新标题+旧摘要"竞态。
  const pid = (() => {
    const m = window.location.pathname.match(/\/projects\/(\d+)/);
    return m ? Number(m[1]) : 0;
  })();

  const { data: paperDetail, isLoading: detailLoading } = usePaper(pid, paper.paperId);
  // P1-5：usePaper 返回 PaperDetail（含 abstract/creators）；year 不在 PaperDetail 中，
  // 通过 paper.year（ProjectPaperItem）兜底。加载中为 null，已完成但无数据为 {}。
  const detail: PaperFullData | null = detailLoading
    ? null
    : paperDetail
      ? { abstract: paperDetail.abstract ?? undefined, creators: paperDetail.creators as Creator[] | undefined }
      : {};
  const keywords = extractKeywords(researchQuestion);

  // 排除理由弹层状态
  const [showExclusion, setShowExclusion] = useState(false);
  const [exclusionReason, setExclusionReason] = useState(EXCLUSION_REASONS[0]);
  const [deciding, setDeciding] = useState(false);

  // 处理决策
  const decide = async (status: InclusionStatus, reason?: string) => {
    if (deciding) return;
    setDeciding(true);
    try {
      await onDecide(status, reason);
    } finally {
      setDeciding(false);
      setShowExclusion(false);
    }
  };

  // 键盘快捷键
  const containerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // 排除弹层打开时，只处理 Escape
      if (showExclusion) {
        if (e.key === "Escape") setShowExclusion(false);
        return;
      }
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement) return;
      switch (e.key.toLowerCase()) {
        case "i":
          e.preventDefault();
          void decide("included");
          break;
        case "e":
          e.preventDefault();
          setShowExclusion(true);
          break;
        case "m":
          e.preventDefault();
          void decide("maybe");
          break;
        case "escape":
          e.preventDefault();
          onClose();
          break;
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [showExclusion, deciding, decide, onClose]);

  // 焦点管理：打开时聚焦容器
  useEffect(() => {
    containerRef.current?.focus();
  }, []);

  return (
    <div className="screening-overlay" ref={containerRef} tabIndex={-1} role="dialog" aria-modal="true" aria-label="文献筛选模式">
      {/* 头部：进度 + 关闭 */}
      <div className="screening-header">
        <h3 style={{ margin: 0, fontFamily: "var(--serif)", fontSize: "1rem" }}>筛选模式</h3>
        <span className="screening-progress">
          {current + 1} / {total}
        </span>
        {/* 进度条 */}
        <div style={{ flex: 1, height: 4, background: "var(--line)", borderRadius: 2, overflow: "hidden" }}>
          <div
            style={{
              height: "100%",
              width: `${((current + 1) / total) * 100}%`,
              background: "var(--cinnabar)",
              transition: "width 0.3s",
            }}
          />
        </div>
        <button className="btn btn-ghost" onClick={onClose} aria-label="退出筛选模式">
          退出 <kbd className="kbd">Esc</kbd>
        </button>
      </div>

      {/* 正文：标题 + 作者 + 摘要（高亮关键词） */}
      <div className="screening-body">
        <h1 className="screening-title">{paper.title || "（无标题）"}</h1>
        <div className="screening-meta">
          {detail?.creators && detail.creators.length > 0 && (
            <span>{formatCreators(detail.creators)} · </span>
          )}
          {/* year 优先取 PaperDetail（如有），兜底用 ProjectPaperItem.year */}
          {(detail?.year ?? paper.year) && <span>{detail?.year ?? paper.year}</span>}
        </div>
        {detail === null ? (
          <p style={{ color: "var(--ink-3)" }}>加载摘要中…</p>
        ) : detail?.abstract ? (
          <div className="screening-abstract">
            {highlightAbstract(detail.abstract, keywords)}
          </div>
        ) : (
          <p style={{ color: "var(--ink-3)" }}>（暂无摘要）</p>
        )}
      </div>

      {/* 底部操作栏 */}
      <div className="screening-actions">
        <button
          className="btn btn-primary"
          disabled={deciding}
          onClick={() => void decide("included")}
          style={{ background: "var(--ok)", borderColor: "var(--ok)" }}
        >
          纳入 <kbd className="kbd">I</kbd>
        </button>
        <button
          className="btn btn-primary"
          disabled={deciding}
          onClick={() => setShowExclusion(true)}
          style={{ background: "var(--cinnabar-2)", borderColor: "var(--cinnabar-2)" }}
        >
          排除 <kbd className="kbd">E</kbd>
        </button>
        <button
          className="btn"
          disabled={deciding}
          onClick={() => void decide("maybe")}
          style={{ color: "var(--warn)", borderColor: "var(--warn)" }}
        >
          待定 <kbd className="kbd">M</kbd>
        </button>
        <div style={{ flex: 1 }} />
        <div className="screening-kbd-hint">
          快捷键：
          <kbd className="kbd">I</kbd> 纳入
          <kbd className="kbd">E</kbd> 排除
          <kbd className="kbd">M</kbd> 待定
          <kbd className="kbd">Esc</kbd> 退出
        </div>
      </div>

      {/* 排除理由弹层 */}
      {showExclusion && (
        <div className="exclusion-dialog" role="dialog" aria-modal="true" aria-label="选择排除理由">
          <div className="exclusion-dialog-card">
            <h3 className="exclusion-dialog-title">排除理由</h3>
            <label htmlFor="exclusion-reason-select" style={{ display: "block", marginBottom: "0.35rem", fontSize: "0.82rem", color: "var(--ink-2)" }}>
              请选择排除原因
            </label>
            <select
              id="exclusion-reason-select"
              className="input"
              value={exclusionReason}
              onChange={(e) => setExclusionReason(e.target.value)}
              autoFocus
            >
              {EXCLUSION_REASONS.map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
            <div className="exclusion-dialog-actions">
              <button className="btn btn-ghost" onClick={() => setShowExclusion(false)}>取消</button>
              <button
                className="btn btn-primary"
                disabled={deciding}
                onClick={() => void decide("excluded", exclusionReason)}
                style={{ background: "var(--cinnabar-2)", borderColor: "var(--cinnabar-2)" }}
              >
                确认排除
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
