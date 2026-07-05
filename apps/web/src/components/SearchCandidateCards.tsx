/**
 * SearchCandidateCards — P2-T4
 *
 * 渲染 Agent SearchTool emit 的候选文献列表。
 * 每条可勾选；底部两个操作按钮：
 *   - "加入文献库"（defaultStatus=candidate）
 *   - "加入并纳入"（defaultStatus=included）
 * 调 useAddFromSearch，成功后显示 imported/skipped 反馈，清空选择。
 */
import { useState, useCallback, useEffect, useRef, useMemo } from "react";
import type { FromSearchResult, SearchCandidate } from "../api/client";
import { useAddFromSearch, useBackfillFulltext } from "../api/agentHooks";
import { useSciverseSettings } from "../api/useSciverseSettings";

// 数据源 → 展示名。多源检索接入后，来源不再只有 openalex/sciverse；旧代码把非 sciverse
// 一律标 "OpenAlex" 会误标 core/europepmc/crossref 等（QA 实测 185/200 篇标错）。
const SOURCE_LABELS: Record<string, string> = {
  sciverse: "Sciverse",
  openalex: "OpenAlex",
  core: "CORE",
  europepmc: "EuropePMC",
  crossref: "Crossref",
  semantic: "Semantic Scholar",
  hal: "HAL",
  base: "BASE",
  unpaywall: "Unpaywall",
  upload: "上传",
};

function sourceLabel(c: SearchCandidate): string {
  // 跨源合并的候选优先显示所有涉及源（如 "CORE+OpenAlex"），如实反映多源来源。
  const merged = (c.mergedSources ?? []).filter(Boolean);
  if (merged.length > 1) {
    return merged.map((s) => SOURCE_LABELS[s] ?? s).join("+");
  }
  const key = (c.source || c.provider || "").toLowerCase();
  return SOURCE_LABELS[key] ?? (c.source || c.provider || "未知来源");
}

interface Props {
  projectId: number;
  candidates: SearchCandidate[];
  /** 本次检索关键词，来自 AgentSearchResultsEvent.query */
  query?: string;
  /** 同一 run 内累计检索轮数。>1 时头部显示累计语义，避免误解为单轮结果。 */
  searchCount?: number;
  /** 最近一轮返回的候选数。 */
  latestCount?: number;
  /** 上游限流/超时时为 true，表示候选可能不完整。 */
  partial?: boolean;
  partialReason?: string | null;
}

interface ImportFeedback {
  imported: number;
  skipped: number;
  failed: FromSearchResult["failed"];
  failedCount: number;
}

interface FulltextFeedback {
  status: "running" | "done" | "error";
  eligible: number;
  fetched: number;
  stillMetadataOnly: number;
  failed: number;
  message?: string;
}

export function SearchCandidateCards({
  projectId,
  candidates,
  query,
  searchCount,
  latestCount,
  partial = false,
  partialReason,
}: Props) {
  // 默认全选
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(candidates.map((c) => c.candidate_id)),
  );
  const previousCandidateIdsRef = useRef<Set<string>>(
    new Set(candidates.map((c) => c.candidate_id)),
  );
  const [feedback, setFeedback] = useState<ImportFeedback | null>(null);
  const [fulltextFeedback, setFulltextFeedback] = useState<FulltextFeedback | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const { settings: sciverse } = useSciverseSettings();
  const sciverseOptions = useMemo(() => ({
    apiToken: sciverse.apiToken || undefined,
    baseUrl: sciverse.baseUrl || undefined,
  }), [sciverse.apiToken, sciverse.baseUrl]);

  // C: 候选集合变化时仅默认勾选新候选，保留已有候选的人工选择状态。
  const candidateKey = candidates.map((c) => c.candidate_id).join(",");
  useEffect(() => {
    const currentCandidateIds = candidates.map((c) => c.candidate_id);
    const previousCandidateIds = previousCandidateIdsRef.current;

    setSelected((prev) => {
      const next = new Set<string>();
      currentCandidateIds.forEach((id) => {
        if (prev.has(id) || !previousCandidateIds.has(id)) {
          next.add(id);
        }
      });
      return next;
    });
    previousCandidateIdsRef.current = new Set(currentCandidateIds);
    setFeedback(null);
    setFulltextFeedback(null);
    setActionError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidateKey]);

  const { mutateAsync: addFromSearchAsync, isPending } = useAddFromSearch(projectId);
  const { mutateAsync: backfillFulltextAsync, isPending: isBackfillingFulltext } = useBackfillFulltext(projectId);

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
      if (isPending || isBackfillingFulltext) return;
      const chosen = candidates.filter((c) => selected.has(c.candidate_id));
      if (chosen.length === 0) return;
      if (
        defaultStatus === "included" &&
        chosen.length === candidates.length &&
        !window.confirm(`确认将 ${chosen.length} 篇全部加入并纳入？`)
      ) {
        return;
      }

      setFeedback(null);
      setFulltextFeedback(null);
      setActionError(null);

      try {
        const result = await addFromSearchAsync({ pid: projectId, candidates: chosen, defaultStatus });
        setFeedback({
          imported: result.imported,
          skipped: result.skipped,
          failed: result.failed ?? [],
          failedCount: result.failedCount ?? result.failed?.length ?? 0,
        });
        const eligibleIds = (result.fulltextEligiblePaperIds ?? []).filter((id) => Number.isFinite(id));
        const successfulCount = result.paperIds?.length ?? result.imported + result.skipped;
        const metadataOnlyCount = Math.max(0, successfulCount - eligibleIds.length);
        if (eligibleIds.length > 0) {
          setFulltextFeedback({
            status: "running",
            eligible: eligibleIds.length,
            fetched: 0,
            failed: 0,
            stillMetadataOnly: metadataOnlyCount,
          });
          try {
            const fulltext = await backfillFulltextAsync({
              paperIds: eligibleIds,
              maxPapers: Math.max(eligibleIds.length, 1),
              sciverse: sciverseOptions,
            });
            const stillMetadataOnly =
              metadataOnlyCount + (fulltext.failed?.length ?? 0) + Math.max(0, fulltext.remaining ?? 0);
            setFulltextFeedback({
              status: "done",
              eligible: eligibleIds.length,
              fetched: fulltext.fetched,
              failed: fulltext.failed?.length ?? 0,
              stillMetadataOnly,
            });
          } catch (e) {
            setFulltextFeedback({
              status: "error",
              eligible: eligibleIds.length,
              fetched: 0,
              failed: eligibleIds.length,
              stillMetadataOnly: metadataOnlyCount + eligibleIds.length,
              message: e instanceof Error ? e.message : "全文拉取失败，请稍后在文献库补全文",
            });
          }
        }
        // 入库后清空选择（已入库条目不再高亮）
        setSelected(new Set());
      } catch (e) {
        setActionError(e instanceof Error ? e.message : "入库失败，请重试");
      }
    },
    [candidates, selected, isPending, isBackfillingFulltext, addFromSearchAsync, projectId, backfillFulltextAsync, sciverseOptions],
  );

  const handleReselectFailed = useCallback(() => {
    if (!feedback) return;
    const candidateIds = new Set(candidates.map((c) => c.candidate_id));
    const failedIds = (feedback.failed ?? [])
      .map((item) => item.candidateId)
      .filter((id): id is string => !!id && candidateIds.has(id));
    setSelected(new Set(failedIds));
  }, [candidates, feedback]);

  if (candidates.length === 0) {
    return (
      <div className="candidate-cards-empty" role="status">
        {partial ? `检索被限流或超时，暂无候选${partialReason ? `：${partialReason}` : ""}` : "暂无候选"}
      </div>
    );
  }

  const selectedCount = candidates.filter((c) => selected.has(c.candidate_id)).length;
  const hasSelection = selectedCount > 0;
  const failedIdsInCandidates = feedback
    ? (feedback.failed ?? []).filter((item) =>
        item.candidateId ? candidates.some((c) => c.candidate_id === item.candidateId) : false
      )
    : [];

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
      {partial && (
        <div className="candidate-partial-note" role="status">
          检索被限流或超时，本次仅返回部分结果{partialReason ? `：${partialReason}` : ""}
        </div>
      )}

      <ul className="candidate-list" role="list">
        {candidates.map((c) => {
          const isChecked = selected.has(c.candidate_id);
          const hasSciverseFulltext = !!c.sciverseDocId?.trim();
          const hasOaPdf = !!c.pdfUrl?.trim();
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
                  {(c.source || c.provider) && (
                    <span className="badge badge-soft candidate-source-badge">
                      {sourceLabel(c)}
                    </span>
                  )}
                  <span
                    className={`badge badge-soft candidate-fulltext-badge${hasSciverseFulltext || hasOaPdf ? " is-fulltext" : ""}`}
                    title={
                      hasSciverseFulltext
                        ? "Sciverse 返回 doc_id，可拉取全文"
                        : hasOaPdf
                          ? "开放获取(OA) PDF 直链，导入后可安全下载解析全文"
                          : "仅题录元数据，无法直接用于研究空白精读"
                    }
                  >
                    {hasSciverseFulltext ? "含全文" : hasOaPdf ? "开放获取PDF" : "仅题录"}
                  </span>
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
          disabled={!hasSelection || isPending || isBackfillingFulltext}
          onClick={() => void handleImport("candidate")}
          aria-label="加入文献库（候选状态）"
        >
          {isPending || isBackfillingFulltext ? <span className="spinner" /> : null}
          加入文献库
        </button>
        <button
          className="btn btn-primary"
          disabled={!hasSelection || isPending || isBackfillingFulltext}
          onClick={() => void handleImport("included")}
          aria-label="加入并纳入（included 状态）"
        >
          {isPending || isBackfillingFulltext ? <span className="spinner" /> : null}
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
          {feedback.failedCount > 0 && (
            <span className="badge badge-soft state-err">
              失败 {feedback.failedCount} 篇
            </span>
          )}
          {feedback.failedCount > 0 && (
            <div className="candidate-failed-panel">
              <details className="candidate-failed-details">
                <summary>失败明细</summary>
                <ul>
                  {(feedback.failed ?? []).map((item, index) => (
                    <li key={`${item.candidateId ?? "failed"}-${index}`}>
                      <span className="candidate-failed-title">{item.title}</span>
                      <span className="candidate-failed-reason">{item.reason}</span>
                    </li>
                  ))}
                </ul>
              </details>
              <button
                className="btn"
                type="button"
                disabled={failedIdsInCandidates.length === 0}
                onClick={handleReselectFailed}
              >
                只重选失败项
              </button>
            </div>
          )}
        </div>
      )}
      {fulltextFeedback && (
        <div className="candidate-feedback candidate-fulltext-feedback" role="status" aria-live="polite">
          {fulltextFeedback.status === "running" ? (
            <span>
              <span className="spinner" /> 正在为 {fulltextFeedback.eligible} 篇候选拉取全文…
            </span>
          ) : fulltextFeedback.status === "done" ? (
            <>
              <span className="badge badge-ok">
                已为 {fulltextFeedback.fetched} 篇拉取全文
              </span>
              {fulltextFeedback.stillMetadataOnly > 0 && (
                <span className="badge badge-soft muted">
                  {fulltextFeedback.stillMetadataOnly} 篇仅题录，无法用于研究空白精读
                </span>
              )}
              {fulltextFeedback.failed > 0 && (
                <span className="badge badge-soft state-err">
                  全文失败 {fulltextFeedback.failed} 篇
                </span>
              )}
            </>
          ) : (
            <span className="state-err">
              {fulltextFeedback.message ?? "全文拉取失败，请稍后在文献库补全文"}
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
