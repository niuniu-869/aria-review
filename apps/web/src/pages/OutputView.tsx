/**
 * OutputView.tsx — 产出区（M5）
 *
 * 数据流闸门（同 AnalysisView）：
 *   - 无 activeCorpus 或 status≠ready → 显示「需先构建分析语料」提示
 *   - ready → 展示综述报告导出 / 引用导出
 *   - PRISMA / pin 工件汇集仅需 projectId，不受 corpus 闸门限制（F-22），始终渲染
 *
 * 已接线:
 *   1. 综述报告导出 — 复用 ReportPanel（A7: MD / HTML / DOCX + 标题/作者/章节勾选）
 *   2. 引用导出     — 复用 ReportPanel（已含 getCite + GB/T7714/APA/MLA）
 *   3. PRISMA       — 复用 PrismaPanel（只需 projectId）
 *   4. Pin 工件     — useArtifacts(pid, true) 列已 pin 综述 + ArtifactCard 展示
 *
 * TODO（M5 后续 / legacy 补迁清单 §10）:
 *   - PDF 导出：后端需加 /report?format=pdf 端点（LaTeX/pandoc PDF；按钮位留禁用态）
 *   - DOI 反向校验：POST /corpus/{id}/validate-doi（按钮位留禁用态）
 *   - 费用看板：Settings 页展示 token 消耗
 *   - PDF 全文抓取：ScreenPanel / 批量任务（未启动）
 *   - 批量翻译/总结：AiToolsPanel 批量模式（未启动）
 */

import { useState } from "react";
import { useParams } from "react-router-dom";
import { getPanelRCorpusId, useProject, useArtifacts } from "../api/agentHooks";
import { ArtifactCard } from "../components/ArtifactCard";
import { ArtifactCanvas } from "../components/ArtifactCanvas";
import { ReportPanel } from "../components/ReportPanel";
import { PrismaPanel } from "../components/PrismaPanel";
import { ProjectGate } from "../components/ProjectGate";
import type { ArtifactItem } from "../api/client";
import { getArtifactCanvasContent, useArtifactContent } from "../hooks/useArtifactContent";
import { ErrMsg, Loading } from "../lib/ui";

// ---------------------------------------------------------------------------
// 无 corpus 时的闸门提示（与 AnalysisView 保持一致的 UX 基调）
// ---------------------------------------------------------------------------

function NoCorpusGate() {
  return (
    <div className="card placeholder-zone" style={{ margin: "1.5rem" }}>
      <h3>需先构建分析语料</h3>
      <p style={{ fontSize: "0.88rem", color: "var(--ink-3)" }}>
        请前往「分析」页，点击「构建分析语料」，成功后产出区即可解锁报告导出。
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 已 Pin 工件汇集（可选展示）
// ---------------------------------------------------------------------------

interface PinnedArtifactsProps {
  projectId: number;
}

function PinnedArtifacts({ projectId }: PinnedArtifactsProps) {
  const { data, isLoading, error, refetch } = useArtifacts(projectId, true);
  const [canvas, setCanvas] = useState<ArtifactItem | null>(null);
  const contentState = useArtifactContent(projectId, canvas);
  const canvasContent = getArtifactCanvasContent(canvas, contentState);
  const canvasEvidenceRefs = contentState.data?.evidenceRefs ?? null;

  const pinned = data?.artifacts ?? [];
  // 有缓存数据时后台刷新失败不阻断列表（stale-while-error），只在首载失败时展示错误
  const displayError = error && data == null
    ? Object.assign(new Error("已 Pin 综述工件加载失败，请重试。"), {
        originalMessage: error instanceof Error ? error.message : String(error),
      })
    : null;

  if (isLoading) {
    return (
      <section className="card" style={{ marginBottom: "1.5rem" }}>
        <h3 style={{ marginTop: 0, fontSize: "0.95rem" }}>已 Pin 综述工件</h3>
        <Loading label="加载已 Pin 综述工件…" />
      </section>
    );
  }

  if (displayError) {
    return (
      <section className="card" style={{ marginBottom: "1.5rem" }}>
        <h3 style={{ marginTop: 0, fontSize: "0.95rem" }}>已 Pin 综述工件</h3>
        <ErrMsg
          error={displayError}
          action={
            <button type="button" className="btn btn-ghost" onClick={() => void refetch()}>
              重试
            </button>
          }
        />
      </section>
    );
  }

  if (pinned.length === 0) return null;

  return (
    <section className="card" style={{ marginBottom: "1.5rem" }}>
      <h3 style={{ marginTop: 0, fontSize: "0.95rem" }}>已 Pin 综述工件</h3>
      <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
        {pinned.map((a) => (
          <ArtifactCard
            key={a.id}
            artifact={a}
            projectId={projectId}
            onExpand={(art) => setCanvas(art)}
          />
        ))}
      </div>

      {canvas && (
        <>
          {canvas.runId && Number(canvas.runId) > 0 && contentState.error && (
            <div className="card" style={{ marginTop: "0.75rem" }}>
              <span style={{ color: "var(--danger)" }}>加载工件内容失败。</span>
              <button
                type="button"
                className="btn btn-ghost"
                style={{ marginLeft: "0.75rem" }}
                onClick={contentState.retry}
              >
                重试
              </button>
            </div>
          )}
          <ArtifactCanvas
            artifact={canvas}
            projectId={projectId}
            content={canvasContent}
            evidenceRefs={canvasEvidenceRefs}
            onClose={() => setCanvas(null)}
          />
        </>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// OutputView 主体
// ---------------------------------------------------------------------------

export function OutputView() {
  const { pid } = useParams<{ pid: string }>();
  const pidNum = Number(pid);
  const project = useProject(pidNum > 0 ? pidNum : 0);

  const activeCorpus = project.data?.activeCorpus ?? null;
  const corpusReady = activeCorpus?.status === "ready";
  const rCorpusId = getPanelRCorpusId(activeCorpus);
  const projectIdStr = pidNum > 0 ? String(pidNum) : "";

  return (
    <ProjectGate project={project}>
      <div className="container" style={{ padding: "1.5rem" }}>
        {/* 顶部简述 */}
        <h2 style={{ marginBottom: "0.25rem" }}>产出区</h2>
        <p
          className="muted"
          style={{ fontSize: "0.85rem", marginBottom: "1.5rem", color: "var(--ink-3)" }}
        >
          汇集综述报告、引用列表与 PRISMA 流程图；Agent 运行产出的已 Pin 工件同步展示于此。
        </p>

        {/* 数据流闸门：无 ready corpus 时显示引导提示 */}
        {!corpusReady && <NoCorpusGate />}

        {/* ---- ready 时展示报告/引用导出能力 ---- */}
        {corpusReady && (
          /* 1 & 2. 综述报告导出 + 引用导出（复用 ReportPanel） */
          <div className="card" style={{ marginBottom: "1.5rem" }}>
            {/* A7: ReportPanel 已含真实 MD/HTML/DOCX 导出 + 标题/作者/章节勾选 */}
            <ReportPanel projectId={projectIdStr} corpusId={rCorpusId} />

            {/* TODO M5-legacy: PDF 导出 — 后端需 /report?format=pdf 端点 (LaTeX/pandoc PDF) */}
            <div style={{ marginTop: "1rem", display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
              <button
                type="button"
                className="btn btn-ghost"
                disabled
                title="即将支持 — 需后端 PDF 端点"
                style={{ opacity: 0.5, cursor: "not-allowed" }}
              >
                导出 PDF（即将支持）
              </button>
              {/* TODO M5-legacy: DOI 反向校验 — 后端需 POST /corpus/{id}/validate-doi */}
              <button
                type="button"
                className="btn btn-ghost"
                disabled
                title="即将支持 — DOI 反向校验"
                style={{ opacity: 0.5, cursor: "not-allowed" }}
              >
                DOI 校验（即将支持）
              </button>
            </div>
          </div>
        )}

        {/* 3. PRISMA 流程图（仅需 projectId，不受 corpus 闸门限制，F-22） */}
        <div className="card" style={{ marginBottom: "1.5rem" }}>
          <PrismaPanel projectId={projectIdStr} />
        </div>

        {/* 4. 已 Pin 工件汇集（来自 Agent 综述运行；仅需 projectId，不受 corpus 闸门限制） */}
        {pidNum > 0 && <PinnedArtifacts projectId={pidNum} />}
      </div>
    </ProjectGate>
  );
}
