import { Link } from "react-router-dom";

/**
 * AboutPage — 公开「Agent 工作原理」页（未登录可访问，路由挂在 RequireAuth 之外）。
 *
 * 薄壳（返回入口 + 标题 + skip-link）包一个全屏 iframe，复用自包含静态页
 * /agent-workflow.html（已过 nature 去 slop）。iframe 加 sandbox="allow-scripts"（不给
 * allow-same-origin）：demo 纯自包含 DOM 动画，不依赖同源能力，收窄安全边界（codex P4）。
 */
export function AboutPage() {
  return (
    <div className="about-page">
      <a href="#about-main" className="about-skip">跳到正文</a>
      <header className="about-bar">
        <Link to="/welcome" className="btn btn-ghost about-back" aria-label="返回首页">
          ← 返回
        </Link>
        <span className="about-title">Aria Agent 工作原理</span>
      </header>
      {/* main + tabIndex=-1：skip-link 跳转后能真正把键盘焦点落到正文（codex P4） */}
      <main id="about-main" className="about-main" tabIndex={-1}>
        <iframe
          className="about-frame"
          src="/agent-workflow.html"
          title="Aria Agent 工作原理"
          sandbox="allow-scripts"
        />
      </main>
    </div>
  );
}
