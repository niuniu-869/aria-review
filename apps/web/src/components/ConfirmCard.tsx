// ConfirmCard — agent 写操作确认卡片 (P2-3)
// 让用户对 agent 的写工具调用做"批准/拒绝"。pending 时按钮禁用 + spinner。
import { useState } from "react";

interface Props {
  toolId: string;
  action: string;
  argsPreview: string;
  pending?: boolean;
  onApprove: () => void;
  onReject: () => void;
}

export function ConfirmCard({ toolId, action, argsPreview, pending, onApprove, onReject }: Props) {
  // 参数预览默认折叠(过长时截断), 点击可展开
  const [expanded, setExpanded] = useState(false);
  const TRUNCATE = 200;
  const long = argsPreview.length > TRUNCATE;
  const shown = expanded || !long ? argsPreview : argsPreview.slice(0, TRUNCATE) + "…";

  return (
    <div
      className="timeline-card tl-confirm"
      role="region"
      aria-label="写操作确认"
    >
      <div className="tl-label">需要确认写操作</div>
      <div className="confirm-head">
        <span className="confirm-tool">{toolId}</span>
        <span className="confirm-action">{action}</span>
      </div>
      <pre className="confirm-args" aria-label="操作参数预览">
        {shown}
      </pre>
      {long && (
        <button
          type="button"
          className="btn btn-ghost confirm-toggle"
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? "收起" : "展开全部"}
        </button>
      )}
      <div className="confirm-actions">
        <button
          type="button"
          className="btn btn-primary"
          disabled={pending}
          aria-label="批准写操作"
          onClick={onApprove}
        >
          {pending ? (
            <>
              <span className="spinner" />
              处理中
            </>
          ) : (
            "批准"
          )}
        </button>
        <button
          type="button"
          className="btn"
          disabled={pending}
          aria-label="拒绝写操作"
          onClick={onReject}
        >
          拒绝
        </button>
      </div>
    </div>
  );
}
