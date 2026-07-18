/**
 * LoginPage — 登录 / 注册（Phase B）。
 *
 * 全屏 split 品牌体验：左「靛蓝墨韵」品牌面板（朱砂印章 + 可信话术），右认证卡。
 * 视觉语言对齐全站设计系统（纸/墨/朱砂 token、宋体标题、.card/.btn/.input）。
 * 登录/注册成功后经 useEffect 等 isAuthenticated 翻转再跳转，避免与 RequireAuth 竞态。
 */
import { useEffect, useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { ApiError, authLogin, authRegister } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { track } from "../lib/track";

export function LoginPage() {
  // 初始模式支持 ?mode=register（Welcome 页「开始使用」CTA 直达注册态）
  const [mode, setMode] = useState<"login" | "register">(() =>
    new URLSearchParams(window.location.search).get("mode") === "register" ? "register" : "login",
  );
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [invite, setInvite] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const nav = useNavigate();
  const loc = useLocation() as { state?: { from?: string } };
  const { refresh, isAuthenticated } = useAuth();
  const from = loc.state?.from || "/";

  // 已登录（含登录/注册成功后 /me 刷新到位）→ 离开 /login 跳目标页。
  // 用 effect 等 isAuthenticated 真正翻转再导航，避免「nav 早于登录态更新 → 被 RequireAuth 弹回 /login」的竞态。
  useEffect(() => {
    if (isAuthenticated) nav(from, { replace: true });
  }, [isAuthenticated, from, nav]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      if (mode === "login") {
        await authLogin({ email, password });
        track("login_success");
      } else {
        await authRegister({ email, password, invite_code: invite || undefined });
      }
      // 触发 /me 刷新；跳转交给上面的 useEffect（等 isAuthenticated 翻转），此处不直接 nav 以免竞态。
      refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "操作失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  function toggleMode(next: "login" | "register") {
    setMode(next);
    setErr("");
  }

  return (
    <div className="auth-screen">
      {/* 左：品牌墨韵面板（窄屏隐藏） */}
      <aside className="auth-brand">
        <div className="auth-brand-inner">
          <Link to="/welcome" style={{ textDecoration: "none", color: "inherit" }} aria-label="回到 Aria Review 首页">
            <div className="auth-seal" aria-hidden="true">綜</div>
            <h1 className="auth-brand-title">Aria&nbsp;Review</h1>
          </Link>
          <p className="auth-brand-tag">可信文献综述 Agent 工作台</p>
          <p className="auth-brand-desc">
            把零散文献炼成<strong>可溯源、可分析、可信</strong>的结构化语料——
            从一篇 PDF 走到一份能逐句回链原文的系统综述。
          </p>
          <ul className="auth-trust">
            <li><span className="auth-trust-tick" aria-hidden="true">✓</span> 可验证 RunLog 哈希链</li>
            <li><span className="auth-trust-tick" aria-hidden="true">✓</span> grounding 逐句溯源</li>
            <li><span className="auth-trust-tick" aria-hidden="true">✓</span> 零伪造约束</li>
          </ul>
        </div>
        <p className="auth-brand-foot">Aria Review · 面向研究者的可信文献综述工作台</p>
      </aside>

      {/* 右：认证卡 */}
      <main className="auth-panel">
        <div className="auth-card">
          <div className="auth-card-head">
            <span className="auth-seal auth-seal-sm" aria-hidden="true">綜</span>
            <div>
              <div className="auth-card-brand">Aria Review</div>
              <div className="auth-card-sub">可信文献综述工作台</div>
            </div>
          </div>

          <div className="auth-tabs" role="tablist" aria-label="登录或注册">
            {(["login", "register"] as const).map((m) => (
              <button
                key={m}
                type="button"
                role="tab"
                aria-selected={mode === m}
                className={`auth-tab ${mode === m ? "is-active" : ""}`}
                onClick={() => toggleMode(m)}
              >
                {m === "login" ? "登录" : "注册"}
              </button>
            ))}
          </div>

          <form onSubmit={submit} className="auth-form">
            <div className="auth-field">
              <label htmlFor="email">邮箱</label>
              <input
                id="email"
                className="input"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
              />
            </div>

            <div className="auth-field">
              <label htmlFor="password">
                密码
                {mode === "register" && <span className="auth-hint">（至少 8 位）</span>}
              </label>
              <input
                id="password"
                className="input"
                type="password"
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                required
                minLength={mode === "register" ? 8 : undefined}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
              />
            </div>

            {mode === "register" && (
              <div className="auth-field">
                <label htmlFor="invite">邀请码</label>
                <input
                  id="invite"
                  className="input"
                  type="text"
                  required
                  value={invite}
                  onChange={(e) => setInvite(e.target.value)}
                  placeholder="需邀请码才能注册"
                />
              </div>
            )}

            {err && (
              <div className="auth-error" role="alert">
                {err}
              </div>
            )}

            <button type="submit" className="btn btn-primary btn-block btn-lg" disabled={busy}>
              {busy ? "处理中…" : mode === "login" ? "登录" : "创建账号"}
            </button>
          </form>

          <p className="auth-foot">
            {mode === "login" ? "还没有账号？" : "已有账号？"}
            <button
              type="button"
              className="auth-link"
              onClick={() => toggleMode(mode === "login" ? "register" : "login")}
            >
              {mode === "login" ? "用邀请码注册" : "去登录"}
            </button>
          </p>
        </div>
      </main>
    </div>
  );
}
