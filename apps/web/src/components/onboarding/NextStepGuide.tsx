/**
 * NextStepGuide.tsx — 上下文「下一步」行动卡（A8 新手指导）
 *
 * 据项目当前阶段（paperCount / includedCount / activeCorpus）给出明确的下一步
 * 行动建议 + 一个朱砂主按钮跳到对应区。复用既有路由（library / analysis / output）。
 *
 * 放置：ProjectShell 项目壳层主体顶部。
 * 关闭：本会话内可关闭（sessionStorage，按项目维度记忆），关闭后该会话不再弹。
 *   - sessionStorage 读写均有 try/catch，隐私模式 / 禁用时优雅降级（仅当次不持久）。
 */
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import type { ActiveCorpus } from "../../api/agentHooks";

interface NextStepStats {
  paperCount: number;
  includedCount: number;
  activeCorpus?: ActiveCorpus | null;
}

interface NextStepGuideProps {
  /** 项目 ID（用于拼路由 + sessionStorage 维度） */
  projectId: number;
  stats: NextStepStats | null | undefined;
}

/** 单步建议描述 */
interface StepAdvice {
  /** 序号标签（与五步工作流对应，便于新手定位） */
  badge: string;
  /** 行动标题（朱砂强调） */
  title: string;
  /** 一句话 why（解释为什么 / 做什么，新手友好） */
  why: string;
  /** 主按钮文案 */
  cta: string;
  /** 主按钮目标（相对 /projects/:pid，"" = 对话首页） */
  to: string;
}

/** 据统计推导当前应做的「下一步」 */
function deriveAdvice(stats: NextStepStats | null | undefined): StepAdvice {
  // 无数据时按「尚未导入」处理（最保守）
  const paperCount = stats?.paperCount ?? 0;
  const includedCount = stats?.includedCount ?? 0;
  const corpusReady = stats?.activeCorpus?.status === "ready";

  if (paperCount === 0) {
    return {
      badge: "第 1 步 · 导入",
      title: "导入第一批文献",
      why: "上传 PDF/ZIP 或检索文献到文献库，这是综述的起点。",
      cta: "前往文献库导入",
      to: "library",
    };
  }
  if (includedCount === 0) {
    return {
      badge: "第 2 步 · 筛选",
      title: "筛选纳入文献",
      why: "在文献库中把相关文献标记为「纳入」，确定本次综述的范围。",
      cta: "前往文献库筛选",
      to: "library",
    };
  }
  if (!corpusReady) {
    return {
      badge: "第 3 步 · 分析",
      title: "构建分析语料",
      why: "把纳入文献物化为「语料」后，才能进行文献计量分析。",
      cta: "前往分析区构建语料",
      to: "analysis/overview",
    };
  }
  // 有 ready 语料：引导进入综述 / 导出（已完成分析就绪）
  return {
    badge: "第 4–5 步 · 综述 / 导出",
    title: "开始综述与导出",
    why: "语料已就绪，可生成 AI 综述初稿，并在产出区导出报告与引用。",
    cta: "前往综述与产出",
    to: "analysis/review",
  };
}

/** sessionStorage key（按项目维度，关一个项目的不影响其他项目） */
function dismissKey(projectId: number): string {
  return `bibliocn.nextstep.dismissed.${projectId}`;
}

/** 读取本会话是否已关闭（try/catch 优雅降级） */
function readDismissed(projectId: number): boolean {
  try {
    return sessionStorage.getItem(dismissKey(projectId)) === "1";
  } catch {
    return false;
  }
}

/** 写入本会话已关闭（try/catch 优雅降级） */
function writeDismissed(projectId: number): void {
  try {
    sessionStorage.setItem(dismissKey(projectId), "1");
  } catch {
    /* 隐私模式 / 禁用：忽略，仅当次内存态生效 */
  }
}

export function NextStepGuide({ projectId, stats }: NextStepGuideProps) {
  // 初始即读 sessionStorage，避免闪现后再消失
  const [dismissed, setDismissed] = useState(() => readDismissed(projectId));
  const navigate = useNavigate();

  if (dismissed) return null;

  const advice = deriveAdvice(stats);

  function handleDismiss() {
    setDismissed(true);
    writeDismissed(projectId);
  }

  function handleGo() {
    navigate(advice.to ? `/projects/${projectId}/${advice.to}` : `/projects/${projectId}`);
  }

  return (
    <section className="nextstep" aria-label="下一步行动建议">
      <div className="nextstep-body">
        <span className="nextstep-badge">{advice.badge}</span>
        <div className="nextstep-text">
          <p className="nextstep-title">{advice.title}</p>
          <p className="nextstep-why">{advice.why}</p>
        </div>
      </div>
      <div className="nextstep-actions">
        <button type="button" className="btn btn-primary nextstep-cta" onClick={handleGo}>
          {advice.cta}
        </button>
        <button
          type="button"
          className="btn btn-ghost nextstep-dismiss"
          onClick={handleDismiss}
          aria-label="关闭下一步建议（本次会话不再显示）"
        >
          稍后
        </button>
      </div>
    </section>
  );
}
