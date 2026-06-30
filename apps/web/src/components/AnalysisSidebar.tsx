/**
 * AnalysisSidebar.tsx — 分析区左侧分组导航
 *
 * 4 组 × 13 视图，可折叠到图标轨（collapsed 态）。
 * 复用 styles.css 的 .sidebar/.sidebar-section/.sidebar-item 等现有类。
 * 当前选中项高亮（--cinnabar）。未就绪的组（需要 activeCorpus）在无语料时置灰。
 */
import type { ActiveCorpus } from "../api/agentHooks";

// ---------------------------------------------------------------------------
// 视图元数据
// ---------------------------------------------------------------------------

export type AnalysisViewId =
  | "overview" | "sources" | "authors"                          // 统计概览
  | "documents" | "conceptual" | "intellectual" | "social"       // 知识结构
  | "screen" | "prisma"                                          // 文献库洞察
  | "chat" | "aitools" | "review" | "report";                    // AI 工具台

interface ViewMeta {
  id: AnalysisViewId;
  label: string;
  icon: string;   // 单字符 emoji/符号，折叠态只显示图标
  title: string;  // 简述，用于 AnalysisFrame 标题栏
  desc: string;   // 一句话说明
  /**
   * 视图级是否需要 activeCorpus ready（覆盖组级 requiresCorpus）。
   * 省略则继承所在组的 requiresCorpus。
   * codex 契约修正：「文献库洞察」组 requiresCorpus:false，但 screen 实际调
   * /corpus/{id}/ai/screen 需 corpus → 给 screen 单独设 true、prisma 设 false。
   */
  requiresCorpus?: boolean;
}

interface GroupMeta {
  key: string;
  label: string;
  icon: string;
  /** 组级默认：是否需要 activeCorpus ready 才能可用（视图可用 ViewMeta.requiresCorpus 覆盖） */
  requiresCorpus: boolean;
  views: ViewMeta[];
}

/** 视图实际是否需要 corpus：视图级优先，缺省继承组级 */
function viewRequiresCorpus(group: GroupMeta, view: ViewMeta): boolean {
  return view.requiresCorpus ?? group.requiresCorpus;
}

export const ANALYSIS_GROUPS: GroupMeta[] = [
  {
    key: "stats",
    label: "统计概览",
    icon: "📊",
    requiresCorpus: true,
    views: [
      { id: "overview",     label: "领域概览",   icon: "🔭", title: "领域概览",   desc: "年度产出、主要指标与总体态势" },
      { id: "sources",      label: "核心期刊",   icon: "📰", title: "核心期刊",   desc: "期刊来源分布、Bradford 核心区" },
      { id: "authors",      label: "核心作者",   icon: "👤", title: "核心作者",   desc: "作者产出量与影响力排名" },
    ],
  },
  {
    key: "knowledge",
    label: "知识结构",
    icon: "🕸",
    requiresCorpus: true,
    views: [
      { id: "documents",    label: "关键词热点", icon: "🔑", title: "关键词热点", desc: "高频词与 TF-IDF 词云" },
      { id: "conceptual",   label: "主题地图",   icon: "🗺", title: "主题地图",   desc: "共词聚类概念图谱（Thematic Map）" },
      { id: "intellectual", label: "知识脉络",   icon: "📚", title: "知识脉络",   desc: "引文耦合知识结构演化" },
      { id: "social",       label: "合作网络",   icon: "🤝", title: "合作网络",   desc: "作者/机构/国家合作关系网络" },
    ],
  },
  {
    key: "library",
    // 组级 false 仅作缺省：screen 需 corpus、prisma 不需，逐视图覆盖（与 PanelDispatch needsCorpus 一致）
    label: "文献库洞察",
    icon: "🔍",
    requiresCorpus: false,
    views: [
      { id: "screen",  label: "相关性筛选", icon: "🎯", title: "AI 相关性筛选", desc: "对文献进行 AI 相关性评分与排序", requiresCorpus: true },
      { id: "prisma",  label: "PRISMA",     icon: "📋", title: "PRISMA 流程图",  desc: "生成系统综述 PRISMA 流程图", requiresCorpus: false },
    ],
  },
  {
    key: "aitools",
    label: "AI 工具台",
    icon: "🤖",
    requiresCorpus: true,
    views: [
      { id: "chat",    label: "语料对话", icon: "💬", title: "语料对话",   desc: "与当前语料进行多轮学术对话" },
      { id: "aitools", label: "AI 工具",  icon: "⚙",  title: "AI 工具",   desc: "文本总结、翻译与改写" },
      { id: "review",  label: "AI 综述",  icon: "✍",  title: "AI 文献综述", desc: "自动生成可引用的文献综述" },
      { id: "report",  label: "导出报告", icon: "📤", title: "导出报告与引用", desc: "下载 Markdown/HTML 报告及引用列表" },
    ],
  },
];

/** 根据 viewId 快速查找 ViewMeta */
export function findViewMeta(id: AnalysisViewId): ViewMeta | undefined {
  for (const g of ANALYSIS_GROUPS) {
    const v = g.views.find((v) => v.id === id);
    if (v) return v;
  }
  return undefined;
}

// ---------------------------------------------------------------------------
// 组件
// ---------------------------------------------------------------------------

interface AnalysisSidebarProps {
  activeView: AnalysisViewId;
  onSelect: (view: AnalysisViewId) => void;
  activeCorpus: ActiveCorpus | null | undefined;
  collapsed: boolean;
  onToggleCollapse: () => void;
}

export function AnalysisSidebar({
  activeView,
  onSelect,
  activeCorpus,
  collapsed,
  onToggleCollapse,
}: AnalysisSidebarProps) {
  const corpusReady = activeCorpus?.status === "ready";

  return (
    <aside
      className={`sidebar${collapsed ? " collapsed" : ""}`}
      aria-label="分析导航"
      style={{ position: "sticky", top: "var(--topbar-h, 54px)", alignSelf: "flex-start", zIndex: 5 }}
    >
      {/* 折叠切换按钮 */}
      <button
        className="sidebar-item"
        onClick={onToggleCollapse}
        title={collapsed ? "展开侧边栏" : "折叠侧边栏"}
        aria-label={collapsed ? "展开侧边栏" : "折叠侧边栏"}
        style={{ justifyContent: "center", borderBottom: "1px solid var(--line)", padding: "0.55rem" }}
      >
        <span style={{ fontSize: "0.88rem" }}>{collapsed ? "▶" : "◀"}</span>
        {!collapsed && <span style={{ fontSize: "0.78rem", color: "var(--ink-3)", marginLeft: 2 }}>收起</span>}
      </button>

      {/* 4 分组 */}
      {ANALYSIS_GROUPS.map((group) => {
        // 组级置灰：仅当组内全部视图都需 corpus 且语料未就绪（部分视图可用时不整组置灰）。
        const groupDisabled =
          !corpusReady && group.views.every((v) => viewRequiresCorpus(group, v));
        return (
          <div
            key={group.key}
            className="sidebar-section"
            style={{ opacity: groupDisabled ? 0.45 : 1 }}
            aria-disabled={groupDisabled}
          >
            {/* 分组标题（折叠态只显示图标） */}
            {!collapsed && (
              <div className="sidebar-title" title={groupDisabled ? "需要先构建分析语料" : undefined}>
                {group.label}
                {groupDisabled && (
                  <span
                    style={{ marginLeft: "0.35rem", fontSize: "0.68rem", color: "var(--ink-3)" }}
                    title="需先构建语料"
                  >
                    (未就绪)
                  </span>
                )}
              </div>
            )}

            {/* 视图条目：逐视图按 requiresCorpus 判定置灰（如无语料时 screen 灰、prisma 可用） */}
            {group.views.map((view) => {
              const isActive = activeView === view.id;
              const disabled = !corpusReady && viewRequiresCorpus(group, view);
              return (
                <button
                  key={view.id}
                  className={`sidebar-item${isActive ? " active" : ""}`}
                  onClick={() => !disabled && onSelect(view.id)}
                  disabled={disabled}
                  title={collapsed ? `${view.label}：${view.desc}` : view.desc}
                  aria-current={isActive ? "page" : undefined}
                  style={{
                    cursor: disabled ? "not-allowed" : "pointer",
                    justifyContent: collapsed ? "center" : "flex-start",
                  }}
                >
                  <span style={{ fontSize: "1rem", flexShrink: 0 }}>{view.icon}</span>
                  {!collapsed && <span>{view.label}</span>}
                </button>
              );
            })}
          </div>
        );
      })}
    </aside>
  );
}
