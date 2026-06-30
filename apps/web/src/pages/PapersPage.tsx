import { useParams } from "react-router-dom";
import { usePatchInclusion, useProjectPapers } from "../api/agentHooks";
import type { InclusionStatus, ProjectPaperItem } from "../api/client";
import { ErrMsg, Loading } from "../lib/ui";

// 修复1: 枚举与后端对齐 candidate/included/excluded/maybe
const INCLUSION_LABELS: Record<InclusionStatus, string> = {
  candidate: "待筛选",
  included: "已纳入",
  excluded: "已排除",
  maybe: "待定",
};

function InclusionSelect({ pid, item }: { pid: number; item: ProjectPaperItem }) {
  const patch = usePatchInclusion(pid);
  return (
    <select
      value={item.inclusionStatus}
      disabled={patch.isPending}
      style={{ fontSize: "0.82rem", padding: "0.25rem 0.4rem" }}
      onChange={(e) =>
        patch.mutate({ paperId: item.paperId, inclusionStatus: e.target.value as InclusionStatus })
      }
    >
      <option value="candidate">{INCLUSION_LABELS.candidate}</option>
      <option value="included">{INCLUSION_LABELS.included}</option>
      <option value="excluded">{INCLUSION_LABELS.excluded}</option>
      <option value="maybe">{INCLUSION_LABELS.maybe}</option>
    </select>
  );
}

export function PapersPage() {
  const { pid } = useParams<{ pid: string }>();
  const pidNum = Number(pid);
  const { data, isLoading, error } = useProjectPapers(pidNum);

  if (isLoading) return <Loading label="加载文献列表…" />;
  if (error) return <ErrMsg error={error} />;
  if (!data || data.papers.length === 0)
    return <p className="muted">暂无文献，请通过 Agent 导入文献。</p>;

  return (
    <div>
      <table className="tbl">
        <thead>
          <tr>
            <th>标题</th>
            <th>年份</th>
            <th>评分</th>
            <th>纳排状态</th>
          </tr>
        </thead>
        <tbody>
          {data.papers.map((p) => (
            <tr key={p.paperId}>
              <td style={{ maxWidth: 360 }}>{p.title}</td>
              <td className="tnum">{p.year ?? "—"}</td>
              <td className="tnum">{p.screeningScore != null ? p.screeningScore.toFixed(2) : "—"}</td>
              <td>
                <InclusionSelect pid={pidNum} item={p} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
