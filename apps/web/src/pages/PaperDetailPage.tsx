import { useParams } from "react-router-dom";
import { usePaper } from "../api/agentHooks";
import type { Creator } from "../api/client";
import { ErrMsg, Loading, formatCreators } from "../lib/ui";

// 修复1: 枚举与后端对齐 candidate/included/excluded/maybe
const INCLUSION_ZH: Record<string, string> = {
  candidate: "待筛选",
  included: "已纳入",
  excluded: "已排除",
  maybe: "待定",
};

export function PaperDetailPage() {
  const { pid, paperId } = useParams<{ pid: string; paperId: string }>();
  const pidNum = Number(pid);
  const paperIdNum = Number(paperId);
  const { data, isLoading, error } = usePaper(pidNum, paperIdNum);

  if (isLoading) return <Loading label="加载文献详情…" />;
  if (error) return <ErrMsg error={error} />;
  if (!data) return null;

  return (
    <div className="card" style={{ maxWidth: 720 }}>
      <h2 style={{ marginTop: 0 }}>{data.title}</h2>
      <table style={{ borderCollapse: "collapse", fontSize: "0.9rem", marginBottom: "1rem" }}>
        <tbody>
          {data.creators && data.creators.length > 0 && (
            <tr>
              <td style={{ color: "var(--ink-3)", paddingRight: "1rem", whiteSpace: "nowrap" }}>作者</td>
              <td>{formatCreators(data.creators as Creator[])}</td>
            </tr>
          )}
          {data.doi && (
            <tr>
              <td style={{ color: "var(--ink-3)", paddingRight: "1rem" }}>DOI</td>
              <td>
                <a href={`https://doi.org/${data.doi}`} target="_blank" rel="noopener noreferrer">
                  {data.doi}
                </a>
              </td>
            </tr>
          )}
          <tr>
            <td style={{ color: "var(--ink-3)", paddingRight: "1rem" }}>纳排状态</td>
            <td>{INCLUSION_ZH[data.inclusionStatus] ?? data.inclusionStatus}</td>
          </tr>
        </tbody>
      </table>
      {data.abstract && (
        <div>
          <div style={{ fontSize: "0.78rem", color: "var(--ink-3)", fontWeight: 600, marginBottom: "0.4rem" }}>
            摘要
          </div>
          <p style={{ margin: 0, lineHeight: 1.7, fontSize: "0.92rem", color: "var(--ink-2)" }}>
            {data.abstract}
          </p>
        </div>
      )}
      {data.tags && data.tags.length > 0 && (
        <div style={{ marginTop: "1rem", display: "flex", gap: "0.4rem", flexWrap: "wrap" }}>
          {data.tags.map((t) => (
            <span key={t} className="badge badge-soft">{t}</span>
          ))}
        </div>
      )}
    </div>
  );
}
