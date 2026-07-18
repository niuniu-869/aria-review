// RunHistory — 历史运行只读区（F-07）
// 进入对话页时展示最近已完成 run 的用户指令 + finalOutput（默认折叠），
// 避免导航离开后对话内容完全消失。数据源：runs 列表（父级筛出最近 done 的至多 3 条）
// + getRun 详情（与 useRun 同一 queryKey，复用缓存）；纯只读，不触碰 SSE 流状态。
import { useQueries } from "@tanstack/react-query";
import { getRun } from "../api/client";

interface Props {
  projectId: number;
  /** 最近 done 的 runId（至多 3 条，新→旧）。 */
  runIds: number[];
}

export function RunHistory({ projectId, runIds }: Props) {
  const results = useQueries({
    queries: runIds.map((runId) => ({
      queryKey: ["run", projectId, String(runId)],
      queryFn: () => getRun(projectId, String(runId)),
      enabled: projectId > 0,
    })),
  });
  // 无 finalOutput 的 run 不展示（如检索 run 产出走候选卡，不在此回溯）；
  // 全部无产出时整块隐藏，不留空标题。
  const entries = results
    .map((r) => r.data)
    .filter((d) => !!d?.finalOutput);
  if (entries.length === 0) return null;
  return (
    <div className="run-history" aria-label="历史运行">
      <div className="run-history-title">历史运行</div>
      {entries.map((d) => (
        <details key={String(d?.runId)} className="run-history-item">
          <summary>{d?.prompt?.trim() || "（无标题）"}</summary>
          <div className="run-history-output">{d?.finalOutput}</div>
        </details>
      ))}
    </div>
  );
}
