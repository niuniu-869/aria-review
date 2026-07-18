/**
 * WelcomePage — 公开落地页（未登录访问 / 的着陆点）。
 *
 * 叙事主线见 docs/welcome-page-design.md：
 *   Hero+宣传片 → 01 时代坐标(AGI 五层) → 02 版图缺环(0→1/1→100) →
 *   03 凭什么可信(引用穿透演示=签名交互) → 04 真实案例(MD&A) → 数字带 → 愿景+CTA。
 * 自包含：演示数据硬编码（与宣传片 data.ts 同源），样式在 welcome.css（wel-* 前缀），零新依赖。
 */
import { useEffect, useState, type MouseEvent } from "react";
import { Link } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import { getPublicStats, type PublicStats } from "../api/client";
import "../welcome.css";

const GITHUB_URL = "https://github.com/niuniu-869/aria-review";

/* ---------- 演示数据（真实案例：盈余管理与 MD&A 信息披露，与宣传片同源） ---------- */

type CiteKey = "1" | "2";

const CITE_SOURCES: Record<
  CiteKey,
  { doc: string; title: string; anchor: string; blocks: { name: string; text: string; hit?: boolean }[] }
> = {
  "1": {
    doc: "#221 · Feldman 2010",
    title: "MD&A 语调的信息含量",
    anchor: "p.7 · 表2",
    blocks: [
      { name: "摘要", text: "发现：语调正向预测未来盈余" },
      { name: "研究方法", text: "词典法文本分析" },
      { name: "结果 · 表2", text: "10-K MD&A 语调得分：语调转正 → 累计超额收益 ↑", hit: true },
      { name: "结论", text: "语调具有增量信息含量" },
    ],
  },
  "2": {
    doc: "#386 · Brown 2011",
    title: "披露质量与信息不对称",
    anchor: "p.12 · 表3",
    blocks: [
      { name: "摘要", text: "披露复杂度与市场摩擦相关" },
      { name: "研究方法", text: "可读性 / 相似度度量" },
      { name: "结果 · 表3", text: "SEC EDGAR 样本：文本复杂度 ↑ → 买卖价差 ↑", hit: true },
      { name: "结论", text: "复杂披露加剧信息不对称" },
    ],
  },
};

const MATRIX_ROWS = [
  { paper: "#221 Feldman 2010", q: "MD&A 语调的信息含量", m: "词典法文本分析", f: "语调转正 → 累计超额收益 ↑", a: "p.7 表2" },
  { paper: "#386 Brown 2011", q: "披露质量与信息不对称", m: "可读性 / 相似度", f: "复杂度 ↑ → 买卖价差 ↑", a: "p.12 表3" },
  { paper: "#154 Muslu 2015", q: "前瞻性披露", m: "内容分析", f: "前瞻披露 ↑ → 分析师更准", a: "p.5 §3" },
  { paper: "#402 Li 2010", q: "文本可读性", m: "Fog 指数", f: "业绩差 → MD&A 更难读", a: "p.9 表1" },
];

const LADDER = [
  { num: "L1", name: "Chatbots", desc: "会对话" },
  { num: "L2", name: "Reasoners", desc: "会推理" },
  { num: "L3", name: "Agents", desc: "会执行" },
  { num: "L4", name: "Innovators", desc: "会发现新知识" },
  { num: "L5", name: "Organizations", desc: "能运转一个组织" },
];

/* ---------- 滚动入场（尊重 prefers-reduced-motion） ---------- */

function useReveal() {
  useEffect(() => {
    const els = Array.from(document.querySelectorAll<HTMLElement>("[data-reveal]"));
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches || !("IntersectionObserver" in window)) {
      els.forEach((el) => el.classList.add("is-in"));
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            e.target.classList.add("is-in");
            io.unobserve(e.target);
          }
        }
      },
      { threshold: 0.12, rootMargin: "0px 0px -48px" },
    );
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, []);
}

export function WelcomePage() {
  const { isAuthenticated } = useAuth();
  const [cite, setCite] = useState<CiteKey>("1");
  const [scrolled, setScrolled] = useState(false);
  const [stats, setStats] = useState<PublicStats | null>(null);
  useReveal();

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  // 着陆页数字实时取自 /public/stats（免认证）；失败则用静态回退值，不空白。
  useEffect(() => {
    let alive = true;
    getPublicStats()
      .then((s) => { if (alive) setStats(s); })
      .catch(() => { /* 回退到静态值 */ });
    return () => { alive = false; };
  }, []);

  function scrollToFilm(e: MouseEvent<HTMLAnchorElement>) {
    e.preventDefault();
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    document.getElementById("wel-film")?.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "center" });
  }

  const src = CITE_SOURCES[cite];

  return (
    <div className="wel-page">
      {/* ===== 顶栏 ===== */}
      <header className={`wel-nav ${scrolled ? "is-scrolled" : ""}`}>
        <Link to="/welcome" className="wel-nav-brand" aria-label="Aria Review 首页">
          <span className="wel-seal" aria-hidden="true">綜</span>
          <span className="wel-nav-name">Aria Review</span>
          <span className="wel-nav-tag">可信文献综述 Agent 工作台</span>
        </Link>
        <nav className="wel-nav-actions" aria-label="页面操作">
          <Link className="wel-gh wel-about-link" to="/about" aria-label="Agent 工作原理">工作原理</Link>
          <a className="wel-gh" href={GITHUB_URL} target="_blank" rel="noreferrer" aria-label="GitHub 开源仓库">
            <svg viewBox="0 0 16 16" width="18" height="18" fill="currentColor" aria-hidden="true">
              <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
            </svg>
            <span>GitHub</span>
          </a>
          {isAuthenticated ? (
            <Link className="btn btn-primary" to="/">进入工作台 →</Link>
          ) : (
            <>
              <Link className="btn btn-ghost" to="/login">登录</Link>
              <Link className="btn btn-primary" to="/login?mode=register">开始使用</Link>
            </>
          )}
        </nav>
      </header>

      {/* ===== Hero + 宣传片 ===== */}
      <section className="wel-hero">
        <div className="wel-inner">
          <p className="wel-eyebrow" data-reveal>ARIA REVIEW · AI FOR SCIENCE</p>
          <h1 className="wel-h1" data-reveal>
            让 AI 写的每一句综述，
            <br />
            都能追回<em>真实的原文证据</em>。
          </h1>
          <p className="wel-hero-lead" data-reveal>
            Aria 把对话式多源检索 → 全文精读 → 可信综述 → 发现空白 → 验证价值 收进一条<strong>可验证</strong>的研究加速闭环。
            <br />
            别的 Agent 给你一个答案，<strong>Aria 给你一条可验证的研究路径</strong>。
          </p>
          <div className="wel-hero-cta" data-reveal>
            {isAuthenticated ? (
              <Link className="btn btn-primary btn-lg" to="/">进入工作台 →</Link>
            ) : (
              <Link className="btn btn-primary btn-lg" to="/login?mode=register">开始使用</Link>
            )}
            <a className="btn btn-ghost btn-lg" href="#wel-film" onClick={scrollToFilm}>
              ▶&nbsp;2 分 23 秒，看懂 Aria
            </a>
          </div>
          <figure className="wel-film-wrap" data-reveal>
            <div className="wel-film" id="wel-film">
              <video controls preload="none" poster="/media/poster.jpg" playsInline>
                <source src="/media/aria-review.mp4" type="video/mp4" />
                您的浏览器不支持视频播放。
              </video>
            </div>
            <figcaption className="wel-film-cap">
              实测案例：盈余管理与 MD&amp;A 信息披露 · 片中全部为真实运行画面
            </figcaption>
          </figure>
        </div>
      </section>

      {/* ===== 01 · 时代坐标 ===== */}
      <section className="wel-section wel-dark">
        <div className="wel-inner">
          <p className="wel-kicker" data-reveal>01 · 我们在哪</p>
          <h2 className="wel-h2" data-reveal>
            Agent 已经会做事了。
            <br />
            下一层，是<em>发现值得做的事</em>。
          </h2>
          <p className="wel-body" data-reveal>
            OpenAI 在 2024 年提出过一个五层刻度：会对话的，会推理的，会执行的，会发现新知识的，最后是能运转一个组织的。
            今天的 AI 正站在第三层——它已经能替你跑完一整套任务。但从「会做事」到「会发现值得做的事」，中间隔着一整层。
            <strong>科研，是这一步跨越最真实的试验场。</strong>
          </p>
          <div className="wel-ladder" data-reveal>
            {LADDER.map((s, i) => (
              <div className={`wel-step ${i === 3 ? "wel-step-next" : ""}`} style={{ "--i": i } as React.CSSProperties} key={s.num}>
                {i === 3 && <span className="wel-step-badge">Aria 在这一步</span>}
                <span className="wel-step-num">{s.num}</span>
                <span className="wel-step-name">{s.name}</span>
                <span className="wel-step-desc">{s.desc}</span>
              </div>
            ))}
          </div>
          <p className="wel-src-note" data-reveal>OPENAI · 2024 · 据报道的内部分级</p>
        </div>
      </section>

      {/* ===== 02 · 版图缺环 ===== */}
      <section className="wel-section">
        <div className="wel-inner">
          <p className="wel-kicker" data-reveal>02 · 缺了什么</p>
          <h2 className="wel-h2" data-reveal>
            从 0 到 1 有人做了，从 1 到 100 有人做了。
            <br />
            <em>「往哪走」</em>还没有。
          </h2>
          <div className="wel-cards" data-reveal>
            <article className="wel-card">
              <p className="wel-card-k">0 → 1 · 提出假设</p>
              <h3>AI 已经能写出论文</h3>
              <p>
                AI Scientist 写的论文通过了 ICLR 2025 研讨会的同行评审；Co-Scientist 能在几天内生成候选研究假设。
              </p>
              <p className="wel-card-note">前提：方向已经选好。</p>
            </article>
            <article className="wel-card">
              <p className="wel-card-k">1 → 100 · 加速执行</p>
              <h3>AI 已经能加速实验</h3>
              <p>
                Kosmos 一次 12 小时的自主运行，可以完成相当于数月的数据分析；自动实验室把实验越做越快、回归越做越准。
              </p>
              <p className="wel-card-note">前提：方向已经选好。</p>
            </article>
            <article className="wel-card wel-card-hot">
              <p className="wel-card-k">0 之前 · 决定方向</p>
              <h3>这个 gap 真的值得研究吗？</h3>
              <p>
                是真空白，还是你没搜全？值不值得投入一年时间？——这是每项研究开始前最贵的判断，也是 AI for Science
                版图上最后的留白。
              </p>
              <p className="wel-card-note wel-card-note-hot">Aria 做的就是这一步。</p>
            </article>
          </div>
          <p className="wel-closer" data-reveal>方向错了，后面跑得越快，浪费越大。</p>
        </div>
      </section>

      {/* ===== 03 · 凭什么可信 ===== */}
      <section className="wel-section wel-alt">
        <div className="wel-inner">
          <p className="wel-kicker" data-reveal>03 · 凭什么可信</p>
          <h2 className="wel-h2" data-reveal>
            方向判断建立在综述之上。
            <br />
            综述不可信，<em>一切归零</em>。
          </h2>
          <p className="wel-body" data-reveal>
            要判断「这个空白值不值得做」，你得先知道「大家都做了什么、还差什么」——这就是综述。
            可是今天的综述 Agent，要么只能帮你<strong>找到</strong>文献，要么把文献切碎塞进模型，写出一篇
            <strong>你没法核对</strong>的漂亮文章。一条编造的引用，就足以让整个方向判断作废。
          </p>
          <div className="wel-feats" data-reveal>
            <div className="wel-feat">
              <h3>整篇精读，不是切碎检索</h3>
              <p>每篇文献被拆成段、表、图、公式的结构块，结果表格整张保留；AI 像研究者一样逐块精读，每条结论都带着页码和表号。</p>
            </div>
            <div className="wel-feat">
              <h3>写验分离</h3>
              <p>读和写交给 AI，核验交给确定性的程序：每条引用都被逐条反查，每个数据都绑定原文出处，全程留下可独立复核的运行记录。</p>
            </div>
            <div className="wel-feat">
              <h3>句句可穿透</h3>
              <p>综述里的每个引用点一下，直接落到那篇文献的原文段落——连第几页、第几张表都标着。</p>
            </div>
          </div>

          {/* 签名交互：引用穿透演示 */}
          <div className="wel-demo" data-reveal>
            <div className="wel-demo-review">
              <p className="wel-demo-label">AI 生成的综述 · 节选</p>
              <p className="wel-demo-text">
                近年研究显示，管理层讨论与分析（MD&amp;A）的语调具有增量信息含量：语调转正与未来累计超额收益显著正相关
                <button
                  type="button"
                  className={`wel-cite ${cite === "1" ? "is-active" : ""}`}
                  onClick={() => setCite("1")}
                  aria-pressed={cite === "1"}
                >
                  [1]
                </button>
                ；而披露文本的复杂度上升会加剧信息不对称，表现为更高的买卖价差
                <button
                  type="button"
                  className={`wel-cite ${cite === "2" ? "is-active" : ""}`}
                  onClick={() => setCite("2")}
                  aria-pressed={cite === "2"}
                >
                  [2]
                </button>
                。
              </p>
              <p className="wel-demo-hint">点一下引用，右侧回到原文 →</p>
            </div>
            <div className="wel-demo-src" key={cite} aria-live="polite">
              <p className="wel-demo-doc">
                {src.doc} <span>《{src.title}》</span>
              </p>
              <ul className="wel-blocks">
                {src.blocks.map((b) => (
                  <li className={`wel-blk ${b.hit ? "is-hit" : ""}`} key={b.name}>
                    <span className="wel-blk-name">{b.name}</span>
                    <span className="wel-blk-text">{b.text}</span>
                    {b.hit && <span className="wel-blk-anchor">● {src.anchor}</span>}
                  </li>
                ))}
              </ul>
            </div>
          </div>
          <p className="wel-demo-foot" data-reveal>这不是示意图——线上产品里，每条引用都这样工作。</p>
          <p className="wel-demo-foot" data-reveal>
            <Link className="wel-link" to="/about">▸ 看 Agent 如何一步步工作 →</Link>
          </p>
        </div>
      </section>

      {/* ===== 04 · 真实案例 ===== */}
      <section className="wel-section">
        <div className="wel-inner">
          <p className="wel-kicker" data-reveal>04 · 它真的能跑</p>
          <h2 className="wel-h2" data-reveal>
            66 篇 MD&amp;A 文献，两条候选空白，
            <br />
            <em>一真一假</em>。
          </h2>

          <div className="wel-case" data-reveal>
            <p className="wel-case-step">第一步 · 精读汇成证据矩阵</p>
            <p className="wel-case-desc">66 篇文献每篇一行：研究问题、方法、主要发现——每一格都带原文锚点，点一下回到原文那一块。</p>
            <div className="wel-tablewrap">
              <table className="wel-matrix">
                <thead>
                  <tr>
                    <th>文献</th>
                    <th>研究问题</th>
                    <th>方法</th>
                    <th>主要发现</th>
                    <th>锚点</th>
                  </tr>
                </thead>
                <tbody>
                  {MATRIX_ROWS.map((r) => (
                    <tr key={r.paper}>
                      <td className="wel-td-paper">{r.paper}</td>
                      <td>{r.q}</td>
                      <td>{r.m}</td>
                      <td>{r.f}</td>
                      <td className="wel-td-anchor">● {r.a}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="wel-table-cap">66 篇中的 4 篇 · 节选</p>
          </div>

          <div className="wel-case" data-reveal>
            <p className="wel-case-step">第二步 · 从矩阵的张力里派生候选空白</p>
            <blockquote className="wel-gap-quote">
              「MD&amp;A 前瞻性语气与盈余管理动机的因果识别，仍缺工具变量设计。」
              <cite>候选空白 g2 · 从证据矩阵派生，可回溯到原文</cite>
            </blockquote>
          </div>

          <div className="wel-case" data-reveal>
            <p className="wel-case-step">第三步 · 价值核验：确定性裁决，公开规则</p>
            <div className="wel-verdicts">
              <div className="wel-verdict wel-verdict-ok">
                <p className="wel-verdict-gap">g2 · 前瞻性语气的因果识别</p>
                <p className="wel-verdict-metrics">反查命中 2 篇 · 新颖度 0.86 · 可行性 ✓ 数据可得·方法成熟</p>
                <p className="wel-verdict-tag">✓ 真空白 · 新颖度 × 可行性双重达标 → 值得做</p>
              </div>
              <div className="wel-verdict wel-verdict-no">
                <p className="wel-verdict-gap">g3 · 可读性指标在中文年报的适用性</p>
                <p className="wel-verdict-metrics">反查命中 41 篇 · 新颖度 0.22</p>
                <p className="wel-verdict-tag">✕ 不是空白，是没搜全</p>
              </div>
            </div>
          </div>

          <p className="wel-closer" data-reveal>
            由证据说话，凭锚点立论——每一条空白，都能回溯到原文。
          </p>

          <div className="wel-shots" data-reveal>
            <figure>
              <img src="/media/shot-overview.jpg" alt="Aria Review 领域概览：文献计量仪表盘" loading="lazy" />
              <figcaption>领域概览 · 文献计量仪表盘</figcaption>
            </figure>
            <figure>
              <img src="/media/shot-review.jpg" alt="AI 生成的可溯源综述正文" loading="lazy" />
              <figcaption>可溯源综述 · 正文与证据</figcaption>
            </figure>
            <figure>
              <img src="/media/shot-provenance.jpg" alt="引用穿透：点击综述引用，右栏定位并高亮原文" loading="lazy" />
              <figcaption>引用穿透 · 原文定位</figcaption>
            </figure>
          </div>
        </div>
      </section>

      {/* ===== 数字带 ===== */}
      <section className="wel-stats wel-dark">
        <div className="wel-inner">
          <div className="wel-stats-row" data-reveal>
            <div className="wel-stat">
              <span className="wel-stat-num">{(stats?.papers ?? 469).toLocaleString()}</span>
              <span className="wel-stat-label">篇文献结构化入库</span>
            </div>
            <div className="wel-stat">
              <span className="wel-stat-num">{(stats?.blockAnchors ?? 8007).toLocaleString()}</span>
              <span className="wel-stat-label">条块级溯源锚点</span>
            </div>
            <div className="wel-stat">
              <span className="wel-stat-num">{(stats?.dois ?? 461).toLocaleString()}</span>
              <span className="wel-stat-label">DOI 精准贯通</span>
            </div>
          </div>
          <p className="wel-stats-note" data-reveal>以上数字来自真实运行，分母按真实文档对象计算。</p>
        </div>
      </section>

      {/* ===== 愿景 + CTA ===== */}
      <section className="wel-section wel-final">
        <div className="wel-inner">
          <h2 className="wel-h2" data-reveal>
            下一站：让机器持续监听一个领域，
            <br />
            <em>自动捕获值得研究的空白</em>。
          </h2>
          <p className="wel-body" data-reveal>
            今天，Aria 已经把「多源检索 → 可信综述 → 找空白 → 价值核验」连成一条可验证的闭环，并用新颖度与可行性双重裁决判断空白是否值得做。我们正在把它推向下一站——对一个研究领域的文献流做持续监听，让值得研究的问题自己浮现，且每一条都带着可回溯的证据。
            <strong>让 AI 参与科研的全过程，而每一步都可被验证。</strong>
          </p>
          <div className="wel-cta" data-reveal>
            {isAuthenticated ? (
              <Link className="btn btn-primary btn-lg" to="/">进入工作台 →</Link>
            ) : (
              <>
                <Link className="btn btn-primary btn-lg" to="/login?mode=register">开始使用</Link>
                <p className="wel-cta-note">
                  内测邀请制，注册需邀请码 · <Link to="/login" className="wel-link">已有账号？登录</Link>
                </p>
              </>
            )}
          </div>
        </div>
      </section>

      {/* ===== Footer ===== */}
      <footer className="wel-footer">
        <div className="wel-inner wel-footer-row">
          <span className="wel-footer-brand">
            <span className="wel-seal wel-seal-sm" aria-hidden="true">綜</span>
            Aria Review · 可信文献综述 Agent 工作台
          </span>
          <span className="wel-footer-meta">
            <a href={GITHUB_URL} target="_blank" rel="noreferrer" className="wel-link">GitHub</a>
            <span aria-hidden="true">·</span>
            <span>© 2026</span>
          </span>
        </div>
      </footer>
    </div>
  );
}
