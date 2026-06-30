/**
 * SearchCandidateCards — P2-T4
 *
 * 渲染 Agent SearchTool emit 的候选文献列表。
 * 每条可勾选；底部两个操作按钮：
 *   - "加入文献库"（defaultStatus=candidate）
 *   - "加入并纳入"（defaultStatus=included）
 * 调 useAddFromSearch，成功后显示 imported/skipped 反馈，清空选择。
 */
import { useState, useCallback, useEffect } from "react";
import type { SearchCandidate } from "../api/client";
import { useAddFromSearch } from "../api/agentHooks";

interface Props {
  projectId: number;
  candidates: SearchCandidate[];
  /** 本次检索关键词，来自 AgentSearchResultsEvent.query */
  query?: string;
  /** 同一 run 内累计检索轮数。>1 时头部显示累计语义，避免误解为单轮结果。 */
  searchCount?: number;
  /** 最近一轮返回的候选数。 */
  latestCount?: number;
}

interface ImportFeedback {
  imported: number;
  skipped: number;
  failed: number;
}

export function SearchCandidateCards({ projectId, candidates, query, searchCount, latestCount }: Props) {
  // 默认全选
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(candidates.map((c) => c.candidate_id)),
  );
  const [feedback, setFeedback] = useState<ImportFeedback | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // C: 候选集合变化时（同 run 内第二次检索）重置为全选新候选，清除旧选择残留
  const candidateKey = candidates.map((c) => c.candidate_id).join(",");
  useEffect(() => {
    setSelected(new Set(candidates.map((c) => c.candidate_id)));
    setFeedback(null);
    setActionError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidateKey]);

  const { mutateAsync, isPending } = useAddFromSearch(projectId);

  // 切换单条勾选
  const toggleCandidate = useCallback((id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  const handleImport = useCallback(
    async (defaultStatus: "candidate" | "included") => {
      if (isPending) return;
      const chosen = candidates.filter((c) => selected.has(c.candidate_id));
      if (chosen.length === 0) return;

      setFeedback(null);
      setActionError(null);

      try {
        const result = await mutateAsync({ pid: projectId, candidates: chosen, defaultStatus });
        setFeedback({ imported: result.imported, skipped: result.skipped, failed: result.failed ?? 0 });
        // 入库后清空选择（已入库条目不再高亮）
        setSelected(new Set());
      } catch (e) {
        setActionError(e instanceof Error ? e.message : "入库失败，请重试");
      }
    },
    [candidates, selected, isPending, mutateAsync, projectId],
  );

  if (candidates.length === 0) {
    return (
      <div className="candidate-cards-empty" role="status">
        暂无候选
      </div>
    );
  }

  const selectedCount = selected.size;
  const hasSelection = selectedCount > 0;

  return (
    <div className="candidate-cards" aria-label={`检索候选文献 ${candidates.length} 篇`}>
      <div className="candidate-cards-header">
        <span className="candidate-cards-title">
          {searchCount && searchCount > 1 ? (
            <>
              累计 <strong>{searchCount}</strong> 轮检索，去重后 <strong>{candidates.length}</strong> 篇候选
              {query && (
                <span className="candidate-cards-subtitle">
                  最近：{query}（{latestCount ?? 0} 篇）
                </span>
              )}
            </>
          ) : query ? (
            <>检索「<strong>{query}</strong>」找到 <strong>{candidates.length}</strong> 篇候选</>
          ) : (
            <>检索到 <strong>{candidates.length}</strong> 篇候选文献</>
          )}
        </span>
        <span className="candidate-cards-sel-hint muted">
          {hasSelection ? `已选 ${selectedCount} 篇` : "未选"}
        </span>
      </div>

      <ul className="candidate-list" role="list">
        {candidates.map((c) => {
          const isChecked = selected.has(c.candidate_id);
          const authorStr =
            c.authors && c.authors.length > 0
              ? c.authors.slice(0, 3).join("，") + (c.authors.length > 3 ? " 等" : "")
              : "—";

          return (
            <li key={c.candidate_id} className="candidate-item card" data-candidate-id={c.candidate_id}>
              <label className="candidate-item-label">
                <input
                  type="checkbox"
                  className="candidate-checkbox"
                  checked={isChecked}
                  data-candidate-id={c.candidate_id}
                  onChange={() => toggleCandidate(c.candidate_id)}
                  aria-label={`选择：${c.title}`}
                />
              </label>

              <div className="candidate-body">
                <div className="candidate-title">
                  {c.url ? (
                    <a
                      href={c.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      aria-label={`打开来源链接：${c.title}`}
                      className="candidate-title-link"
                    >
                      {c.title}
                    </a>
                  ) : (
                    <span>{c.title}</span>
                  )}
                </div>

                <div className="candidate-meta">
                  {c.year && <span className="candidate-year">{c.year}</span>}
                  {c.containerTitle && (
                    <span className="candidate-journal">{c.containerTitle}</span>
                  )}
                  <span className="candidate-authors muted">{authorStr}</span>
                </div>

                <div className="candidate-badges">
                  {c.source && (
                    <span className="badge badge-soft candidate-source-badge">
                      {c.provider === "sciverse" || c.source === "sciverse" ? "Sciverse" : "OpenAlex"}
                    </span>
                  )}
                  {c.citedByCount != null && c.citedByCount > 0 && (
                    <span
                      className="badge badge-soft candidate-cited-badge"
                      title={`被引 ${c.citedByCount} 次`}
                    >
                      被引 {c.citedByCount}
                    </span>
                  )}
                  {c.url && (
                    <a
                      href={c.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="badge badge-soft candidate-oa-link"
                      aria-label={`打开来源链接（DOI/来源）：${c.title}`}
                    >
                      DOI/来源 ↗
                    </a>
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ul>

      {/* 操作栏 */}
      <div className="candidate-actions" role="group" aria-label="候选入库操作">
        <button
          className="btn"
          disabled={!hasSelection || isPending}
          onClick={() => void handleImport("candidate")}
          aria-label="加入文献库（候选状态）"
        >
          {isPending ? <span className="spinner" /> : null}
          加入文献库
        </button>
        <button
          className="btn btn-primary"
          disabled={!hasSelection || isPending}
          onClick={() => void handleImport("included")}
          aria-label="加入并纳入（included 状态）"
        >
          {isPending ? <span className="spinner" /> : null}
          加入并纳入
        </button>
      </div>

      {/* 反馈区 */}
      {feedback && (
        <div
          className="candidate-feedback"
          role="status"
          aria-live="polite"
        >
          <span className="badge badge-ok">
            已导入 {feedback.imported} 篇
          </span>
          {feedback.skipped > 0 && (
            <span className="badge badge-soft muted">
              跳过 {feedback.skipped} 篇（已在库中）
            </span>
          )}
          {feedback.failed > 0 && (
            <span className="badge badge-soft state-err">
              失败 {feedback.failed} 篇
            </span>
          )}
        </div>
      )}
      {actionError && (
        <div className="candidate-error state-err" role="alert">
          {actionError}
        </div>
      )}
    </div>
  );
}
