/**
 * RequireAuth — 路由守卫（Phase B）。未登录 → 跳 /login（记住来源路径）。
 * 作为 layout route 的 element，用 <Outlet/> 渲染受保护子路由。
 */
import { useEffect } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import { localDateKey, shouldTrackDailyAppOpen, track } from "../lib/track";

const APP_OPEN_DATE_KEY = "aria.analytics.appOpenDate";

export function RequireAuth() {
  const { isLoading, isAuthenticated } = useAuth();
  const loc = useLocation();

  useEffect(() => {
    if (!isAuthenticated) return;
    try {
      const today = localDateKey(new Date());
      if (!shouldTrackDailyAppOpen(localStorage.getItem(APP_OPEN_DATE_KEY), today)) return;
      // 送达成功才写去重标记：失败时当天后续访问仍会补报（codex 复核 P2）。
      void track("app_open").then((delivered) => {
        if (!delivered) return;
        try {
          localStorage.setItem(APP_OPEN_DATE_KEY, today);
        } catch {
          /* localStorage 不可用则放弃去重 */
        }
      });
    } catch {
      // localStorage 不可用时放弃去重与上报，绝不影响认证主流程。
    }
  }, [isAuthenticated]);

  // DEV/E2E：放行守卫，保持现有 playwright e2e 与本地开发不被登录阻挡；生产构建守卫生效。
  if (import.meta.env.DEV) return <Outlet />;

  if (isLoading) {
    return (
      <div
        className="container"
        style={{ paddingTop: "3rem", textAlign: "center", color: "var(--ink-3)" }}
      >
        加载中…
      </div>
    );
  }
  if (!isAuthenticated) {
    // 首页路人 → 公开落地页讲清楚产品；深链接/会话过期 → 直接回登录（保留 from，登录后原路返回）。
    if (loc.pathname === "/") {
      return <Navigate to="/welcome" replace />;
    }
    return <Navigate to="/login" state={{ from: loc.pathname + loc.search }} replace />;
  }
  return <Outlet />;
}
