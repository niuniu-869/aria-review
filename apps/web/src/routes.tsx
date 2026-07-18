/**
 * routes.tsx — M3 路由重构
 *
 * 结构：
 *   /                               ProjectsPage
 *   /projects/:pid                  ProjectShell（壳层）
 *     index                         ChatWorkbench（agent 对话）
 *     library                       LibraryView（三栏文管）
 *     library/:paperId              LibraryView 右栏详情（兼容深链接）
 *     papers[/paperId]              重定向到 library[/paperId]
 *     analysis                      AnalysisView（重定向至 overview）
 *     analysis/:view                AnalysisView（13 视图之一）
 *     output                        OutputView（产出区）
 *   /settings                       SettingsPage
 *
 * M3 变更：
 *   - /legacy 路由已移除（CorpusFlow/Welcome 能力全部由 /analysis 承接）
 *   - analysis 增加 :view 参数子路由，支持深链接（如 /analysis/conceptual）
 *   - analysis 根路由用 Navigate 重定向到 overview
 */
import { lazy, Suspense, type ReactNode } from "react";
import { Link, Navigate, Route, Routes, useParams } from "react-router-dom";

// 页面
import { WorkbenchLayout } from "./components/workbench/WorkbenchLayout";
import { RouteErrorBoundary } from "./components/RouteErrorBoundary";
import { RouteLoadingFallback } from "./components/RouteFallback";

// 壳层
import { ProjectShell } from "./components/shell/ProjectShell";

// 认证（Phase B）：登录/注册页 + 路由守卫
import { RequireAuth } from "./components/AuthGate";
import { LoginPage } from "./pages/LoginPage";

// 公开落地页（未登录访问 / 的着陆点，设计见 docs/welcome-page-design.md）
const WelcomePage = lazy(() => import("./pages/WelcomePage").then((m) => ({ default: m.WelcomePage })));
// 公开「Agent 工作原理」页（iframe 复用自包含静态页，未登录可访问）
const AboutPage = lazy(() => import("./pages/AboutPage").then((m) => ({ default: m.AboutPage })));

const ChatWorkbench = lazy(() => import("./pages/ChatWorkbench").then((m) => ({ default: m.ChatWorkbench })));
const LibraryView = lazy(() => import("./pages/LibraryView").then((m) => ({ default: m.LibraryView })));
const AnalysisView = lazy(() => import("./pages/AnalysisView").then((m) => ({ default: m.AnalysisView })));
const ResearchView = lazy(() => import("./pages/ResearchView").then((m) => ({ default: m.ResearchView })));
const OutputView = lazy(() => import("./pages/OutputView").then((m) => ({ default: m.OutputView })));
const SettingsPage = lazy(() => import("./pages/SettingsPage").then((m) => ({ default: m.SettingsPage })));

// 开发/联调路由（仅 DEV：playwright 用）。把 import() 放进 import.meta.env.DEV 的死分支，
// prod 构建 DEV=false → 整个 lazy/import 被 DCE 删除，DevRoutes 模块与内置 fixture 不进 prod
// bundle（连 chunk 都不产出）。codex 终审 P1。
const DevRoutes = import.meta.env.DEV
  ? lazy(() => import("./dev/DevRoutes").then((m) => ({ default: m.DevRoutes })))
  : null;

function routePage(node: ReactNode) {
  return (
    <RouteErrorBoundary>
      <Suspense fallback={<RouteLoadingFallback />}>{node}</Suspense>
    </RouteErrorBoundary>
  );
}

function PapersRedirect() {
  const { pid, paperId } = useParams<{ pid: string; paperId?: string }>();
  return <Navigate to={`/projects/${pid}/library${paperId ? `/${paperId}` : ""}`} replace />;
}

/** 404 兜底页：未匹配任何路由时显示，提供返回首页入口（复用 .container/.card 样式） */
function NotFound() {
  return (
    <div className="container" style={{ paddingTop: "2rem" }}>
      <div className="card" style={{ textAlign: "center", padding: "2rem" }}>
        <h2 style={{ margin: "0 0 0.5rem" }}>页面不存在 (404)</h2>
        <p style={{ margin: "0 0 1.25rem", color: "var(--ink-3)", fontSize: "0.9rem" }}>
          你访问的地址未找到，可能已失效或输入有误。
        </p>
        <Link className="btn btn-primary" to="/">
          返回首页
        </Link>
      </div>
    </div>
  );
}

export function AppRoutes() {
  return (
    <Routes>
      {/* 公开落地页 + 登录 / 注册（未登录可访问） */}
      <Route path="/welcome" element={routePage(<WelcomePage />)} />
      <Route path="/about" element={routePage(<AboutPage />)} />
      <Route path="/login" element={routePage(<LoginPage />)} />

      {/* 受保护路由组：未登录 → 跳 /login（RequireAuth 生产生效，DEV 放行 e2e） */}
      <Route element={<RequireAuth />}>
      {/* 语料工作台 landing（内嵌 ProjectsPage：我的项目 + 新建 SLR 项目） */}
      <Route path="/" element={<WorkbenchLayout />} />

      {/* 项目工作台（ProjectShell 壳层 + 四区） */}
      <Route path="/projects/:pid" element={<ProjectShell />}>
        <Route index element={routePage(<ChatWorkbench />)} />

        {/* 文献库：LibraryView 提供容器 Outlet，子路由为列表和详情 */}
        <Route path="library" element={routePage(<LibraryView />)}>
          <Route path=":paperId" element={null} />
        </Route>
        <Route path="papers" element={<PapersRedirect />} />
        <Route path="papers/:paperId" element={<PapersRedirect />} />

        {/* 分析区：analysis 根路由重定向到 overview；:view 为具体视图 */}
        <Route path="analysis">
          <Route index element={<Navigate to="overview" replace />} />
          <Route path=":view" element={routePage(<AnalysisView />)} />
        </Route>

        {/* 研究区：GAP 发现 + 价值核验（HITL 研究副驾） */}
        <Route path="research" element={routePage(<ResearchView />)} />

        <Route path="output" element={routePage(<OutputView />)} />
      </Route>

      {/* 全局设置 */}
      <Route path="/settings" element={routePage(<SettingsPage />)} />
      </Route>

      {/* 开发/联调路由（仅 DEV；prod 构建里此分支为死代码被消除） */}
      {import.meta.env.DEV && DevRoutes && (
        <Route
          path="/dev/*"
          element={routePage(<DevRoutes />)}
        />
      )}

      {/* catch-all：未匹配任何路由 → 404 兜底 */}
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
