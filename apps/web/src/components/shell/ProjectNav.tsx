/**
 * ProjectNav.tsx — 项目内一级导航条（四区）
 * 位于 TopBar 下方，随 ProjectShell 渲染
 * 4 项：对话(index) / 文献库(library) / 分析(analysis) / 产出(output)
 * 使用 NavLink 激活态：--cinnabar 下划线，沿用 ProjectWorkbench 现有 tab 样式
 */
import { NavLink, useParams } from "react-router-dom";

const NAV_ITEMS = [
  { to: "",        end: true,  label: "对话",   title: "agent 对话工作台" },
  { to: "library", end: false, label: "文献库", title: "文献浏览与筛选" },
  { to: "analysis",end: false, label: "分析",   title: "bibliometrix 分析区" },
  { to: "research",end: false, label: "研究",   title: "研究空白发现与价值核验（HITL）" },
  { to: "output",  end: false, label: "产出",   title: "综述报告与引用导出" },
] as const;

export function ProjectNav() {
  const { pid } = useParams<{ pid: string }>();

  return (
    <nav className="zone-nav" aria-label="项目一级导航">
      {NAV_ITEMS.map((item) => {
        const href = item.to ? `/projects/${pid}/${item.to}` : `/projects/${pid}`;
        return (
          <NavLink
            key={item.to}
            to={href}
            end={item.end}
            title={item.title}
            className={({ isActive }) =>
              `zone-nav-item${isActive ? " active" : ""}`
            }
          >
            {item.label}
          </NavLink>
        );
      })}
    </nav>
  );
}
