// TrustCard — Phase 2 竞赛门面：历史可见的「可信凭证 · 可验证运行日志」卡。
//
// 数据源：GET /projects/{pid}/agent/runs/{rid}/grounding（getGrounding）。
// 诚信约定：metrics.scoreable===false 时三率为 null → 如实显示「不可评分」，
//   绝不伪装 100%。哈希链/事件数恒可验证，照常显示。
// 容错：run 无 grounding（404）时静默返回 null（不渲染），不打断页面。
import { useState, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { getGrounding, getRunLog } from "../api/client";
import { ApiError } from "../api/client";
import { Loading, ErrMsg } from "../lib/ui";

interface Props {
  projectId: number;
  runId: number;
}

/** 比率 → 百分比文案；null → 「不可评分」（灰显，诚实标注，不伪装满分）。 */
function ratePct(value: number | null): { text: string; muted: boolean } {
  if (value === null || value === undefined) return { text: "不可评分", muted: true };
  return { text: `${Math.round(value * 100)}%`, muted: false };
}

/** sha 前 12 位 + 省略号（空则占位）。 */
function shortHash(h: string | undefined): string {
  if (!h) return "—";
  return `${h.slice(0, 12)}…`;
}

export function TrustCard({ projectId, runId }: Props) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["grounding", projectId, runId],
    queryFn: () => getGrounding(projectId, runId),
    enabled: projectId > 0 && runId > 0,
    retry: false,
  });

  const [copied, setCopied] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<Error | null>(null);

  const handleCopy = useCallback((label: string, text: string) => {
    void navigator.clipboard?.writeText(text).then(
      () => {
        setCopied(label);
        window.setTimeout(() => setCopied(null), 1500);
      },
      () => {
        /* 复制失败静默（无剪贴板权限时不打断） */
      },
    );
  }, []);

  // 下载 RunLog（逻辑搬自 AgentChat.handleDownloadRunLog）
  const handleDownload = useCallback(async () => {
    if (downloading) return;
    setDownloading(true);
    setDownloadError(null);
    try {
      const log = await getRunLog(projectId, String(runId));
      const json = `${JSON.stringify(log, null, 2)}\n`;
      JSON.parse(json);
      const blob = new Blob([json], { type: "application/json;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `runlog_${runId}.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setDownloadError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setDownloading(false);
    }
  }, [projectId, runId, downloading]);

  // run 无 grounding（404）→ 静默不渲染（不打断其它内容渲染）
  if (error instanceof ApiError && error.status === 404) return null;

  if (isLoading) {
    return (
      <div className="card trust-card">
        <Loading label="加载可信凭证…" />
      </div>
    );
  }
  if (error) {
    return (
      <div className="card trust-card">
        <ErrMsg error={error} />
      </div>
    );
  }
  if (!data) return null;

  const { manifest, metrics } = data;
  const chainOk = !!manifest.chainHead;
  const zero = ratePct(metrics.zeroFabricationRate);
  const acc = ratePct(metrics.groundingAccuracy);
  const prov = ratePct(metrics.provenanceHitRate);

  return (
    <div className="card trust-card" aria-label="可信凭证卡">
      <div className="trust-head">
        {/* 克制的单色盾形符号（非彩色 emoji） */}
        <span className="trust-shield" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M12 2 4 5v6c0 5 3.5 8.5 8 11 4.5-2.5 8-6 8-11V5l-8-3Z" />
            <path d="m9 12 2 2 4-4" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
        <h3 className="trust-title">可信凭证 · 可验证运行日志</h3>
      </div>

      {!metrics.scoreable && (
        <div className="trust-notice" role="note">
          本次运行未产生引用证据，grounding 指标不可评分（如实标注，未伪装满分）。
        </div>
      )}

      <div className="trust-tiles">
        <div className="trust-tile">
          <div className="trust-tile-value">
            {manifest.eventCount}
            <span className={chainOk ? "trust-chk ok" : "trust-chk"} aria-hidden="true">
              {chainOk ? "✓" : "—"}
            </span>
          </div>
          <div className="trust-tile-label">哈希链事件{chainOk ? "（完整）" : ""}</div>
        </div>

        <div className="trust-tile">
          <div className={`trust-tile-value${zero.muted ? " muted" : ""}`}>{zero.text}</div>
          <div className="trust-tile-label">零伪造率</div>
        </div>

        <div className="trust-tile">
          <div className={`trust-tile-value${acc.muted ? " muted" : ""}`}>{acc.text}</div>
          <div className="trust-tile-label">grounding 准确率</div>
        </div>

        <div className="trust-tile">
          <div className={`trust-tile-value${prov.muted ? " muted" : ""}`}>{prov.text}</div>
          <div className="trust-tile-label">溯源命中率</div>
        </div>

        <div className="trust-tile">
          <div className="trust-tile-value">{metrics.evidenceCount}</div>
          <div className="trust-tile-label">证据条目</div>
        </div>
      </div>

      <div className="trust-foot">
        <button
          type="button"
          className="trust-hash"
          onClick={() => handleCopy("doc", manifest.contentSha256)}
          title="点击复制完整哈希"
        >
          源文档 content_sha256: <code>{shortHash(manifest.contentSha256)}</code>
          {copied === "doc" && <span className="trust-copied"> 已复制</span>}
        </button>
        <button
          type="button"
          className="trust-hash"
          onClick={() => handleCopy("chain", manifest.chainHead)}
          title="点击复制完整链头"
        >
          链头: <code>{shortHash(manifest.chainHead)}</code>
          {copied === "chain" && <span className="trust-copied"> 已复制</span>}
        </button>
      </div>

      <div className="trust-verify">
        可在容器内 <code>python scripts/verify_runlog.py</code> 独立复核哈希链 / 零伪造 / 溯源。
      </div>

      {downloadError && <ErrMsg error={downloadError} />}

      <div className="trust-actions">
        <button
          type="button"
          className="btn btn-ghost"
          disabled={downloading}
          onClick={() => void handleDownload()}
        >
          {downloading ? (
            <>
              <span className="spinner" /> 下载中
            </>
          ) : (
            "下载 RunLog"
          )}
        </button>
      </div>
    </div>
  );
}
