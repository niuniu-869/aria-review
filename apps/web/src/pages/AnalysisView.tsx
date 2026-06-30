/**
 * AnalysisView.tsx — 分析区主视图（M3）
 *
 * 路由：/projects/:pid/analysis/:view?（view 默认 overview）
 *
 * 数据流闸门：
 *   - 无 activeCorpus(null) 或 status≠ready → 显示「构建分析语料」提示（复用 CorpusStatusCard）
 *   - stale=true → 顶部警告条（仍允许查看旧分析）
 *   - ready → 给面板传 projectId={String(pid)} + corpusId={activeCorpus.rCorpusId}
 *
 * id 契约：分析 REST 用 R 字符串 id（rCorpusId），不能传 DB int corpusId。
 *
 * 布局：.app-shell-2col（左 AnalysisSidebar + 右 AnalysisFrame + Panel）
 * URL 同步：sidebar 点击 → useNavigate，深链接 → useParams view
 */
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useProject, useMaterializeCorpus } from "../api/agentHooks";
import type { ActiveCorpus } from "../api/agentHooks";
import { useLlmSettings } from "../api/useLlmSettings";
import { AnalysisSidebar, findViewMeta, type AnalysisViewId } from "../components/AnalysisSidebar";
import { AnalysisFrame } from "../components/AnalysisFrame";
import { ErrorBoundary } from "../components/ErrorBoundary";

// 13 个现有面板（原样复用，只换容器）
import { OverviewPanel } from "../components/OverviewPanel";
import { SourcesPanel } from "../components/SourcesPanel";
import { AuthorsPanel } from "../components/AuthorsPanel";
import { DocumentsPanel } from "../components/DocumentsPanel";
import { ConceptualPanel } from "../components/ConceptualPanel";
import { IntellectualPanel } from "../components/IntellectualPanel";
import { SocialPanel } from "../components/SocialPanel";
import { ScreenPanel } from "../components/ScreenPanel";
import { PrismaPanel } from "../components/PrismaPanel";
import { ChatPanel } from "../components/ChatPanel";
import { AiToolsPanel } from "../components/AiToolsPanel";
import { ReviewPanel } from "../components/ReviewPanel";
import { ReportPanel } from "../components/ReportPanel";

// ---------------------------------------------------------------------------
// 数据流闸门：CorpusStatusCard（复用 M2 逻辑）
// ---------------------------------------------------------------------------

interface CorpusStatusCardProps {
  projectId: number;
  activeCorpus: ActiveCorpus | null | undefined;
}

function CorpusStatusCard({ projectId, activeCorpus }: CorpusStatusCardProps) {
  const mat = useMaterializeCorpus(projectId);
  const isBuilding = mat.isPending;

  // ---- 无 active corpus ----
  if (!activeCorpus) {
    return (
      <div className="card corpus-status-card" style={{ marginBottom: "1.5rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
          <div>
            <h4 style={{ margin: 0, fontSize: "0.95rem" }}>分析语料未就绪</h4>
            <p style={{ margin: "0.25rem 0 0", fontSize: "0.83rem", color: "var(--ink-3)" }}>
              需先将 included 论文物化为 R 分析语料，才能运行 13 项文献计量分析。
            </p>
          </div>
          <button
            className="btn"
            onClick={() => mat.mutate()}
            disabled={isBuilding}
            style={{ marginLeft: "auto", whiteSpace: "nowrap" }}
          >
            {isBuilding ? "构建中…" : "构建分析语料"}
          </button>
        </div>
        {mat.isError && (
          <p style={{ margin: "0.5rem 0 0", color: "var(--red)", fontSize: "0.83rem" }}>
            构建失败：{(mat.error as Error)?.message ?? "未知错误"}
          </p>
        )}
      </div>
    );
  }

  // ---- active corpus failed ----
  if (activeCorpus.status === "failed") {
    return (
      <div className="card corpus-status-card" style={{ marginBottom: "1.5rem", borderColor: "var(--danger)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
          <div>
            <h4 style={{ margin: 0, fontSize: "0.95rem", color: "var(--danger)" }}>语料构建失败</h4>
            <p style={{ margin: "0.25rem 0 0", fontSize: "0.83rem", color: "var(--ink-3)" }}>
              上次构建遇到错误，请检查 included 论文并重试。
            </p>
          </div>
          <button
            className="btn btn-danger"
            onClick={() => mat.mutate()}
            disabled={isBuilding}
            style={{ marginLeft: "auto", whiteSpace: "nowrap" }}
          >
            {isBuilding ? "构建中…" : "重试构建"}
          </button>
        </div>
      </div>
    );
  }

  // ---- active corpus parsing ----
  if (activeCorpus.status === "parsing") {
    return (
      <div className="card corpus-status-card" style={{ marginBottom: "1.5rem" }}>
        <h4 style={{ margin: 0, fontSize: "0.95rem" }}>语料解析中…</h4>
        <p style={{ margin: "0.25rem 0 0", fontSize: "0.83rem", color: "var(--ink-3)" }}>
          R 服务正在解析 {activeCorpus.documentCount} 篇题录，请稍候后刷新。
        </p>
      </div>
    );
  }

  // ready：无需返回卡片，由调用方决定是否显示（stale 条单独渲染）
  return null;
}

// ---------------------------------------------------------------------------
// stale 警告条
// ---------------------------------------------------------------------------

interface StaleBarProps {
  projectId: number;
}

function StaleBar({ projectId }: StaleBarProps) {
  const mat = useMaterializeCorpus(projectId);
  return (
    <div
      className="stale-bar"
      role="alert"
      style={{
        display: "flex",
        alignItems: "center",
        gap: "0.75rem",
        padding: "0.5rem 1rem",
        background: "var(--warn-soft, #fef3c7)",
        borderBottom: "1px solid var(--warn, #b8791a)",
        fontSize: "0.85rem",
        color: "var(--warn, #b8791a)",
        flexWrap: "wrap",
      }}
    >
      <span>⚠ 纳入集已变更，当前分析数据可能过期。仍可查看旧分析结果。</span>
      <button
        className="btn"
        onClick={() => mat.mutate()}
        disabled={mat.isPending}
        style={{ marginLeft: "auto", fontSize: "0.82rem", padding: "0.3rem 0.75rem", whiteSpace: "nowrap" }}
      >
        {mat.isPending ? "重算中…" : "立即重算"}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 面板分发：根据 view id 渲染对应 Panel
// ---------------------------------------------------------------------------

interface PanelDispatchProps {
  view: AnalysisViewId;
  /** 项目 id 字符串（Panel props 接受 string） */
  projectId: string;
  /** R 字符串语料 id（分析 REST 路径参数，非 DB int） */
  corpusId: string;
  /** 是否已有 ready corpus（无 corpus 的面板仍可渲染 Screen/Prisma） */
  hasCorpus: boolean;
  /** M5: LLM 配置（从 useLlmSettings 读取，注入 AI 面板） */
  llm?: { apiKey?: string; baseUrl?: string; model?: string };
}

function PanelDispatch({ view, projectId, corpusId, hasCorpus, llm }: PanelDispatchProps) {
  // 需要 corpus 的面板，无 corpus 时显示提示。
  // codex M3-P2#1: screen(AI相关性筛选)走 /corpus/{id}/ai/screen, 需 rCorpusId, 归入需 corpus;
  // 仅 prisma 真正只依赖 projectId(走 /projects/{pid}/prisma)。
  // review 走项目级可溯源综述(run_review 用 project markdowns,不依赖 R corpus)→ 不 gate
  const needsCorpus = view !== "prisma" && view !== "review";

  if (needsCorpus && !hasCorpus) {
    return (
      <div className="placeholder-zone">
        <h3>需先构建分析语料</h3>
        <p>请返回分析区顶部，点击「构建分析语料」后即可查看此视图。</p>
      </div>
    );
  }

  switch (view) {
    case "overview":     return <OverviewPanel     projectId={projectId} corpusId={corpusId} />;
    case "sources":      return <SourcesPanel      projectId={projectId} corpusId={corpusId} />;
    case "authors":      return <AuthorsPanel      projectId={projectId} corpusId={corpusId} />;
    case "documents":    return <DocumentsPanel    projectId={projectId} corpusId={corpusId} />;
    case "conceptual":   return <ConceptualPanel   projectId={projectId} corpusId={corpusId} />;
    case "intellectual": return <IntellectualPanel projectId={projectId} corpusId={corpusId} />;
    case "social":       return <SocialPanel       projectId={projectId} corpusId={corpusId} />;
    case "screen":       return <ScreenPanel       projectId={projectId} corpusId={corpusId} />;
    case "prisma":       return <PrismaPanel       projectId={projectId} />;
    // M5: chat/review 注入 LLM 配置（prop 透传，内部逻辑不变）
    case "chat":         return <ChatPanel         projectId={projectId} corpusId={corpusId} llm={llm} />;
    case "aitools":      return <AiToolsPanel      projectId={projectId} corpusId={corpusId} llm={llm} />;
    case "review":       return <ReviewPanel       projectId={projectId} corpusId={corpusId} llm={llm} />;
    case "report":       return <ReportPanel       projectId={projectId} corpusId={corpusId} />;
    default:             return null;
  }
}

// ---------------------------------------------------------------------------
// AnalysisView 主体
// ---------------------------------------------------------------------------

/** 合法的 AnalysisViewId 集合，用于参数校验 */
const VALID_VIEWS = new Set<string>([
  "overview", "sources", "authors",
  "documents", "conceptual", "intellectual", "social",
  "screen", "prisma",
  "chat", "aitools", "review", "report",
]);

export function AnalysisView() {
  const { pid, view } = useParams<{ pid: string; view?: string }>();
  const pidNum = Number(pid);
  const navigate = useNavigate();

  // 当前视图（URL 参数 → 默认 overview）
  const activeView: AnalysisViewId =
    view && VALID_VIEWS.has(view) ? (view as AnalysisViewId) : "overview";

  // codex M3-P2#2: 非法 view 时把 URL 重定向到 overview, 避免地址与实际视图/sidebar 高亮不一致。
  useEffect(() => {
    if (view && !VALID_VIEWS.has(view) && pid) {
      navigate(`/projects/${pid}/analysis/overview`, { replace: true });
    }
  }, [view, pid, navigate]);

  const { data } = useProject(pidNum > 0 ? pidNum : 0);
  const activeCorpus = data?.activeCorpus ?? null;
  const corpusReady = activeCorpus?.status === "ready";
  const isStale = corpusReady && activeCorpus?.stale === true;

  // M5: 读取 LLM 配置，注入 AI 面板（localStorage 单源，跨组件共享）
  const { settings: llm } = useLlmSettings();
  const llmOptions = {
    apiKey: llm.apiKey || undefined,
    baseUrl: llm.baseUrl || undefined,
    model: llm.model || undefined,
  };

  // sidebar 折叠状态（本地 UI 状态，不持久化）
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  // sidebar 点击 → URL 切换（深链接可达）
  function handleSelect(v: AnalysisViewId) {
    navigate(`/projects/${pid}/analysis/${v}`);
  }

  // 获取当前视图元数据（用于 AnalysisFrame 标题）
  const viewMeta = findViewMeta(activeView);

  return (
    <div style={{ display: "flex", flexDirection: "column", minHeight: "calc(100vh - 100px)" }}>
      {/* stale 警告条（仅 ready + stale 时显示，允许查看旧分析） */}
      {isStale && pidNum > 0 && <StaleBar projectId={pidNum} />}

      <div
        className={`app-shell-2col${sidebarCollapsed ? " sidebar-collapsed" : ""}`}
        style={{ flex: 1, alignItems: "stretch" }}
      >
        {/* 左侧分组导航 */}
        <AnalysisSidebar
          activeView={activeView}
          onSelect={handleSelect}
          activeCorpus={activeCorpus}
          collapsed={sidebarCollapsed}
          onToggleCollapse={() => setSidebarCollapsed((v) => !v)}
        />

        {/* 右侧内容区 */}
        <div style={{ flex: 1, overflow: "auto" }}>
          {/* 数据流闸门：无语料/非 ready 显示构建卡片 */}
          {(!corpusReady) && activeView !== "review" && pidNum > 0 && (
            <div style={{ padding: "1.5rem" }}>
              <CorpusStatusCard projectId={pidNum} activeCorpus={activeCorpus} />
            </div>
          )}

          {/* ready 或 prisma(只需 projectId) 时渲染面板; screen 等需 corpus, 仅 ready 渲染 */}
          {(corpusReady || activeView === "prisma" || activeView === "review") && (
            <AnalysisFrame
              title={viewMeta?.title ?? activeView}
              desc={viewMeta?.desc ?? ""}
            >
              {/* 面板级隔离：单个分析视图渲染崩溃（数据异常）不波及侧栏/顶栏。
                  key 含 activeView + 项目 id：切换视图或切换项目（同一视图）后都重置 boundary，
                  否则崩溃态会在切项目后残留 fallback (codex P2)。 */}
              <ErrorBoundary
                key={`${activeView}:${pidNum}`}
                fallback={
                  <div className="placeholder-zone state state-err" role="alert">
                    此分析视图渲染出错（数据可能异常），请切换其他视图或返回项目列表
                  </div>
                }
              >
                <PanelDispatch
                  view={activeView}
                  projectId={pidNum > 0 ? String(pidNum) : ""}
                  corpusId={activeCorpus?.rCorpusId ?? ""}
                  hasCorpus={corpusReady}
                  llm={llmOptions}
                />
              </ErrorBoundary>
            </AnalysisFrame>
          )}
        </div>
      </div>
    </div>
  );
}
