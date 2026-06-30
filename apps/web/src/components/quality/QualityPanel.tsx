/**
 * QualityPanel.tsx — 语料质检面板（F5）。
 *
 * 消费 GET /projects/{id}/quality-report：按 by_type 展示彩色计数 pill，issues 列表行
 * 可点回链对应 paper。配色映射既有色板（DESIGN §3 裁定）：
 *   duplicate=朱砂(高危) / missing_metadata=金(警示) / not_parsed=靛蓝(中性待办)。
 * 404（尚未生成质检）静默降级（不渲染，不打断），与 TrustCard 同语义。
 */
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { getQualityReport, ApiError } from "../../api/client";

interface Props {
  projectId: number;
}

/** 问题类型 → 中文标签 + pill 修饰类（映射色板，见 styles ql-pill-*） */
const TYPE_META: Record<string, { label: string; cls: string }> = {
  duplicate: { label: "重复", cls: "ql-pill-dup" },
  missing_metadata: { label: "缺元数据", cls: "ql-pill-meta" },
  not_parsed: { label: "未解析", cls: "ql-pill-parse" },
};

function typeMeta(type: string): { label: string; cls: string } {
  return TYPE_META[type] ?? { label: type, cls: "ql-pill-other" };
}

export function QualityPanel({ projectId }: Props) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["quality-report", projectId],
    queryFn: () => getQualityReport(projectId),
    enabled: projectId > 0,
    retry: false,
  });

  // 尚未生成质检（404）→ 静默不渲染（不打断工作台）
  if (error instanceof ApiError && error.status === 404) return null;
  if (isLoading) return <div className="card ql-panel ql-panel-loading muted">加载语料质检…</div>;
  if (error || !data) return null;

  const entries = Object.entries(data.by_type).filter(([, n]) => n > 0);
  const problemTotal = entries.reduce((s, [, n]) => s + n, 0);

  return (
    <div className="card ql-panel" aria-label="语料质检面板">
      <div className="ql-head">
        <h3 className="ql-title">语料质检</h3>
        <span className="ql-summary muted">
          共 <span className="tnum">{data.total}</span> 篇 · 问题{" "}
          <span className="tnum">{problemTotal}</span>
        </span>
      </div>

      {entries.length === 0 ? (
        <p className="ql-clean muted">未发现质量问题，语料整洁。</p>
      ) : (
        <div className="ql-pills">
          {entries.map(([type, n]) => {
            const m = typeMeta(type);
            return (
              <span key={type} className={`ql-pill ${m.cls}`} title={`${m.label}：${n} 篇`}>
                <span className="ql-pill-label">{m.label}</span>
                <span className="ql-pill-count tnum">{n}</span>
              </span>
            );
          })}
        </div>
      )}

      {data.issues.length > 0 && (
        <ul className="ql-issues">
          {data.issues.map((it, i) => {
            const m = typeMeta(it.type);
            return (
              <li key={`${it.paper_id}-${i}`} className="ql-issue">
                <span className={`badge ql-issue-tag ${m.cls}`}>{m.label}</span>
                <span className="ql-issue-detail">{it.detail}</span>
                <Link
                  className="ql-issue-link"
                  to={`/projects/${projectId}/library/${it.paper_id}`}
                  title="打开该文献"
                >
                  文献 #{it.paper_id}
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
