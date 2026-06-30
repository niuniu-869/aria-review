import { NavLink, Outlet, useNavigate, useParams } from "react-router-dom";
import { useProject } from "../api/agentHooks";
import { ErrMsg, Loading } from "../lib/ui";
import { AgentChat } from "../components/AgentChat";

export function ProjectWorkbench() {
  const { pid } = useParams<{ pid: string }>();
  const pidNum = Number(pid);
  const navigate = useNavigate();
  const { data, isLoading, error } = useProject(pidNum);

  return (
    <div className="container" style={{ padding: "1.5rem" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "1rem" }}>
        <button className="btn btn-ghost" onClick={() => navigate("/")}>← 项目列表</button>
        {isLoading && <Loading label="加载项目…" />}
        {error && <ErrMsg error={error} />}
        {data && (
          <h2 style={{ margin: 0 }}>{data.name}</h2>
        )}
      </div>

      {/* 子导航 */}
      <nav className="tabs" style={{ marginBottom: "1.5rem" }}>
        <NavLink
          to={`/projects/${pid}`}
          end
          className={({ isActive }) => `tab${isActive ? "" : ""}`}
          aria-selected={undefined}
          style={({ isActive }) => ({
            borderBottom: isActive ? "2px solid var(--cinnabar)" : "2px solid transparent",
            color: isActive ? "var(--cinnabar)" : undefined,
            fontWeight: isActive ? 700 : undefined,
          })}
        >
          工作台
        </NavLink>
        <NavLink
          to={`/projects/${pid}/papers`}
          className={({ isActive }) => `tab${isActive ? "" : ""}`}
          style={({ isActive }) => ({
            borderBottom: isActive ? "2px solid var(--cinnabar)" : "2px solid transparent",
            color: isActive ? "var(--cinnabar)" : undefined,
            fontWeight: isActive ? 700 : undefined,
          })}
        >
          文献
        </NavLink>
      </nav>

      <div className="workbench-layout">
        {/* 主区 */}
        <div className="workbench-main">
          <Outlet />
        </div>

        {/* 侧栏 */}
        {data && (
          <aside className="workbench-aside">
            <div className="card">
              <h3 style={{ marginTop: 0, fontSize: "0.95rem" }}>项目统计</h3>
              <div className="stat-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
                <div className="stat">
                  <div className="stat-label">总文献</div>
                  <div className="stat-value">{data.paperCount}</div>
                </div>
                <div className="stat">
                  <div className="stat-label">已纳入</div>
                  <div className="stat-value">{data.includedCount}</div>
                </div>
              </div>
              {data.researchQuestion && (
                <div style={{ marginTop: "1rem" }}>
                  <div style={{ fontSize: "0.78rem", color: "var(--ink-3)", fontWeight: 600, marginBottom: "0.35rem" }}>
                    研究问题
                  </div>
                  <p style={{ margin: 0, fontSize: "0.88rem", color: "var(--ink-2)" }}>
                    {data.researchQuestion}
                  </p>
                </div>
              )}
            </div>
          </aside>
        )}
      </div>
    </div>
  );
}

// 默认工作台主区 — AgentChat (P1-10)
export function WorkbenchIndex() {
  const { pid } = useParams<{ pid: string }>();
  const pidNum = Number(pid);
  return (
    <div className="card">
      <AgentChat projectId={pidNum} />
    </div>
  );
}
