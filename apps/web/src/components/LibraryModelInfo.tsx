/**
 * LibraryModelInfo.tsx — 文献库模型说明弹层（Task 5）
 *
 * 说明"全局共享库 + 项目纳排"模型（spec §4.2）。
 * 无障碍: role="dialog" / aria-modal / ESC / 点击外部关闭 / focus trap。
 */
import { useEffect, useRef } from "react";

interface Props {
  open: boolean;
  onClose: () => void;
  /** 弹层触发按钮的 DOM ref（用于关闭后复焦） */
  triggerRef?: React.RefObject<HTMLButtonElement | null>;
}

/** 返回 container 内所有可聚焦元素 */
function getFocusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(
    container.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])',
    ),
  ).filter((el) => !el.hasAttribute("disabled") && el.tabIndex !== -1);
}

export function LibraryModelInfo({ open, onClose, triggerRef }: Props) {
  const dialogRef = useRef<HTMLDivElement>(null);

  /** 统一关闭 handler：先复焦触发按钮，再调 onClose（ESC/关闭按钮/遮罩点击共用）。 */
  const handleClose = () => {
    triggerRef?.current?.focus();
    onClose();
  };

  // ESC 关闭 + focus trap
  useEffect(() => {
    if (!open) return;
    const dialog = dialogRef.current;

    // 打开时聚焦对话框
    dialog?.focus();

    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        handleClose();
        return;
      }

      // focus trap — 捕获 Tab / Shift+Tab，首尾循环
      if (e.key === "Tab" && dialog) {
        const focusable = getFocusableElements(dialog);
        if (focusable.length === 0) {
          e.preventDefault();
          return;
        }
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        const active = document.activeElement as HTMLElement;

        if (e.shiftKey) {
          // Shift+Tab：如果焦点在第一个，跳到最后一个
          if (active === first || active === dialog) {
            e.preventDefault();
            last.focus();
          }
        } else {
          // Tab：如果焦点在最后一个（或 dialog 本身），跳到第一个
          if (active === last || active === dialog) {
            e.preventDefault();
            first.focus();
          }
        }
      }
    };

    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, handleClose]);

  if (!open) return null;

  return (
    <>
      {/* 遮罩层，点击关闭（复用统一 handleClose，确保复焦触发按钮） */}
      <div
        className="lib-model-info-overlay"
        onClick={handleClose}
        aria-hidden="true"
      />
      {/* 弹层 */}
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="文献库说明"
        aria-describedby="lib-model-info-desc"
        className="lib-model-info-dialog"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="lib-model-info-header">
          <h3 style={{ margin: 0, fontSize: "0.95rem", fontFamily: "var(--serif)" }}>
            文献库模型说明
          </h3>
          <button
            className="lib-model-info-close"
            onClick={handleClose}
            aria-label="关闭说明"
          >
            ✕
          </button>
        </div>
        <div id="lib-model-info-desc" className="lib-model-info-body">
          <section>
            <h4>全局共享库</h4>
            <p>
              所有文献题录（Paper）存储在全局共享库中，按 DOI / 标题去重。
              同一篇文献可被多个项目复用，避免重复下载与存储。
            </p>
          </section>
          <section>
            <h4>项目纳排</h4>
            <p>
              每个项目只保存自己的<strong>纳排标注</strong>（已纳入 / 待筛选 / 已排除 / 待定）
              与<strong>分析语料</strong>快照。项目并不"拥有"文献，而是对全局共享库的论文
              做筛选决策，生成专属的分析子集。
            </p>
          </section>
          <section>
            <h4>状态字段说明</h4>
            <ul style={{ margin: "0.25rem 0 0", paddingLeft: "1.1rem", fontSize: "0.85rem" }}>
              <li><strong>元数据</strong>：含摘要或 CSL-JSON 的完整题录</li>
              <li><strong>PDF</strong>：已上传全文附件</li>
              <li><strong>已OCR</strong>：PDF 经 MinerU 解析，可作为综述语料</li>
              <li><strong>语料就绪</strong>：当前项目已构建分析语料快照（可写综述）</li>
            </ul>
          </section>
        </div>
      </div>
    </>
  );
}
