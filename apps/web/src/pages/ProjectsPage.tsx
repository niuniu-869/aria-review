/**
 * ProjectsPage.tsx — 项目列表 / 入口页
 *
 * A8 新手指导：
 *   - 首次用户（无项目）：在新建表单上方展示「欢迎 hero + 五步工作流可视化」，秒懂平台能做什么。
 *   - 有项目时：hero 收起为一行小提示（避免老用户冗余）。
 *   - inline 样式迁到 design-system 类（.projects-* / .wf-*），视觉与 A6/A7 面板统一。
 *   - 新建表单功能不变（useCreateProject）。
 */
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useCreateProject, useProjects } from "../api/agentHooks";
import { ErrMsg, Loading } from "../lib/ui";
import { TrustBadgeStrip } from "../components/TrustBadgeStrip";

/** 五步工作流（与 StageBar / WelcomeTour 共享心智模型） */
const WORKFLOW = [
  { n: 1, label: "导入", desc: "题录入库" },
  { n: 2, label: "筛选", desc: "纳入排除" },
  { n: 3, label: "分析", desc: "文献计量" },
  { n: 4, label: "综述", desc: "AI 初稿" },
  { n: 5, label: "导出", desc: "报告引用" },
] as const;

/** 五步工作流可视化（hero 内 + 复用） */
function WorkflowFlow() {
  return (
    <ol className="wf-flow" aria-label="五步文献综述工作流">
      {WORKFLOW.map((s, i) => (
        <li key={s.n} className="wf-step">
          <span className="wf-step-n" aria-hidden="true">{s.n}</span>
          <span className="wf-step-body">
            <span className="wf-step-label">{s.label}</span>
            <span className="wf-step-desc">{s.desc}</span>
          </span>
          {i < WORKFLOW.length - 1 && (
            <span className="wf-arrow" aria-hidden="true">→</span>
          )}
        </li>
      ))}
    </ol>
  );
}

export function ProjectsPage() {
  const navigate = useNavigate();
  const { data, isLoading, error } = useProjects();
  const createMutation = useCreateProject();

  const [name, setName] = useState("");
  const [rq, setRq] = useState("");
  const [desc, setDesc] = useState("");
  const [formErr, setFormErr] = useState<string | null>(null);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) { setFormErr("项目名称不能为空"); return; }
    setFormErr(null);
    try {
      const proj = await createMutation.mutateAsync({
        name: name.trim(),
        researchQuestion: rq.trim() || undefined,
        description: desc.trim() || undefined,
      });
      navigate(`/projects/${proj.id}`);
    } catch (err) {
      setFormErr((err as Error)?.message ?? "创建失败");
    }
  }

  // 首次用户：列表已加载且为空
  const isFirstTime = !!data && data.projects.length === 0;
  const hasProjects = !!data && data.projects.length > 0;

  return (
    <div className="container projects-page">
      {/* A8: 首次用户欢迎 hero + 五步工作流可视化 */}
      {isFirstTime && (
        <section className="projects-hero" aria-label="平台介绍">
          <h1 className="projects-hero-title">
            欢迎使用 Biblio<span className="projects-hero-dot">CN</span>
          </h1>
          <p className="projects-hero-lead">
            面向中文研究者的文献计量与系统综述助手 ——
            顺着<strong>五步工作流</strong>，端到端完成一份可溯源的文献综述。
          </p>
          <WorkflowFlow />
          {/* Phase 5: 全局可信主张徽章条（WorkflowFlow 之后） */}
          <TrustBadgeStrip />
          <p className="projects-hero-cta-hint">
            从下方新建你的第一个项目开始 ↓
          </p>
        </section>
      )}

      {/* 有项目时：收起为一行小提示 */}
      {hasProjects && (
        <p className="projects-flow-hint" aria-label="工作流提示">
          工作流：<span className="projects-flow-hint-steps">导入 → 筛选 → 分析 → 综述 → 导出</span>
        </p>
      )}

      <h2 className="projects-heading">我的项目</h2>

      {/* 新建表单 */}
      <div className="card projects-create-card">
        <h3 className="projects-create-title">新建 SLR 项目</h3>
        <form onSubmit={handleCreate}>
          <div className="projects-field">
            <label htmlFor="proj-name">项目名称 *</label>
            <input
              id="proj-name"
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例：电子健康档案隐私保护系统综述"
            />
          </div>
          <div className="projects-field">
            <label htmlFor="proj-rq">研究问题（可选）</label>
            <input
              id="proj-rq"
              className="input"
              value={rq}
              onChange={(e) => setRq(e.target.value)}
              placeholder="例：哪些技术用于 EHR 数据的隐私保护？"
            />
          </div>
          <div className="projects-field projects-field-last">
            <label htmlFor="proj-desc">描述（可选）</label>
            <textarea
              id="proj-desc"
              className="input"
              rows={2}
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
              placeholder="简要描述研究范围"
            />
          </div>
          {formErr && <p className="projects-form-err">{formErr}</p>}
          <button
            type="submit"
            className="btn btn-primary"
            disabled={createMutation.isPending}
          >
            {createMutation.isPending ? "创建中…" : "创建项目"}
          </button>
        </form>
      </div>

      {/* 项目列表 */}
      {isLoading && <Loading label="加载项目列表…" />}
      {error && <ErrMsg error={error} />}
      {isFirstTime && (
        <p className="muted">暂无项目，请在上方创建第一个项目。</p>
      )}
      {hasProjects && (
        <div className="proj-grid">
          {data.projects.map((p) => (
            <div
              key={p.id}
              className="card proj-card"
              role="button"
              tabIndex={0}
              onClick={() => navigate(`/projects/${p.id}`)}
              onKeyDown={(e) => e.key === "Enter" && navigate(`/projects/${p.id}`)}
            >
              <div className="proj-card-name">{p.name}</div>
              <div className="muted proj-card-meta">
                创建于 {new Date(p.createdAt).toLocaleDateString("zh-CN")}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
