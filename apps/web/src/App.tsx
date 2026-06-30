/**
 * App.tsx — 应用根组件
 * M0: 用 TopBar 替换内联 header，BackendStatus 逻辑已移入 TopBar
 * 防白屏护栏: AppRoutes 外包一层顶层 ErrorBoundary，崩溃时显示可用兜底卡片。
 *   TopBar 在 ErrorBoundary 外侧 → 顶栏永远可见（不随路由树崩溃消失）。
 */
import { TopBar } from "./components/shell/TopBar";
import { AppRoutes } from "./routes";
import { ErrorBoundary } from "./components/ErrorBoundary";

/** 顶层兜底卡片：保留可用性（刷新 / 返回首页），避免整页白屏 */
function AppCrashFallback() {
  return (
    <div className="container" style={{ paddingTop: "2rem" }}>
      <div className="card" style={{ textAlign: "center", padding: "2rem" }}>
        <h2 style={{ margin: "0 0 0.5rem" }}>页面渲染出错（已隔离）</h2>
        <p style={{ margin: "0 0 1.25rem", color: "var(--ink-3)", fontSize: "0.9rem" }}>
          当前页面遇到了渲染异常。你可以刷新页面重试，或返回首页继续操作。
        </p>
        <div style={{ display: "flex", gap: "0.75rem", justifyContent: "center", flexWrap: "wrap" }}>
          <button className="btn btn-primary" onClick={() => location.reload()}>
            刷新页面
          </button>
          <a className="btn" href="/">
            返回首页
          </a>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <div className="app-shell">
      <TopBar />
      <ErrorBoundary fallback={<AppCrashFallback />}>
        <AppRoutes />
      </ErrorBoundary>
    </div>
  );
}
