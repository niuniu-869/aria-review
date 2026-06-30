/**
 * StageBar.tsx — 项目阶段进度条（A8: 升级为可交互工作流向导）
 * 数据来源：project 统计（paperCount / includedCount / activeCorpus）
 * 展示：导入 → 筛选 → 分析 → 综述 → 导出，未达步骤置灰
 *
 * M2 更新：「分析」阶段的就绪判定接 activeCorpus（有 ready corpus = 该阶段已达成）。
 * A8 更新：
 *   - 每个阶段升级为 <button>，可点击跳转到对应区（可访问：button 语义 + aria-label + 键盘）。
 *   - 每个阶段加 title 提示，hover/focus 时说明该步做什么。
 *   - active 阶段更醒目（朱砂），done/active/置灰视觉保持不变。
 *   - 保持 .stage-step / done / active 类与 DOM 顺序不变（既有测试依赖）。
 */
import { useNavigate, useParams } from "react-router-dom";
import type { ActiveCorpus } from "../../api/agentHooks";

/** 项目统计（agentHooks useProject 返回的 data 子集） */
interface ProjectStats {
  paperCount: number;
  includedCount: number;
  /** M2: 项目当前 active corpus；null = 尚未物化语料 */
  activeCorpus?: ActiveCorpus | null;
}

interface StageBarProps {
  stats: ProjectStats | null | undefined;
}

/**
 * 五步工作流定义。
 * to: 相对 /projects/:pid 的子路径（"" = 对话首页）。
 * hint: hover/focus 时显示的一句话说明（新手友好）。
 */
const STAGES = [
  { key: "import",   label: "导入", to: "library",          hint: "导入文献：上传 / 检索文献到文献库" },
  { key: "screen",   label: "筛选", to: "library",          hint: "筛选纳入：在文献库标记纳入 / 排除，确定综述范围" },
  { key: "analysis", label: "分析", to: "analysis/overview", hint: "文献计量分析：领域概览、关键词、合作网络等" },
  { key: "review",   label: "综述", to: "analysis/review",   hint: "AI 综述：基于语料生成可溯源的综述初稿" },
  { key: "export",   label: "导出", to: "output",            hint: "导出产出：综述报告、引用列表与 PRISMA 流程图" },
] as const;

/** 根据项目统计判断当前所在阶段索引。
 *
 * M2: 「分析」阶段（index=2）已达成 = activeCorpus.status === "ready"。
 * 注意：stale corpus 仍算「已有语料」，阶段不回退。
 */
function getCurrentStage(stats: ProjectStats | null | undefined): number {
  if (!stats) return 0;
  if (stats.paperCount === 0) return 0;    // 尚未导入
  if (stats.includedCount === 0) return 1; // 有文献但未筛选
  // M2: 有 ready corpus → 分析阶段已达成（index=3 表示"分析"已完成，当前在"综述"或更后）
  if (stats.activeCorpus?.status === "ready") return 3;
  return 2; // 有纳入文献但无 ready corpus，当前仍在「分析」阶段
}

export function StageBar({ stats }: StageBarProps) {
  const current = getCurrentStage(stats);
  const { pid } = useParams<{ pid: string }>();
  const navigate = useNavigate();

  function go(to: string) {
    if (!pid) return;
    navigate(to ? `/projects/${pid}/${to}` : `/projects/${pid}`);
  }

  return (
    <nav className="stage-bar" aria-label="项目工作流进度，可点击跳转到对应阶段">
      {STAGES.map((s, i) => {
        const isDone = i < current;
        const isActive = i === current;
        const cls = isDone ? "done" : isActive ? "active" : "";
        const state = isActive ? "（当前阶段）" : isDone ? "（已完成）" : "";
        return (
          <button
            key={s.key}
            type="button"
            className={`stage-step ${cls}`}
            title={s.hint}
            aria-current={isActive ? "step" : undefined}
            aria-label={`第 ${i + 1} 步 ${s.label}${state}：${s.hint}`}
            onClick={() => go(s.to)}
          >
            <span className="stage-dot" aria-hidden="true" />
            {s.label}
            {i < STAGES.length - 1 && (
              <span className="stage-arrow" aria-hidden="true">›</span>
            )}
          </button>
        );
      })}
    </nav>
  );
}
