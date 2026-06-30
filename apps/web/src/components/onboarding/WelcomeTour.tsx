/**
 * WelcomeTour.tsx — 首次访问「新手指南」浮层（A8 新手指导）
 *
 * 两种用法：
 *   1. 受控浮层 <WelcomeTour open={...} onClose={...} />：介绍五步工作流 + 「开始」按钮。
 *      - role=dialog + aria-modal，遮罩 + 宣纸卡片，朱砂强调。
 *      - 可 ESC / 点遮罩关闭；打开时 focus 进入对话框，可访问。
 *   2. 常驻入口 <GuideButton onClick={...} />：ghost 按钮「? 新手指南」，老用户可随时重开。
 *
 * 持久化辅助：
 *   - hasOnboarded() / markOnboarded()：localStorage `bibliocn.onboarded` 标记，
 *     首次进入平台时自动弹一次；关闭后写标记不再自动弹。读写均 try/catch 优雅降级。
 *
 * 不引入任何 tour 第三方库，纯手写轻量浮层。
 */
import { useEffect, useRef } from "react";

/** localStorage 标记 key */
const ONBOARDED_KEY = "bibliocn.onboarded";

/** 是否已完成首次引导（try/catch：隐私模式 / 禁用时按「已引导」处理，避免反复弹） */
export function hasOnboarded(): boolean {
  try {
    return localStorage.getItem(ONBOARDED_KEY) === "1";
  } catch {
    // 读不到（隐私模式）：返回 true，避免无法持久化导致每次都弹
    return true;
  }
}

/** 写入「已完成首次引导」标记（try/catch 优雅降级） */
export function markOnboarded(): void {
  try {
    localStorage.setItem(ONBOARDED_KEY, "1");
  } catch {
    /* 隐私模式 / 禁用：忽略 */
  }
}

/** 五步工作流说明（与 StageBar / ProjectsPage hero 保持一致心智模型） */
const TOUR_STEPS = [
  { n: 1, label: "导入", desc: "上传 PDF/ZIP 或检索文献到文献库" },
  { n: 2, label: "筛选", desc: "标记纳入 / 排除，确定综述范围" },
  { n: 3, label: "分析", desc: "文献计量：关键词、合作网络、主题地图" },
  { n: 4, label: "综述", desc: "AI 生成可溯源的综述初稿" },
  { n: 5, label: "导出", desc: "导出报告、引用与 PRISMA 流程图" },
] as const;

interface WelcomeTourProps {
  open: boolean;
  /** 关闭回调（ESC / 遮罩 / 按钮 / 开始 均触发） */
  onClose: () => void;
}

/** 受控的新手指南浮层 */
export function WelcomeTour({ open, onClose }: WelcomeTourProps) {
  const dialogRef = useRef<HTMLDivElement>(null);

  // ESC 关闭 + 焦点管理：打开时焦点移入对话框、记录触发元素，关闭时恢复焦点
  // (codex A8 P2: 键盘用户关闭后焦点不应落到被卸载节点, 应回到触发入口)。
  useEffect(() => {
    if (!open) return;
    const prevFocused = document.activeElement as HTMLElement | null;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    dialogRef.current?.focus();
    return () => {
      document.removeEventListener("keydown", onKey);
      // 元素仍在文档中才恢复焦点 (避免聚焦已卸载节点)
      if (prevFocused && prevFocused.isConnected) prevFocused.focus();
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="onboard-overlay"
      onClick={onClose}
      data-testid="welcome-tour-overlay"
    >
      <div
        className="onboard-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="onboard-title"
        aria-describedby="onboard-desc"
        tabIndex={-1}
        ref={dialogRef}
        // 阻止冒泡：点卡片内部不关闭
        onClick={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          className="onboard-close"
          aria-label="关闭新手指南"
          onClick={onClose}
        >
          ×
        </button>

        <h2 id="onboard-title" className="onboard-title">
          欢迎使用 Biblio<span className="onboard-accent">CN</span>
        </h2>
        <p id="onboard-desc" className="onboard-desc">
          一个面向中文研究者的文献计量与系统综述（SLR）助手。
          只需顺着下面五步，即可端到端完成一份可溯源、零伪造、可哈希验证的文献综述。
        </p>

        <ol className="onboard-steps">
          {TOUR_STEPS.map((s) => (
            <li key={s.n} className="onboard-step">
              <span className="onboard-step-n" aria-hidden="true">{s.n}</span>
              <span className="onboard-step-text">
                <strong className="onboard-step-label">{s.label}</strong>
                <span className="onboard-step-desc">{s.desc}</span>
              </span>
            </li>
          ))}
        </ol>

        <div className="onboard-footer">
          <button type="button" className="btn btn-primary" onClick={onClose}>
            开始
          </button>
        </div>
      </div>
    </div>
  );
}

interface GuideButtonProps {
  onClick: () => void;
}

/** 常驻「? 新手指南」入口（ghost 按钮），老用户可随时重开 */
export function GuideButton({ onClick }: GuideButtonProps) {
  return (
    <button
      type="button"
      className="btn btn-ghost onboard-trigger"
      onClick={onClick}
      title="查看五步工作流新手指南"
      aria-label="打开新手指南"
    >
      ? 新手指南
    </button>
  );
}
