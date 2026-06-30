/**
 * ProjectShell.tsx — 项目级别壳层
 * 加载项目 → 渲染 ProjectNav + StageBar + 区域 Outlet
 * 替代旧的 ProjectWorkbench（Outlet 结构不变，只升级壳层）
 *
 * A8 新手指导接线：
 *   - 项目名行加常驻「? 新手指南」入口（GuideButton），点击重开 WelcomeTour。
 *   - 首次进入平台（localStorage 无 onboarded 标记）自动弹一次 WelcomeTour。
 *   - 区域主体顶部渲染 NextStepGuide（上下文「下一步」行动卡，可本会话关闭）。
 */
import { useEffect, useState } from "react";
import { Outlet, useNavigate, useParams } from "react-router-dom";
import { useProject } from "../../api/agentHooks";
import { ErrMsg, Loading } from "../../lib/ui";
import { NextStepGuide } from "../onboarding/NextStepGuide";
import { GuideButton, WelcomeTour, hasOnboarded, markOnboarded } from "../onboarding/WelcomeTour";
import { ProjectNav } from "./ProjectNav";
import { StageBar } from "./StageBar";

export function ProjectShell() {
  const { pid } = useParams<{ pid: string }>();
  const pidNum = Number(pid);
  const navigate = useNavigate();
  // 非法路由参数守卫 (codex M0-P2-4): /projects/foo → 不发 NaN 请求。
  const validPid = Number.isFinite(pidNum) && pidNum > 0;
  const { data, isLoading, error } = useProject(validPid ? pidNum : 0);

  // A8: 新手指南浮层开关。首次进入平台（localStorage 无标记）自动弹一次。
  const [tourOpen, setTourOpen] = useState(false);
  useEffect(() => {
    if (!hasOnboarded()) setTourOpen(true);
  }, []);

  function closeTour() {
    setTourOpen(false);
    markOnboarded();
  }

  if (!validPid) {
    return (
      <div className="project-shell">
        <div className="project-shell-body">
          <ErrMsg error={new Error(`无效的项目 ID: ${pid}`)} />
        </div>
      </div>
    );
  }

  return (
    <div className="project-shell">
      {/* A8: 新手指南浮层（首次自动弹 / 常驻入口重开） */}
      <WelcomeTour open={tourOpen} onClose={closeTour} />

      {/* 项目标题行 */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "0.75rem",
          padding: "0.6rem 1.5rem 0",
          borderBottom: "none",
          minHeight: "44px",
          // Phase 5: 窄屏允许换行，避免子项挤压把中文项目名逼成逐字竖排
          flexWrap: "wrap",
        }}
      >
        <button
          className="btn btn-ghost"
          style={{ fontSize: "0.82rem" }}
          onClick={() => navigate("/")}
        >
          ← 项目列表
        </button>

        {isLoading && <Loading label="加载项目…" />}
        {error && <ErrMsg error={error} />}

        {data && (
          <>
            <h2
              style={{
                margin: 0,
                fontSize: "1.05rem",
                fontFamily: "var(--serif)",
                // Phase 5: flex 子项默认 min-width:auto 会撑不下导致中文逐字竖排折行；
                // minWidth:0 + 单行省略号让项目名优雅截断而非竖排。
                minWidth: 0,
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {data.name}
            </h2>
            {/* 阶段进度条：紧跟项目名后面 */}
            <div style={{ marginLeft: "auto" }}>
              <StageBar stats={data} />
            </div>
            {/* A8: 常驻「? 新手指南」入口 */}
            <GuideButton onClick={() => setTourOpen(true)} />
          </>
        )}
        {/* 加载 / 出错时也保留新手指南入口（不依赖 project data） */}
        {!data && <GuideButton onClick={() => setTourOpen(true)} />}
      </div>

      {/* 一级导航 */}
      <ProjectNav />

      {/* 区域主体 */}
      <div className="project-shell-body">
        {/* A8: 上下文「下一步」行动卡（据当前阶段引导，可本会话关闭）。
            key={pidNum} 强制按项目 remount: dismissed 仅挂载时读 sessionStorage,
            不加 key 时 React Router 复用组件会让 A 项目的关闭态泄漏到 B 项目 (codex A8 P1)。 */}
        {data && <NextStepGuide key={pidNum} projectId={pidNum} stats={data} />}
        <Outlet />
      </div>
    </div>
  );
}
