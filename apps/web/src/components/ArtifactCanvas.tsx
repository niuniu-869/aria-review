/**
 * ArtifactCanvas — 工件全高 Canvas（右侧可折叠面板，M4）
 *
 * 展示综述全文 + GroundingOverlay（引用溯源）。
 * 内容来源：
 *   - artifact.contentRef = "run:{runId}" → 取 RunLog.run.final_output 渲染
 *   - 若 artifact 直接携带了 content（通过 extraContent prop）→ 直接渲染
 *
 * grounding 数据来源：extraEvidenceRefs（从 AgentChat 的 run_complete 事件取 final_output，
 * 以及从 RunDetail.evidenceRefs 取 evidence_refs）。
 *
 * 不重写 RunTimeline/AgentChat 内部逻辑，在外层包裹读取产出。
 */
import { useCallback } from "react";
import { renderMarkdown } from "../lib/markdown";
import { downloadMarkdown } from "../lib/download";
import type { ArtifactItem } from "../api/client";
import { GroundingOverlay } from "./GroundingOverlay";
import type { FrontendEvidenceRef } from "./GroundingOverlay";
import { usePatchArtifact, useProjectPapers } from "../api/agentHooks";

interface Props {
  /** 当前展开的工件（null = 关闭） */
  artifact: ArtifactItem | null;
  projectId: number;
  /** 工件正文内容（由外层从 run_complete.final_output 传入） */
  content: string | null;
  /** evidence_refs（由外层从 RunDetail/RunLog 传入） */
  evidenceRefs?: FrontendEvidenceRef[] | null;
  /** 关闭 Canvas */
  onClose: () => void;
}

export function ArtifactCanvas({
  artifact,
  projectId,
  content,
  evidenceRefs,
  onClose,
}: Props) {
  const patchArtifact = usePatchArtifact(projectId);
  const { data: paperData } = useProjectPapers(projectId);
  const citationRefs = (paperData?.papers ?? [])
    .filter((p) => p.inclusionStatus === "included")
    .map((p, i) => ({
      index: i + 1,
      projectId,
      paperId: p.paperId,
      title: p.title,
    }));

  const handleRename = useCallback(async () => {
    if (!artifact) return;
    const newTitle = window.prompt("重命名工件标题：", artifact.title);
    if (newTitle && newTitle !== artifact.title) {
      await patchArtifact.mutateAsync({ aid: artifact.id, title: newTitle });
    }
  }, [artifact, patchArtifact]);

  if (!artifact) return null;

  const markdownHtml = content
    ? renderMarkdown(content, { citationRefs, projectId })
    : "<p class='muted'>（暂无内容）</p>";

  return (
    <div className="artifact-canvas" role="complementary" aria-label="工件 Canvas">
      {/* 顶部工具栏 */}
      <div className="artifact-canvas-header">
        <div className="artifact-canvas-title">
          <span className="badge badge-ok" style={{ marginRight: "0.5rem" }}>
            {artifact.type === "review" ? "综述" : artifact.type}
          </span>
          <span
            style={{ fontWeight: 600, cursor: "pointer" }}
            onClick={() => void handleRename()}
            title="点击重命名"
          >
            {artifact.title || "(无标题)"}
          </span>
        </div>
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
          {/* pin 状态显示 */}
          {artifact.pinned && (
            <span className="badge" style={{ fontSize: "0.72rem" }}>已 Pin</span>
          )}
          <button
            className="btn btn-ghost"
            disabled={!content}
            onClick={() => {
              if (!content) return;
              downloadMarkdown(artifact.title || `artifact-${artifact.id}`, content);
            }}
          >
            导出 Markdown
          </button>
          {/* 关闭按钮 */}
          <button
            className="btn btn-ghost"
            onClick={onClose}
            aria-label="关闭 Canvas"
            style={{ fontWeight: 600 }}
          >
            ×
          </button>
        </div>
      </div>

      {/* 正文 + grounding */}
      <div className="artifact-canvas-body">
        <GroundingOverlay
          evidenceRefs={evidenceRefs}
          markdownHtml={markdownHtml}
          projectId={projectId}
        />
      </div>

      {/* 用户标注区 */}
      {artifact.userAnnotation && (
        <div
          className="artifact-canvas-annotation card"
          style={{ margin: "0.75rem", fontSize: "0.82rem" }}
        >
          <div style={{ fontWeight: 600, color: "var(--ink-3)", marginBottom: "0.2rem" }}>
            标注
          </div>
          {artifact.userAnnotation}
        </div>
      )}
    </div>
  );
}
