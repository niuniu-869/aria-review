/**
 * ArtifactCard — 工件卡片组件（M4）
 *
 * 在 AgentChat 运行完成后，run_complete 事件产出的 final_output 以工件卡形式呈现。
 * 提供：
 *   - 类型徽章（综述/分析/抽取/文献集）
 *   - 标题 + 操作（展开 Canvas / pin / 重跑）
 *   - pin 状态持久化（调后端 artifacts 端点）
 *
 * 注意：内容本身派生自 final_output（不可变审计源），工件 id 是后端持久化的身份标识。
 */
import { useState, useCallback } from "react";
import type { ArtifactItem } from "../api/client";
import { usePatchArtifact } from "../api/agentHooks";

// 类型 → 中文标签映射
const TYPE_LABELS: Record<string, string> = {
  review: "综述",
  analysis: "分析",
  extraction: "抽取",
  paperset: "文献集",
};

// 类型 → CSS class 映射（学术宣纸风格）
const TYPE_BADGE_CLASS: Record<string, string> = {
  review: "badge badge-ok",
  analysis: "badge badge-warn",
  extraction: "badge",
  paperset: "badge",
};

interface Props {
  artifact: ArtifactItem;
  projectId: number;
  /** 点击「展开」回调 → 唤起 ArtifactCanvas */
  onExpand: (artifact: ArtifactItem) => void;
  /** 点击「重跑」回调（可选，传入时才显示按钮） */
  onRerun?: (artifact: ArtifactItem) => void;
}

export function ArtifactCard({ artifact, projectId, onExpand, onRerun }: Props) {
  const [pinning, setPinning] = useState(false);
  const patchArtifact = usePatchArtifact(projectId);

  const handlePin = useCallback(async () => {
    if (pinning) return;
    setPinning(true);
    try {
      await patchArtifact.mutateAsync({ aid: artifact.id, pinned: !artifact.pinned });
    } finally {
      setPinning(false);
    }
  }, [artifact.id, artifact.pinned, pinning, patchArtifact]);

  const typeLabel = TYPE_LABELS[artifact.type] ?? artifact.type;
  const badgeClass = TYPE_BADGE_CLASS[artifact.type] ?? "badge";

  return (
    <div className="artifact-card card" data-testid="artifact-card">
      {/* 类型徽章 + 标题行 */}
      <div className="artifact-card-header">
        <span className={badgeClass} title={`工件类型: ${artifact.type}`}>
          {typeLabel}
        </span>
        <span className="artifact-title" title={artifact.title}>
          {artifact.title || "(无标题)"}
        </span>
      </div>

      {/* 操作行 */}
      <div className="artifact-card-actions">
        {/* 展开 Canvas */}
        <button
          className="btn btn-ghost"
          onClick={() => onExpand(artifact)}
          title="在 Canvas 中展开查看（含 grounding 溯源）"
        >
          展开
        </button>

        {/* Pin / Unpin */}
        <button
          className={`btn btn-ghost ${artifact.pinned ? "artifact-pinned" : ""}`}
          disabled={pinning}
          onClick={() => void handlePin()}
          title={artifact.pinned ? "取消 pin" : "Pin 工件（跨会话保留）"}
          aria-pressed={artifact.pinned}
        >
          {artifact.pinned ? "已 Pin" : "Pin"}
        </button>

        {/* 重跑（可选） */}
        {onRerun && (
          <button
            className="btn btn-ghost"
            onClick={() => onRerun(artifact)}
            title="重新运行此 agent 指令"
          >
            重跑
          </button>
        )}
      </div>

      {/* 用户标注（若有） */}
      {artifact.userAnnotation && (
        <div className="artifact-annotation muted" style={{ fontSize: "0.82rem", marginTop: "0.4rem" }}>
          {artifact.userAnnotation}
        </div>
      )}
    </div>
  );
}
