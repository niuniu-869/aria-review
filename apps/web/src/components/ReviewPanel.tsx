import { useEffect, useRef } from "react";
import { Link, useInRouterContext } from "react-router-dom";
import type { LlmRequestOptions } from "../api/client";
import type { ProjectDetail } from "../api/agentHooks";
import type { RCorpusId } from "../api/corpusIds";
import { useProjectFulltextBackfill } from "../hooks/useProjectFulltextBackfill";
import { REVIEW_TYPES, useReviewJob } from "../hooks/useReviewJob";
import { ReviewWithProvenance } from "./review/ReviewWithProvenance";
import { downloadMarkdown } from "../lib/download";
import { track } from "../lib/track";
import {
  AiPanel,
  AiToolbar,
  AiField,
  AiTextInput,
  AiMarkdown,
  AiEmpty,
  AiError,
  AiKeyNotice,
} from "./ai";

export function ReviewPanel({
  projectId,
  corpusId,
  // M5: 可选 LLM 配置 prop，由父组件从 useLlmSettings 读取后注入（不改内部逻辑）
  llm,
  apiKey,
  projectStats,
}: {
  projectId: string;
  corpusId?: RCorpusId;
  llm?: LlmRequestOptions;
  apiKey?: string;
  projectStats?: Pick<ProjectDetail, "includedCount" | "readableFulltextCount">;
}) {
  const {
    type,
    setType,
    topic,
    setTopic,
    running,
    text,
    summary,
    annotated,
    provenanceMap,
    err,
    precheck,
    jobId,
    exportText,
    generate,
  } = useReviewJob({ projectId, corpusId, llm, apiKey, projectStats });
  const pid = Number(projectId);
  const fulltextBackfill = useProjectFulltextBackfill(pid);

  // 引导卡 CTA：应用内（有 Router）走 SPA <Link> 保留内存态/query cache；
  // 单测无 Router 上下文时回退纯 <a>，既不破坏测试又不整页刷新（codex P1-review P2）。
  const inRouter = useInRouterContext();

  // ── 0.6.1 P0 漏斗埋点（best-effort，不影响渲染）──────────────────────
  // 面板曝光：每次挂载记一次。
  useEffect(() => {
    track("review_view", undefined, pid);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // precheck 拦截：reason 变为新的非空值时记一次（区分缺纳入 / 缺全文）。
  const lastBlocked = useRef<string | null>(null);
  useEffect(() => {
    if (precheck) {
      if (precheck.reason !== lastBlocked.current) {
        lastBlocked.current = precheck.reason;
        track("review_precheck_blocked", { reason: precheck.reason }, pid);
      }
    } else {
      lastBlocked.current = null;
    }
  }, [precheck, pid]);
  // 终态：一次生成的 running 由 true→false 时，按 err 判定成功/失败，每个 job 只记一次。
  // 挂载时恢复的历史 job（running 恒 false）不触发，避免误记。
  const prevRunning = useRef(false);
  const firedTerminalFor = useRef<number | null>(null);
  useEffect(() => {
    if (prevRunning.current && !running && jobId && firedTerminalFor.current !== jobId) {
      firedTerminalFor.current = jobId;
      track(err ? "review_job_failed" : "review_job_done", { jobId }, pid);
    }
    prevRunning.current = running;
  }, [running, jobId, err, pid]);

  function exportMarkdown() {
    if (!exportText) return;
    const typeLabel = REVIEW_TYPES.find(([v]) => v === type)?.[1] ?? type;
    // 剥离溯源锚点包裹标记(保留内部 [n] 引用), 否则原始 [[anchor:...]] 串泄漏进导出文本。
    const clean = exportText
      .replace(/\[\[anchor:[A-Za-z0-9_-]+\]\]/g, "")
      .replace(/\[\[\/anchor\]\]/g, "");
    downloadMarkdown(
      `AI综述-${typeLabel}-${projectId}`,
      `# AI综述导出\n\n- 论型：${typeLabel}\n- 主题：${topic || "未填写"}\n\n${clean}\n`,
    );
  }

  async function runFulltextBackfill() {
    track("review_backfill_click", {}, pid);
    try {
      const result = await fulltextBackfill.run();
      track("review_backfill_done", {
        succeeded: result.fetched,
        failed: result.failed.length,
      }, pid);
    } catch {
      // 具体错误由卡内反馈；埋点只记录有聚合结果的正常完成。
    }
  }

  const fulltextBackfillError = fulltextBackfill.error instanceof Error
    ? fulltextBackfill.error.message
    : fulltextBackfill.error
      ? String(fulltextBackfill.error)
      : null;
  const noFulltextBackfilled = !!fulltextBackfill.result
    && fulltextBackfill.result.fetched === 0;

  return (
    <AiPanel title="AI 综述写作" intro="按论型与主题流式生成综述，并对引用做语料核验。">
      <AiToolbar>
        <AiField label="论型" htmlFor="review-type">
          <select id="review-type" className="input" value={type} onChange={(e) => setType(e.target.value)}>
            {REVIEW_TYPES.map(([v, label]) => (
              <option key={v} value={v}>
                {label}
              </option>
            ))}
          </select>
        </AiField>
        <AiField label="研究主题" htmlFor="review-topic">
          <AiTextInput
            id="review-topic"
            value={topic}
            placeholder="例：人工智能在教育中的应用"
            onChange={(e) => setTopic(e.target.value)}
          />
        </AiField>
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => {
            track("review_generate_click", { type }, pid);
            void generate();
          }}
          disabled={!topic.trim() || running || !!precheck}
          title={precheck ? precheck.message : undefined}
        >
          {running ? "生成中…" : "生成综述"}
        </button>
        <button type="button" className="btn" onClick={exportMarkdown} disabled={!exportText}>
          导出 Markdown
        </button>
      </AiToolbar>

      {/* P1: precheck 从一行 muted 小字升级为醒目引导卡——综述一键可达，但缺纳入/全文时
          要让新手一眼看到"下一步去哪"，并直达文献库补齐（不做任何自动批量副作用）。 */}
      {precheck && (
        <div className="review-precheck" role="status">
          <span className="review-precheck-badge" aria-hidden="true">下一步</span>
          <div className="review-precheck-text">
            <p className="review-precheck-title">{precheck.message}</p>
            <p className="review-precheck-detail">{precheck.detail}</p>
          </div>
          <div className="review-precheck-actions">
            {precheck.reason === "no_fulltext" && (
              <>
                <button
                  type="button"
                  className="btn btn-primary review-precheck-cta"
                  onClick={() => void runFulltextBackfill()}
                  disabled={fulltextBackfill.isPending}
                >
                  {fulltextBackfill.isPending ? "自动补全文中…" : "自动补全文"}
                </button>
                {fulltextBackfill.isPending && (
                  <span className="review-precheck-progress" aria-live="polite">
                    {fulltextBackfill.progress
                      ? `已处理 ${fulltextBackfill.progress.done}/${fulltextBackfill.progress.total}`
                      : "正在查找可补全文文献…"}
                  </span>
                )}
                {noFulltextBackfilled && !fulltextBackfillError && (
                  <span className="review-precheck-error" role="alert">
                    {(fulltextBackfill.result?.failed.length ?? 0) > 0
                      ? `自动补全文失败 ${fulltextBackfill.result?.failed.length ?? 0} 篇，请手动导入 PDF。`
                      : "未找到可自动补全文的文献，请手动导入 PDF。"}
                  </span>
                )}
                {fulltextBackfillError && (
                  <span className="review-precheck-error" role="alert">
                    自动补全文失败：{fulltextBackfillError}
                  </span>
                )}
              </>
            )}
            {inRouter ? (
              <Link
                className={`btn review-precheck-cta${precheck.reason === "no_included" ? " btn-primary" : ""}`}
                to={precheck.href}
              >
                {precheck.action}
              </Link>
            ) : (
              <a
                className={`btn review-precheck-cta${precheck.reason === "no_included" ? " btn-primary" : ""}`}
                href={precheck.href}
              >
                {precheck.action}
              </a>
            )}
          </div>
        </div>
      )}

      <AiKeyNotice hasKey={!!(llm?.apiKey || apiKey)} />

      <AiError message={err} />

      {/* 引用校验图例: 文字徽标 + 计数, 取代正文里难辨的裸 emoji */}
      {summary && (
        <div className="cite-legend" aria-live="polite">
          <span className="muted">引用校验</span>
          <span className="lg-item">
            <span className="badge badge-ok cite-mark" title="DOI/PMID 精确命中语料">已核验</span>
            <span className="lg-count tnum">{summary.green}</span>
          </span>
          <span className="lg-item">
            <span className="badge badge-warn cite-mark" title="作者+年模糊命中, 或编号待人工复核">待核</span>
            <span className="lg-count tnum">{summary.yellow}</span>
          </span>
          <span className="lg-item">
            <span className="badge badge-danger cite-mark" title="语料中未找到, 疑似虚构">存疑</span>
            <span className="lg-count tnum">{summary.red}</span>
          </span>
        </div>
      )}

      {/* dogfood A2: 轻量可信卡 —— 综述产出处直接呈现零伪造率+溯源覆盖(诚实空态)，
          不再让"可信"只停留在首页营销区。完整 grounding 指标(哈希链等)在历史 run 的 TrustCard。 */}
      {summary && (() => {
        const g = summary.green ?? 0;
        const y = summary.yellow ?? 0;
        const r = summary.red ?? 0;
        const total = g + y + r;
        const zeroFab = total > 0 ? Math.round(((g + y) / total) * 100) : null;
        const provCount = provenanceMap ? Object.keys(provenanceMap).length : 0;
        return (
          <div
            aria-live="polite"
            style={{ display: "flex", alignItems: "center", gap: "0.6rem", margin: "0.1rem 0 0.7rem" }}
          >
            <span
              className={`badge ${zeroFab === null ? "badge-warn" : r > 0 ? "badge-danger" : "badge-ok"}`}
              title="零伪造率 =（已核验+待核）/全部引用；红色为语料中找不到的伪造引用。无引用时不可评分；存在伪造时标红（codex A2-P3）。"
            >
              零伪造率 {zeroFab === null ? "不可评分" : `${zeroFab}%`}
            </span>
            <span className="muted" style={{ fontSize: "0.8rem" }}>
              共 {total} 处引用{provCount > 0 ? ` · ${provCount} 处可点击溯源原文` : ""}
            </span>
          </div>
        );
      })()}

      {/* 有溯源映射 → 可溯源综述(点引用跳原文页/段)优先，reviewMd 取 annotated||text；
          否则 annotated → 带引用徽标 markdown；再否则 text → 流式 markdown */}
      {provenanceMap && Object.keys(provenanceMap).length > 0 ? (
        <div className="ai-review-body">
          <ReviewWithProvenance
            projectId={Number(projectId)}
            reviewMd={annotated || text}
            provenanceMap={provenanceMap}
          />
        </div>
      ) : annotated ? (
        <div className="ai-review-body">
          <AiMarkdown content={annotated} projectId={projectId} />
        </div>
      ) : text ? (
        <div className="ai-review-body">
          <AiMarkdown content={text} streaming live projectId={projectId} />
        </div>
      ) : (
        !running && !err && <AiEmpty>填写研究主题并点击「生成综述」开始。</AiEmpty>
      )}
    </AiPanel>
  );
}
