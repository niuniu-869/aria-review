/**
 * routing.test.tsx — 验证路由骨架正确挂载各页面组件
 *
 * M3 变更：
 *   - /legacy 路由已移除，相关断言删除
 *   - analysis 路由改为 /analysis/:view，根路由重定向 overview
 *   - 新增 AnalysisSidebar 分组渲染测试
 *   - 新增数据流闸门测试（无语料显示「构建分析语料」）
 *
 * 策略: 用 MemoryRouter 避免浏览器 DOM 依赖；
 *       mock agentHooks 返回空数据，让页面正常渲染；
 *       mock useHealth 返回 undefined（避免真实网络请求）。
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Navigate, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi, describe, it, expect } from "vitest";

// ---- mock agentHooks ----
vi.mock("../api/agentHooks", () => ({
  useProjects: () => ({ data: { projects: [] }, isLoading: false, error: null }),
  useProject: () => ({
    data: { name: "测试项目", paperCount: 0, includedCount: 0, researchQuestion: null, activeCorpus: null },
    isLoading: false,
    error: null,
  }),
  useProjectPapers: () => ({ data: { papers: [] }, isLoading: false, error: null }),
  usePaper: () => ({ data: null, isLoading: false, error: null }),
  useRuns: () => ({ data: { runs: [] }, isLoading: false, error: null }),
  useCreateProject: () => ({ mutateAsync: vi.fn(), isPending: false }),
  usePatchInclusion: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
  useImportPapers: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false, data: undefined, error: null, reset: vi.fn() }),
  useCreateRun: () => ({ mutateAsync: vi.fn(), isPending: false }),
  // M2: corpus 物化 hook
  useMaterializeCorpus: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  // M4: 工件 hooks
  useArtifacts: () => ({ data: { artifacts: [] }, isLoading: false, error: null, refetch: vi.fn() }),
  useCreateArtifact: () => ({ mutateAsync: vi.fn(), isPending: false }),
  usePatchArtifact: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteArtifact: () => ({ mutateAsync: vi.fn(), isPending: false }),
  // W1: 文献库统计 hooks (Task 5)
  useProjectLibraryStats: () => ({ data: null, isLoading: false, error: null }),
  useGlobalLibraryStats: () => ({ data: null, isLoading: false, error: null }),
  // P3-T2/T4: AI 解析 hooks
  useBackfillMetadata: () => ({ mutate: vi.fn(), isPending: false, data: undefined, error: null }),
  useExtractStructured: () => ({ mutate: vi.fn(), isPending: false, data: undefined, error: null }),
}));

// ---- mock client（避免 AgentChat 真实网络请求） ----
vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    createRun: vi.fn(),
    streamAgentRun: vi.fn(),
  };
});

// ---- mock hooks (health) ----
vi.mock("../api/hooks", () => ({
  useHealth: () => ({ data: undefined, isError: false }),
}));

// Import components after mock declarations
import { ProjectsPage } from "../pages/ProjectsPage";
import { PaperDetailPage } from "../pages/PaperDetailPage";
import { ChatWorkbench } from "../pages/ChatWorkbench";
import { LibraryView } from "../pages/LibraryView";
import { LibraryIndex } from "../pages/LibraryIndex";
import { AnalysisView } from "../pages/AnalysisView";
import { OutputView } from "../pages/OutputView";
import { SettingsPage } from "../pages/SettingsPage";
import { ProjectShell } from "../components/shell/ProjectShell";
import { ProjectNav } from "../components/shell/ProjectNav";
import { AnalysisSidebar, ANALYSIS_GROUPS } from "../components/AnalysisSidebar";

// Helper: wrap with QueryClientProvider + MemoryRouter at given path
function renderAt(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/" element={<ProjectsPage />} />
          <Route path="/projects/:pid" element={<ProjectShell />}>
            <Route index element={<ChatWorkbench />} />
            <Route path="library" element={<LibraryView />}>
              <Route index element={<LibraryIndex />} />
              <Route path=":paperId" element={<PaperDetailPage />} />
            </Route>
            {/* M3: analysis/:view 路由 */}
            <Route path="analysis">
              <Route index element={<Navigate to="overview" replace />} />
              <Route path=":view" element={<AnalysisView />} />
            </Route>
            <Route path="output" element={<OutputView />} />
          </Route>
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("M0/M3 路由骨架", () => {
  it('/ 渲染 ProjectsPage — 显示"我的项目"', () => {
    renderAt("/");
    expect(screen.getByText("我的项目")).toBeInTheDocument();
  });

  it("/ 显示新建项目表单", () => {
    renderAt("/");
    expect(screen.getByText("新建 SLR 项目")).toBeInTheDocument();
    expect(screen.getByLabelText("项目名称 *")).toBeInTheDocument();
  });

  it("/projects/:pid 渲染 ProjectShell — 显示四区导航链接", () => {
    renderAt("/projects/1");
    const links = screen.getAllByRole("link");
    const texts = links.map((l) => l.textContent?.trim());
    expect(texts).toContain("对话");
    expect(texts).toContain("文献库");
    expect(texts).toContain("分析");
    expect(texts).toContain("产出");
  });

  it("/projects/:pid index 显示 AgentChat 输入框", () => {
    renderAt("/projects/1");
    expect(screen.getByLabelText("Agent 指令输入")).toBeInTheDocument();
  });

  it("/projects/:pid/library 渲染 LibraryIndex — 无文献时显示提示", () => {
    renderAt("/projects/1/library");
    expect(screen.getByText(/暂无文献/)).toBeInTheDocument();
  });

  it("/projects/:pid/analysis/overview 渲染 AnalysisView — 无语料时显示构建提示", () => {
    renderAt("/projects/1/analysis/overview");
    expect(screen.getByText("分析语料未就绪")).toBeInTheDocument();
    expect(screen.getByText("构建分析语料")).toBeInTheDocument();
  });

  it("/projects/:pid/output 渲染 OutputView 占位卡片", () => {
    renderAt("/projects/1/output");
    expect(screen.getByText("产出区")).toBeInTheDocument();
  });

  it("/settings 渲染 SettingsPage 占位卡片", () => {
    renderAt("/settings");
    expect(screen.getByText("设置")).toBeInTheDocument();
  });
});

describe("ProjectNav 组件", () => {
  function renderNav(pid = "1") {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[`/projects/${pid}`]}>
          <Routes>
            <Route path="/projects/:pid/*" element={<ProjectNav />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  it("渲染四个导航项", () => {
    renderNav();
    expect(screen.getByText("对话")).toBeInTheDocument();
    expect(screen.getByText("文献库")).toBeInTheDocument();
    expect(screen.getByText("分析")).toBeInTheDocument();
    expect(screen.getByText("产出")).toBeInTheDocument();
  });

  it("导航链接指向正确路径", () => {
    renderNav("42");
    const links = screen.getAllByRole("link");
    const hrefs = links.map((l) => l.getAttribute("href"));
    expect(hrefs).toContain("/projects/42");
    expect(hrefs).toContain("/projects/42/library");
    expect(hrefs).toContain("/projects/42/analysis");
    expect(hrefs).toContain("/projects/42/output");
  });
});

// ---------------------------------------------------------------------------
// M3 新增：AnalysisSidebar 分组渲染测试
// ---------------------------------------------------------------------------

describe("AnalysisSidebar 组件", () => {
  function renderSidebar(activeView = "overview", corpusReady = false) {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const activeCorpus = corpusReady
      ? { corpusId: 1, rCorpusId: "r_test", status: "ready" as const, stale: false, documentCount: 10, contentHash: "abc123" }
      : null;
    return render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <AnalysisSidebar
            activeView={activeView as import("../components/AnalysisSidebar").AnalysisViewId}
            onSelect={vi.fn()}
            activeCorpus={activeCorpus}
            collapsed={false}
            onToggleCollapse={vi.fn()}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  it("渲染 4 个分组标题", () => {
    renderSidebar();
    expect(screen.getByText("统计概览")).toBeInTheDocument();
    expect(screen.getByText("知识结构")).toBeInTheDocument();
    expect(screen.getByText("文献库洞察")).toBeInTheDocument();
    expect(screen.getByText("AI 工具台")).toBeInTheDocument();
  });

  it("渲染全部 13 个视图条目", () => {
    renderSidebar("overview", true);
    // 每个分组下的视图 label
    expect(screen.getByText("领域概览")).toBeInTheDocument();
    expect(screen.getByText("核心期刊")).toBeInTheDocument();
    expect(screen.getByText("核心作者")).toBeInTheDocument();
    expect(screen.getByText("关键词热点")).toBeInTheDocument();
    expect(screen.getByText("主题地图")).toBeInTheDocument();
    expect(screen.getByText("知识脉络")).toBeInTheDocument();
    expect(screen.getByText("合作网络")).toBeInTheDocument();
    expect(screen.getByText("相关性筛选")).toBeInTheDocument();
    expect(screen.getByText("PRISMA")).toBeInTheDocument();
    expect(screen.getByText("语料对话")).toBeInTheDocument();
    expect(screen.getByText("AI 工具")).toBeInTheDocument();
    expect(screen.getByText("AI 综述")).toBeInTheDocument();
    expect(screen.getByText("导出报告")).toBeInTheDocument();
  });

  it("无语料时需要 corpus 的分组显示(未就绪)标记", () => {
    renderSidebar("overview", false);
    // 统计概览/知识结构/AI工具台 需 corpus，置灰
    const badges = screen.getAllByText("(未就绪)");
    // 3 个需要语料的分组（统计概览/知识结构/AI工具台）
    expect(badges.length).toBe(3);
  });

  it("ANALYSIS_GROUPS 共含 13 个视图", () => {
    const total = ANALYSIS_GROUPS.reduce((sum, g) => sum + g.views.length, 0);
    expect(total).toBe(13);
  });
});

// ---------------------------------------------------------------------------
// M3 新增：AnalysisView 数据流闸门测试（无语料场景）
// ---------------------------------------------------------------------------

describe("AnalysisView 数据流闸门", () => {
  it("无 activeCorpus 时显示构建语料按钮", () => {
    // 默认 mock 返回 activeCorpus: null
    renderAt("/projects/1/analysis/overview");
    expect(screen.getByText("构建分析语料")).toBeInTheDocument();
    // 无 corpus 时 AnalysisFrame 不渲染（h3 标题不存在）
    expect(screen.queryByRole("heading", { level: 3, name: "领域概览" })).toBeNull();
  });
});
