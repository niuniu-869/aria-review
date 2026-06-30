/**
 * WorkbenchLayout.tsx — 语料工作台（landing，路由 "/"）
 *
 * IA 重定位：从"文献综述生成器" → "学术文献语料工作台"。
 * 叙事 = 语料生产线四段：① 导入文档 → ② Agent 自主加工 → ③ 结构化语料库 → ④ 下游应用。
 *
 * 关键约束（契约 §0 / routing.test）：
 *   - ① 导入段内嵌既有 <ProjectsPage/>（"我的项目"列表 + "新建 SLR 项目"表单原样保留）。
 *   - ④ 下游应用保留"综述/分析/导出"可达入口（降为 link，不删）。
 *   - 视觉复用现有纸/墨/朱砂设计系统，新增类一律 wb- 前缀，零覆盖既有类。
 */
import { Link } from "react-router-dom";
import { ProjectsPage } from "../../pages/ProjectsPage";
import { TrustBadgeStrip } from "../TrustBadgeStrip";
import { QualityPanel } from "../quality/QualityPanel";
import { useProjects } from "../../api/agentHooks";

/** 语料生产线四段（印章序号 + 标题 + 副文案 + 锚点 id） */
const STAGES = [
  { seal: "一", id: "wb-import", title: "导入文档", desc: "新建 SLR 项目或选择已有项目，PDF / 题录批量入库" },
  { seal: "二", id: "wb-agent", title: "Agent 自主加工", desc: "检索补全 · 相关性筛选 · 字段抽取 · OCR 结构化，全程留痕可审" },
  { seal: "三", id: "wb-corpus", title: "结构化语料库", desc: "质量指标与可信指标并陈，每条结论都能回溯到原文" },
  { seal: "四", id: "wb-app", title: "下游应用", desc: "在可信语料之上生成综述、做计量分析、导出报告" },
] as const;

/** ② Agent 加工能力（只读说明卡） */
const CAPS = [
  { k: "检索补全", v: "Sciverse / OpenAlex 元数据反查，补齐题录" },
  { k: "相关性筛选", v: "AI 评分纳入/排除，PRISMA 留痕" },
  { k: "字段抽取", v: "结构化抽取方法/样本/结论等关键字段" },
  { k: "OCR 结构化", v: "MinerU 版面还原，段/表/坐标可定位" },
] as const;

function scrollToStage(id: string) {
  const el = document.getElementById(id);
  if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
}

export function WorkbenchLayout() {
  const { data } = useProjects();
  // ④ 下游入口的深链目标：取最近一个项目；无项目时回落到 ① 导入段引导先建项目。
  const latestPid = data?.projects?.[0]?.id;
  const reviewHref = latestPid ? `/projects/${latestPid}/analysis/review` : undefined;
  const analysisHref = latestPid ? `/projects/${latestPid}/analysis/overview` : undefined;
  const outputHref = latestPid ? `/projects/${latestPid}/output` : undefined;

  return (
    <div className="container wb-shell">
      {/* 卷首区 */}
      <header className="wb-masthead">
        <p className="wb-eyebrow">学术文献 · 语料生产线</p>
        <h1 className="wb-title">
          语料工作台<span className="wb-title-dot">·</span>
          <span className="wb-title-en">Corpus Workbench</span>
        </h1>
        <p className="wb-lead">
          把零散文献炼成<strong>可溯源、可分析、可信</strong>的结构化语料 ——
          顺着下方四段生产线，从一篇 PDF 走到一份能逐句回链原文的综述。
        </p>
      </header>

      {/* 横向流水：四段生产线（点击平滑滚到对应锚区） */}
      <nav className="wb-line" aria-label="语料生产线四段">
        {STAGES.map((s, i) => (
          <div className="wb-line-cell" key={s.id}>
            <a
              className="wb-stage"
              href={`#${s.id}`}
              onClick={(e) => {
                e.preventDefault();
                scrollToStage(s.id);
              }}
            >
              <span className="wb-stage-seal" aria-hidden="true">{s.seal}</span>
              <span className="wb-stage-body">
                <span className="wb-stage-title">{s.title}</span>
                <span className="wb-stage-desc">{s.desc}</span>
              </span>
            </a>
            {i < STAGES.length - 1 && (
              <span className="wb-flow-arrow" aria-hidden="true">→</span>
            )}
          </div>
        ))}
      </nav>

      {/* ① 导入文档 —— 内嵌既有 ProjectsPage（我的项目 + 新建 SLR 项目，原样保留） */}
      <section className="wb-zone" id="wb-import" aria-labelledby="wb-import-head">
        <div className="wb-zone-head">
          <span className="wb-zone-seal" aria-hidden="true">一</span>
          <h2 className="wb-zone-title" id="wb-import-head">导入文档</h2>
          <p className="wb-zone-sub">建立语料起点：新建项目或进入已有项目，批量导入 PDF / 题录。</p>
        </div>
        <div className="wb-zone-projects">
          <ProjectsPage />
        </div>
      </section>

      {/* ② Agent 自主加工 —— 只读能力说明 */}
      <section className="wb-zone" id="wb-agent" aria-labelledby="wb-agent-head">
        <div className="wb-zone-head">
          <span className="wb-zone-seal" aria-hidden="true">二</span>
          <h2 className="wb-zone-title" id="wb-agent-head">Agent 自主加工</h2>
          <p className="wb-zone-sub">进入任一项目的对话工作台，Agent 自主完成下列加工，全程可审计、可回放。</p>
        </div>
        <div className="wb-cap-grid">
          {CAPS.map((c) => (
            <div className="wb-cap card" key={c.k}>
              <div className="wb-cap-k">{c.k}</div>
              <div className="wb-cap-v muted">{c.v}</div>
            </div>
          ))}
        </div>
      </section>

      {/* ③ 结构化语料库 —— 质量 + 可信指标（QualityPanel 由 F5 接入此处） */}
      <section className="wb-zone wb-zone-corpus" id="wb-corpus" aria-labelledby="wb-corpus-head">
        <div className="wb-zone-head">
          <span className="wb-zone-seal" aria-hidden="true">三</span>
          <h2 className="wb-zone-title" id="wb-corpus-head">结构化语料库</h2>
          <p className="wb-zone-sub">语料是「活的库」：既看质量（缺字段/重复/未解析），也看可信（零伪造率 / grounding / 哈希链）。</p>
        </div>
        <div className="ql-corpus-grid">
          {/* 质量：最近项目的语料质检（后端未生成时静默降级，不打断） */}
          {latestPid ? (
            <QualityPanel projectId={latestPid} />
          ) : (
            <div className="card ql-panel muted">创建项目并加工语料后，这里展示语料质检（缺字段 / 重复 / 未解析）。</div>
          )}
          {/* 可信：全局可信主张（每条结论绑定原文证据与哈希链） */}
          <div className="wb-corpus-trust card">
            <h3 className="wb-corpus-trust-title">可信底座</h3>
            <p className="muted wb-corpus-trust-sub">每条结论都绑定原文证据与可验证哈希链，支持点击回链原文。</p>
            <TrustBadgeStrip />
          </div>
        </div>
      </section>

      {/* ④ 下游应用 —— 综述 / 分析 / 导出（保留可达，不删） */}
      <section className="wb-zone" id="wb-app" aria-labelledby="wb-app-head">
        <div className="wb-zone-head">
          <span className="wb-zone-seal" aria-hidden="true">四</span>
          <h2 className="wb-zone-title" id="wb-app-head">下游应用</h2>
          <p className="wb-zone-sub">在可信语料之上展开应用。{latestPid ? "已为你链到最近的项目。" : "先在「① 导入文档」创建项目，应用入口随即可用。"}</p>
        </div>
        <div className="wb-downstream">
          <DownstreamCard
            title="AI 综述"
            desc="逐句可回链原文的可溯源综述"
            href={reviewHref}
            fallbackId="wb-import"
          />
          <DownstreamCard
            title="文献计量分析"
            desc="领域概览 · 主题地图 · 合作网络"
            href={analysisHref}
            fallbackId="wb-import"
          />
          <DownstreamCard
            title="导出报告"
            desc="Markdown / HTML 报告与引用列表"
            href={outputHref}
            fallbackId="wb-import"
          />
        </div>
      </section>
    </div>
  );
}

/** 下游入口卡：有项目则 Link 深链，无项目则回链到 ① 导入段引导建项目。 */
function DownstreamCard({
  title,
  desc,
  href,
  fallbackId,
}: {
  title: string;
  desc: string;
  href?: string;
  fallbackId: string;
}) {
  const body = (
    <>
      <span className="wb-down-title">{title}</span>
      <span className="wb-down-desc muted">{desc}</span>
    </>
  );
  if (href) {
    return (
      <Link className="wb-down card" to={href} aria-label={title}>
        {body}
      </Link>
    );
  }
  return (
    <a
      className="wb-down card wb-down-pending"
      href={`#${fallbackId}`}
      aria-label={title}
      title="先创建项目后可用"
      onClick={(e) => {
        e.preventDefault();
        scrollToStage(fallbackId);
      }}
    >
      {body}
    </a>
  );
}
