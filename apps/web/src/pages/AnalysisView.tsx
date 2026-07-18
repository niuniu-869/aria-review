/**
 * AnalysisView.tsx — 分析区主视图（M3）
 *
 * 路由：/projects/:pid/analysis/:view?（view 默认 overview）
 *
 * 数据流闸门：
 *   - 无 ready activeCorpus → 显示「构建分析语料」提示（复用 CorpusStatusCard）
 *   - latestCorpus failed/parsing → 显示失败原因或重新构建出路
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
import { getPanelRCorpusId, useProject, useMaterializeCorpus } from "../api/agentHooks";
import type { ActiveCorpus, LatestCorpus } from "../api/agentHooks";
import type { RCorpusId } from "../api/corpusIds";
import { useHealth } from "../api/hooks";
import { useLlmSettings } from "../api/useLlmSettings";
import { AnalysisSidebar, type AnalysisViewId } from "../components/AnalysisSidebar";
import {
  DEFAULT_ANALYSIS_VIEW,
  findAnalysisView,
  isAnalysisViewId,
} from "../components/analysisViews";
import { AnalysisFrame } from "../components/AnalysisFrame";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { ProjectGate } from "../components/ProjectGate";
import { track } from "../lib/track";

// ---------------------------------------------------------------------------
// 数据流闸门：CorpusStatusCard（复用 M2 逻辑）
// ---------------------------------------------------------------------------

const ANALYSIS_SERVICE_COMMAND = "docker compose --profile analysis up -d";

function RAnalysisServiceGuide() {
  return (
    <AnalysisFrame
      title="R 分析服务未启动"
      desc="bibliometrix 分析依赖可选的 R 服务；Agent 已连接，分析服务尚未启用。"
    >
      <div className="placeholder-zone" role="status" aria-label="R 分析服务未启动">
        <h3>分析功能需要 R 服务</h3>
        <p>
          当前只启动了 Web、Agent 与数据库。请在项目根目录启用 analysis profile，
          等服务启动完成后刷新页面。
        </p>
        <code
          style={{
            display: "inline-block",
            marginTop: "0.5rem",
            padding: "0.55rem 0.7rem",
            border: "1px solid var(--line)",
            borderRadius: "var(--radius-sm)",
            background: "var(--paper-2)",
            color: "var(--ink)",
            userSelect: "all",
            whiteSpace: "pre-wrap",
          }}
        >
          {ANALYSIS_SERVICE_COMMAND}
        </code>
      </div>
    </AnalysisFrame>
  );
}

interface CorpusStatusCardProps {
  projectId: number;
  activeCorpus: ActiveCorpus | null | undefined;
  latestCorpus: LatestCorpus | null | undefined;
}

function CorpusStatusCard({ projectId, activeCorpus, latestCorpus }: CorpusStatusCardProps) {
  const mat = useMaterializeCorpus(projectId);
  const isBuilding = mat.isPending;
  const latestStatus = latestCorpus?.status ?? activeCorpus?.status;
  const latestError =
    latestCorpus?.errorReason ??
    activeCorpus?.errorReason ??
    (mat.error as Error | null)?.message ??
    "未知错误";

  // ---- 当前 mutation 失败 / 最近一次构建失败 ----
  if (mat.isError || latestStatus === "failed") {
    return (
      <div
        className="card corpus-status-card"
        role="alert"
        style={{ marginBottom: "1.5rem", borderColor: "var(--danger)" }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
          <div>
            <h4 style={{ margin: 0, fontSize: "0.95rem", color: "var(--danger)" }}>语料构建失败</h4>
            <p style={{ margin: "0.25rem 0 0", fontSize: "0.83rem", color: "var(--ink-3)" }}>
              {latestError}
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

  // ---- 同步构建中 / 孤儿 parsing 行 ----
  if (latestStatus === "parsing") {
    return (
      <div className="card corpus-status-card" style={{ marginBottom: "1.5rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
          <div>
            <h4 style={{ margin: 0, fontSize: "0.95rem" }}>
              {isBuilding ? "分析语料构建中" : "上次构建未完成（可能中断）"}
            </h4>
            <p style={{ margin: "0.25rem 0 0", fontSize: "0.83rem", color: "var(--ink-3)" }}>
              {isBuilding
                ? `正在构建 ${latestCorpus?.documentCount ?? activeCorpus?.documentCount ?? 0} 篇题录的分析语料。`
                : "上次请求可能因关页或网关超时中断，可直接重新构建。"}
            </p>
          </div>
          <button
            className="btn"
            onClick={() => mat.mutate()}
            disabled={isBuilding}
            style={{ marginLeft: "auto", whiteSpace: "nowrap" }}
          >
            {isBuilding ? "构建中…" : "重新构建"}
          </button>
        </div>
      </div>
    );
  }

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
  corpusId: RCorpusId;
  /** 是否已有 ready corpus（无 corpus 的面板仍可渲染 Screen/Prisma） */
  hasCorpus: boolean;
  /** M5: LLM 配置（从 useLlmSettings 读取，注入 AI 面板） */
  llm?: { apiKey?: string; baseUrl?: string; model?: string };
}

function PanelDispatch({ view, projectId, corpusId, hasCorpus, llm }: PanelDispatchProps) {
  const viewDefinition = findAnalysisView(view);

  if (viewDefinition.requiresCorpus && !hasCorpus) {
    return (
      <div className="placeholder-zone">
        <h3>需先构建分析语料</h3>
        <p>请返回分析区顶部，点击「构建分析语料」后即可查看此视图。</p>
      </div>
    );
  }

  return viewDefinition.renderPanel({ projectId, corpusId, llm });
}

// ---------------------------------------------------------------------------
// AnalysisView 主体
// ---------------------------------------------------------------------------

export function AnalysisView() {
  const { pid, view } = useParams<{ pid: string; view?: string }>();
  const pidNum = Number(pid);
  const navigate = useNavigate();
  const health = useHealth();

  useEffect(() => {
    track("analysis_view", undefined, pidNum);
    // 每次分析区视图组件挂载仅上报一次。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 当前视图（URL 参数 → 默认 overview）
  const activeView: AnalysisViewId =
    isAnalysisViewId(view) ? view : DEFAULT_ANALYSIS_VIEW;
  const viewMeta = findAnalysisView(activeView);

  // codex M3-P2#2: 非法 view 时把 URL 重定向到 overview, 避免地址与实际视图/sidebar 高亮不一致。
  useEffect(() => {
    if (view && !isAnalysisViewId(view) && pid) {
      navigate(`/projects/${pid}/analysis/${DEFAULT_ANALYSIS_VIEW}`, { replace: true });
    }
  }, [view, pid, navigate]);

  const project = useProject(pidNum > 0 ? pidNum : 0);
  const activeCorpus = project.data?.activeCorpus ?? null;
  const latestCorpus = project.data?.latestCorpus ?? null;
  const rCorpusId = getPanelRCorpusId(activeCorpus);
  const corpusReady = activeCorpus?.status === "ready";
  const latestNeedsAction = latestCorpus?.status === "failed" || latestCorpus?.status === "parsing";
  const isStale = corpusReady && activeCorpus?.stale === true && !latestNeedsAction;
  const showCorpusStatusCard = viewMeta.requiresCorpus && (!corpusReady || latestNeedsAction);
  const rAnalysisServiceDown = health.data?.status === "ok" && health.data?.rService === "down";

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

  return (
    <ProjectGate project={project}>
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
            {rAnalysisServiceDown && viewMeta.requiresR ? (
              <RAnalysisServiceGuide />
            ) : (
              <>
                {/* 数据流闸门：无 ready 语料或最近构建需处理时显示构建卡片 */}
                {showCorpusStatusCard && pidNum > 0 && (
                  <div style={{ padding: "1.5rem" }}>
                    <CorpusStatusCard
                      projectId={pidNum}
                      activeCorpus={activeCorpus}
                      latestCorpus={latestCorpus}
                    />
                  </div>
                )}

                {/* 面板可用性由 registry.requiresCorpus 控制；prisma/review 可无 corpus 渲染。 */}
                {(!viewMeta.requiresCorpus || corpusReady) && (
                  <AnalysisFrame
                    title={viewMeta.title}
                    desc={viewMeta.desc}
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
                        corpusId={rCorpusId}
                        hasCorpus={corpusReady}
                        llm={llmOptions}
                      />
                    </ErrorBoundary>
                  </AnalysisFrame>
                )}
              </>
            )}
          </div>
        </div>
      </div>
    </ProjectGate>
  );
}
