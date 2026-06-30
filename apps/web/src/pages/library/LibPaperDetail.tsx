/**
 * LibPaperDetail.tsx — 文献库右栏详情
 *
 * 复用 usePaper hook，渲染标题/作者/DOI/摘要/标签/状态。
 * 内嵌于三栏右栏，非独立路由页面（不含回退按钮）。
 * P3-T4: 结构化抽取卡（extraction 字段）。
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { usePaper } from "../../api/agentHooks";
import type { Creator, PaperExtractionDto } from "../../api/client";
import { getPaperMarkdown } from "../../api/client";
import { renderMarkdown } from "../../lib/markdown";
import { ErrMsg, Loading, formatCreators } from "../../lib/ui";

const INCLUSION_ZH: Record<string, string> = {
  candidate: "待筛选",
  included: "已纳入",
  excluded: "已排除",
  maybe: "待定",
};

/** 结构化抽取五字段标签 */
const EXTRACTION_FIELDS: { key: keyof PaperExtractionDto; label: string }[] = [
  { key: "researchQuestion", label: "研究问题" },
  { key: "method", label: "研究方法" },
  { key: "findings", label: "主要结论" },
  { key: "dataset", label: "数据集" },
  { key: "contribution", label: "学术贡献" },
];

/** 结构化抽取卡（P3-T4） */
function ExtractionCard({ extraction }: { extraction: PaperExtractionDto | null | undefined }) {
  if (extraction === undefined) return null;

  return (
    <div className="lib-detail-section lib-extraction-card" aria-label="AI 结构化抽取">
      <div className="lib-detail-section-label" style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
        <span>AI 结构化抽取 · 由 MinerU 全文驱动</span>
        {extraction === null && (
          <span
            style={{
              fontSize: "0.75rem",
              color: "var(--ink-3)",
              fontWeight: 400,
            }}
          >
            （尚未 AI 解析）
          </span>
        )}
      </div>
      {extraction === null ? (
        <p style={{ margin: 0, fontSize: "0.85rem", color: "var(--ink-3)", fontStyle: "italic" }}>
          点击工具栏「AI 解析（结构化）」可对本文献进行结构化抽取。
        </p>
      ) : (
        <>
        <p style={{ margin: "0 0 0.5rem", fontSize: "0.72rem", color: "var(--ink-3)" }}>
          基于 MinerU 解析的全文，由 LLM 抽取以下五要素。
        </p>
        <dl className="lib-extraction-dl">
          {EXTRACTION_FIELDS.map(({ key, label }) => (
            <div key={key} className="lib-extraction-field">
              <dt className="lib-extraction-dt">{label}</dt>
              <dd className="lib-extraction-dd">
                {extraction[key] ? (
                  String(extraction[key])
                ) : (
                  <span style={{ color: "var(--ink-3)", fontStyle: "italic" }}>（未抽取）</span>
                )}
              </dd>
            </div>
          ))}
        </dl>
        </>
      )}
    </div>
  );
}

/** MinerU 解析全文（Markdown）折叠区：默认折叠，展开后才按需拉取（避免每次进详情读大文件）。 */
function MineruMarkdownSection({ pid, paperId }: { pid: number; paperId: number }) {
  const [open, setOpen] = useState(false);
  const { data, isLoading, error } = useQuery({
    queryKey: ["paperMarkdown", pid, paperId],
    queryFn: () => getPaperMarkdown(pid, paperId),
    enabled: open, // 仅展开后拉取
    staleTime: 5 * 60 * 1000,
  });

  return (
    <div className="lib-detail-section">
      <button
        type="button"
        className="lib-md-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="lib-md-caret" aria-hidden="true">{open ? "▾" : "▸"}</span>
        MinerU 解析全文（Markdown）
        {open && data?.available && (
          <span className="lib-md-meta">
            由 MinerU 解析 · 共 {data.length} 字符{data.truncated ? "（已截断预览）" : ""}
          </span>
        )}
      </button>
      {open && (
        <div className="lib-md-body">
          {isLoading && <Loading label="加载解析全文…" />}
          {error && <ErrMsg error={error} />}
          {data && !data.available && (
            <p style={{ margin: 0, fontSize: "0.85rem", color: "var(--ink-3)", fontStyle: "italic" }}>
              该文献暂无 MinerU 解析全文（可能未上传 PDF 或未完成 OCR）。
            </p>
          )}
          {data && data.available && (
            <div
              className="md lib-md-content"
              dangerouslySetInnerHTML={{ __html: renderMarkdown(data.markdown) }}
            />
          )}
        </div>
      )}
    </div>
  );
}

const STATUS_CLASS: Record<string, string> = {
  candidate: "lib-status-candidate",
  included: "lib-status-included",
  excluded: "lib-status-excluded",
  maybe: "lib-status-maybe",
};

interface Props {
  pid: number;
  paperId: number;
  /** 窄屏(<1100px)详情覆盖层的"返回列表"回调；桌面三栏布局不显示返回按钮 */
  onBack?: () => void;
}

export function LibPaperDetail({ pid, paperId, onBack }: Props) {
  const { data, isLoading, error } = usePaper(pid, paperId);

  if (isLoading) return <Loading label="加载详情…" />;
  if (error) return <ErrMsg error={error} />;
  if (!data) return null;

  return (
    <div className="lib-detail-inner">
      {onBack && (
        <button
          type="button"
          className="lib-detail-back btn btn-ghost"
          onClick={onBack}
          aria-label="返回文献列表"
        >
          ← 返回列表
        </button>
      )}
      {/* 状态徽章 */}
      <div style={{ marginBottom: "0.5rem" }}>
        <span className={`lib-status-badge ${STATUS_CLASS[data.inclusionStatus] ?? ""}`}>
          {INCLUSION_ZH[data.inclusionStatus] ?? data.inclusionStatus}
        </span>
      </div>

      {/* 标题 */}
      <h2 className="lib-detail-title">{data.title || "（无标题）"}</h2>

      {/* 元信息 */}
      <div className="lib-detail-meta">
        {data.creators && data.creators.length > 0 && (
          <div>{formatCreators(data.creators as Creator[])}</div>
        )}
        {data.doi && (
          <div>
            DOI:{" "}
            <a href={`https://doi.org/${data.doi}`} target="_blank" rel="noopener noreferrer">
              {data.doi}
            </a>
          </div>
        )}
      </div>

      {/* 摘要 */}
      {data.abstract && (
        <div className="lib-detail-section">
          <div className="lib-detail-section-label">摘要</div>
          <p style={{ margin: 0, lineHeight: 1.75, fontSize: "0.9rem", color: "var(--ink-2)" }}>
            {data.abstract}
          </p>
        </div>
      )}

      {/* 标签 */}
      {data.tags && data.tags.length > 0 && (
        <div className="lib-detail-section">
          <div className="lib-detail-section-label">标签</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "0.25rem" }}>
            {data.tags.map((t) => (
              <span key={t} className="tag-chip">{t}</span>
            ))}
          </div>
        </div>
      )}

      {/* 笔记（notes 可能是数组或字符串，兼容两种格式） */}
      {data.notes && (Array.isArray(data.notes) ? data.notes.length > 0 : !!data.notes) && (
        <div className="lib-detail-section">
          <div className="lib-detail-section-label">笔记</div>
          <div style={{ fontSize: "0.88rem", color: "var(--ink-2)", lineHeight: 1.65 }}>
            {Array.isArray(data.notes)
              ? (data.notes as string[]).map((n, i) => <p key={i} style={{ margin: "0.25rem 0" }}>{n}</p>)
              : String(data.notes)}
          </div>
        </div>
      )}

      {/* P3: MinerU 解析全文（Markdown）折叠区 —— 文档处理能力 UI 可见入口 */}
      <MineruMarkdownSection pid={pid} paperId={paperId} />

      {/* 结构化抽取卡（P3-T4）：extraction 字段在 API 返回时始终存在（null 表示未抽取） */}
      <ExtractionCard extraction={data.extraction} />
    </div>
  );
}
