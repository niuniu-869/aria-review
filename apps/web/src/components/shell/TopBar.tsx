/**
 * TopBar.tsx — 全局顶部导航栏
 * 包含：品牌标识 + 项目切换下拉 + 后端状态指示点 + 设置入口
 *
 * 从 App.tsx 吸收 BackendStatus 逻辑；项目切换通过 useProjects + navigate 实现。
 */
import { useEffect, useRef, useState } from "react";
import { Link, matchPath, useLocation, useNavigate } from "react-router-dom";
import { useHealth } from "../../api/hooks";
import { useProjects } from "../../api/agentHooks";
import { useAuth } from "../../auth/AuthContext";

/** 后端状态指示点（原 App.tsx BackendStatus，搬入 TopBar） */
function BackendStatus() {
  const health = useHealth();
  const agentUp = health.data?.status === "ok";
  const rService = health.data?.rService ?? "unknown";
  const up = agentUp && rService === "up";
  const rDown = agentUp && rService === "down";
  // 无数据时 refetchInterval 会把 error 拉回 pending 重试 ~8s，凭 failureCount 保持
  // 「Agent 不可达」标签稳定，避免与「连接中」来回振荡
  const agentDown = health.isError || (!health.data && health.failureCount > 0);
  const cls = health.data ? (up ? "up" : rDown ? "down" : "") : agentDown ? "down" : "";
  const label = health.data
    ? up ? "后端就绪" : rDown ? "R 分析服务未启动" : "R 分析服务状态未知"
    : agentDown ? "Agent 不可达" : "连接中";
  return (
    <span
      className={`status-dot ${cls}`}
      title={`agent ${health.data?.status ?? "?"} · R ${rService}`}
    >
      <span className="led" /> {label}
    </span>
  );
}

/** 项目切换下拉 */
function ProjectSwitcher() {
  // TopBar 渲染在 <Routes> 之外, useParams 拿不到路由参数;
  // 用 matchPath 从当前路径解析 pid (codex M0-P2-1)。
  const location = useLocation();
  const match =
    matchPath("/projects/:pid/*", location.pathname) ??
    matchPath("/projects/:pid", location.pathname);
  const pid = match?.params?.pid;
  const { isAuthenticated } = useAuth();
  const { data } = useProjects(isAuthenticated);
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // 点外部关闭
  useEffect(() => {
    if (!open) return;
    function onClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, [open]);

  const projects = data?.projects ?? [];
  const current = projects.find((p) => String(p.id) === pid);
  const btnLabel = current ? current.name : pid ? `项目 #${pid}` : "选择项目";

  if (projects.length === 0) return null;

  return (
    <div className="project-switcher" ref={ref}>
      <button
        className="project-switcher-btn"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={btnLabel}
      >
        <span className="ps-name">{btnLabel}</span>
        <span className="ps-caret">▾</span>
      </button>
      {open && (
        <div className="project-switcher-dropdown" role="listbox">
          {projects.map((p) => (
            <button
              key={p.id}
              className={`project-switcher-dropdown-item${String(p.id) === pid ? " current" : ""}`}
              role="option"
              aria-selected={String(p.id) === pid}
              onClick={() => {
                setOpen(false);
                navigate(`/projects/${p.id}`);
              }}
            >
              {p.name}
            </button>
          ))}
          <hr className="project-switcher-dropdown-divider" />
          <button
            className="project-switcher-dropdown-item"
            onClick={() => {
              setOpen(false);
              navigate("/");
            }}
          >
            ← 所有项目
          </button>
        </div>
      )}
    </div>
  );
}

/** TopBar — 全局顶部导航栏 */
export function TopBar() {
  // 登录/注册页、公开落地页、公开原理页是独立全屏品牌体验，不显示应用顶栏（含后端健康点等内部 chrome）。
  const { pathname } = useLocation();
  if (pathname === "/login" || pathname === "/welcome" || pathname === "/about") return null;

  return (
    <header className="topbar">
      {/* 品牌 */}
      <div className="brand">
        <Link to="/" style={{ textDecoration: "none" }}>
          <span className="brand-mark">
            Aria Review
          </span>
        </Link>
        <span className="brand-sub">可信文献综述 Agent 工作台</span>
      </div>

      {/* 项目切换器（仅在项目上下文内显示） */}
      <ProjectSwitcher />

      <div className="topbar-spacer" />

      {/* 设置入口 */}
      <Link to="/settings" className="btn btn-ghost" style={{ fontSize: "0.82rem" }}>
        设置
      </Link>

      {/* 后端状态 */}
      <BackendStatus />
    </header>
  );
}
