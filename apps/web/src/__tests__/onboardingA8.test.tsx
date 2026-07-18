/**
 * onboardingA8.test.tsx — A8 新手指导单测
 *
 * 覆盖：
 *  1. NextStepGuide：四阶段文案 + CTA 跳转目标正确；本会话关闭（sessionStorage）。
 *  2. StageBar：五阶段可点击导航到对应区。
 *  3. WelcomeTour：受控开关 / ESC / 遮罩关闭；hasOnboarded + markOnboarded localStorage。
 *  4. ProjectsPage：首次（无项目）显示 hero；有项目时收起为一行小提示。
 *
 * 策略：MemoryRouter 捕获导航；mock agentHooks 控制 stats / 项目列表。
 */
import { render, screen, fireEvent } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import type { ActiveCorpus } from "../api/agentHooks";
import { API_BASE } from "../api/client";
import { asDbCorpusId, asRCorpusId } from "../api/corpusIds";

import { StageBar } from "../components/shell/StageBar";
import { NextStepGuide } from "../components/onboarding/NextStepGuide";
import {
  WelcomeTour,
  GuideButton,
  hasOnboarded,
  markOnboarded,
} from "../components/onboarding/WelcomeTour";

// ---------------------------------------------------------------------------
// mock agentHooks（ProjectsPage 用 useProjects / useCreateProject）
// ---------------------------------------------------------------------------
const { mockUseProjects } = vi.hoisted(() => ({ mockUseProjects: vi.fn() }));

vi.mock("../api/agentHooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/agentHooks")>();
  return {
    ...actual,
    useProjects: mockUseProjects,
    useCreateProject: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useRenameProject: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useDeleteProject: () => ({ mutateAsync: vi.fn(), isPending: false }),
  };
});

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({
    user: { id: 7, email: "qa@example.com" },
    isLoading: false,
    isAuthenticated: true,
    refresh: vi.fn(),
    logout: vi.fn(),
  }),
}));

// 导入需放在 mock 之后
import { ProjectsPage, formatDate } from "../pages/ProjectsPage";

// ---------------------------------------------------------------------------
// localStorage / sessionStorage mock（jsdom opaque origin 无可用 Storage）
// ---------------------------------------------------------------------------
function makeStorageMock() {
  let store: Record<string, string> = {};
  return {
    getItem: (k: string) => store[k] ?? null,
    setItem: (k: string, v: string) => { store[k] = v; },
    removeItem: (k: string) => { delete store[k]; },
    clear: () => { store = {}; },
    get length() { return Object.keys(store).length; },
    key: (i: number) => Object.keys(store)[i] ?? null,
  };
}
Object.defineProperty(window, "localStorage", { value: makeStorageMock(), writable: true });
Object.defineProperty(window, "sessionStorage", { value: makeStorageMock(), writable: true });

// ---------------------------------------------------------------------------
// 工具：捕获当前路由路径
// ---------------------------------------------------------------------------
function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="location">{loc.pathname}</div>;
}

const READY_CORPUS: ActiveCorpus = {
  corpusId: asDbCorpusId(1), rCorpusId: asRCorpusId("r-1"), status: "ready",
  documentCount: 5, contentHash: "h", stale: false,
};

beforeEach(() => {
  try { sessionStorage.clear(); localStorage.clear(); } catch { /* noop */ }
  vi.clearAllMocks();
});
afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// 1. NextStepGuide
// ---------------------------------------------------------------------------
describe("NextStepGuide 下一步行动卡", () => {
  function renderGuide(stats: Parameters<typeof NextStepGuide>[0]["stats"], pid = 7) {
    return render(
      <MemoryRouter initialEntries={[`/projects/${pid}`]}>
        <NextStepGuide projectId={pid} stats={stats} />
        <LocationProbe />
      </MemoryRouter>,
    );
  }

  it("paperCount===0 → 检索建库，CTA 跳对话首页", () => {
    renderGuide({ paperCount: 0, includedCount: 0, activeCorpus: null });
    expect(screen.getByText("让 AI 帮你检索文献建库")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /去对话检索建库/ }));
    expect(screen.getByTestId("location").textContent).toBe("/projects/7");
  });

  it("有文献无纳入 → 筛选纳入，CTA 跳文献库", () => {
    renderGuide({ paperCount: 10, includedCount: 0, activeCorpus: null });
    expect(screen.getByText("筛选纳入文献")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /前往文献库筛选/ }));
    expect(screen.getByTestId("location").textContent).toBe("/projects/7/library");
  });

  it("有纳入无 ready 语料 → 构建语料，CTA 跳分析", () => {
    renderGuide({ paperCount: 10, includedCount: 5, activeCorpus: null });
    expect(screen.getByText("构建分析语料")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /前往分析区构建语料/ }));
    expect(screen.getByTestId("location").textContent).toBe("/projects/7/analysis/overview");
  });

  it("有 ready 语料 → 综述与导出，CTA 跳综述", () => {
    renderGuide({ paperCount: 10, includedCount: 5, activeCorpus: READY_CORPUS });
    expect(screen.getByText("开始综述与导出")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /前往综述与产出/ }));
    expect(screen.getByTestId("location").textContent).toBe("/projects/7/analysis/review");
  });

  it("点「稍后」关闭，且写 sessionStorage", () => {
    renderGuide({ paperCount: 0, includedCount: 0, activeCorpus: null });
    fireEvent.click(screen.getByRole("button", { name: /关闭下一步建议/ }));
    expect(screen.queryByText("让 AI 帮你检索文献建库")).toBeNull();
    let stored: string | null = null;
    try { stored = sessionStorage.getItem("bibliocn.nextstep.dismissed.7"); } catch { /* noop */ }
    expect(stored).toBe("1");
  });

  it("sessionStorage 已标记关闭时不渲染", () => {
    try { sessionStorage.setItem("bibliocn.nextstep.dismissed.9", "1"); } catch { /* noop */ }
    render(
      <MemoryRouter>
        <NextStepGuide projectId={9} stats={{ paperCount: 0, includedCount: 0, activeCorpus: null }} />
      </MemoryRouter>,
    );
    expect(screen.queryByText("让 AI 帮你检索文献建库")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// 2. StageBar 可点击导航
// ---------------------------------------------------------------------------
describe("StageBar 可交互工作流向导", () => {
  function renderBar(stats: Parameters<typeof StageBar>[0]["stats"], pid = "3") {
    // StageBar 用 useParams 取 pid；用一个 :pid 路由包裹它，
    // LocationProbe 放路由外，导航后仍保持挂载（能读到最新路径）。
    return render(
      <MemoryRouter initialEntries={[`/projects/${pid}`]}>
        <LocationProbe />
        <Routes>
          <Route path="/projects/:pid/*" element={<StageBar stats={stats} />} />
        </Routes>
      </MemoryRouter>,
    );
  }

  it("五步均为 button（可访问/可点击）", () => {
    renderBar({ paperCount: 0, includedCount: 0, activeCorpus: null });
    const steps = document.querySelectorAll("button.stage-step");
    expect(steps.length).toBe(5);
  });

  it("点「导入」跳文献库", () => {
    renderBar({ paperCount: 0, includedCount: 0, activeCorpus: null });
    fireEvent.click(screen.getByRole("button", { name: /第 1 步 导入/ }));
    expect(screen.getByTestId("location").textContent).toBe("/projects/3/library");
  });

  it("点「分析」跳分析概览", () => {
    renderBar({ paperCount: 0, includedCount: 0, activeCorpus: null });
    fireEvent.click(screen.getByRole("button", { name: /第 3 步 分析/ }));
    expect(screen.getByTestId("location").textContent).toBe("/projects/3/analysis/overview");
  });

  it("点「综述」跳 AI 综述", () => {
    renderBar({ paperCount: 0, includedCount: 0, activeCorpus: null });
    fireEvent.click(screen.getByRole("button", { name: /第 4 步 综述/ }));
    expect(screen.getByTestId("location").textContent).toBe("/projects/3/analysis/review");
  });

  it("点「导出」跳产出区", () => {
    renderBar({ paperCount: 0, includedCount: 0, activeCorpus: null });
    fireEvent.click(screen.getByRole("button", { name: /第 5 步 导出/ }));
    expect(screen.getByTestId("location").textContent).toBe("/projects/3/output");
  });

  it("保留 done/active 视觉类（既有逻辑不破坏）", () => {
    render(
      <MemoryRouter initialEntries={["/projects/3"]}>
        <Routes>
          <Route path="/projects/:pid" element={<StageBar stats={{ paperCount: 3, includedCount: 2, activeCorpus: READY_CORPUS }} />} />
        </Routes>
      </MemoryRouter>,
    );
    const steps = document.querySelectorAll(".stage-step");
    // ready corpus → current=3，前 3 步 done，第 4 步 active
    expect(steps[2].classList.contains("done")).toBe(true);
    expect(steps[3].classList.contains("active")).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// 3. WelcomeTour + localStorage 持久化
// ---------------------------------------------------------------------------
describe("WelcomeTour 新手指南浮层", () => {
  it("open=false 不渲染对话框", () => {
    render(<WelcomeTour open={false} onClose={vi.fn()} />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("open=true 渲染对话框 + 五步说明", () => {
    render(<WelcomeTour open onClose={vi.fn()} />);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText(/Aria Review/)).toBeInTheDocument();
    // 五步 label（在 hero/stage 之外的浮层内）
    expect(screen.getByText("导入")).toBeInTheDocument();
    expect(screen.getByText("综述")).toBeInTheDocument();
  });

  it("点「开始」触发 onClose", () => {
    const onClose = vi.fn();
    render(<WelcomeTour open onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: "开始" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("ESC 触发 onClose", () => {
    const onClose = vi.fn();
    render(<WelcomeTour open onClose={onClose} />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("点遮罩触发 onClose，点卡片内部不触发", () => {
    const onClose = vi.fn();
    render(<WelcomeTour open onClose={onClose} />);
    // 点卡片内部不关闭
    fireEvent.click(screen.getByRole("dialog"));
    expect(onClose).not.toHaveBeenCalled();
    // 点遮罩关闭
    fireEvent.click(screen.getByTestId("welcome-tour-overlay"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("hasOnboarded：首次为 false，markOnboarded 后为 true", () => {
    try { localStorage.clear(); } catch { /* noop */ }
    expect(hasOnboarded()).toBe(false);
    markOnboarded();
    expect(hasOnboarded()).toBe(true);
    let stored: string | null = null;
    try { stored = localStorage.getItem("bibliocn.onboarded"); } catch { /* noop */ }
    expect(stored).toBe("1");
  });

  it("hasOnboarded：传入用户时按用户维度持久化", () => {
    const user = { id: 42, email: "user@example.com" };
    expect(hasOnboarded(user)).toBe(false);
    markOnboarded(user);
    expect(hasOnboarded(user)).toBe(true);
    expect(hasOnboarded({ id: 43, email: "other@example.com" })).toBe(false);
    let stored: string | null = null;
    try { stored = localStorage.getItem("bibliocn.onboarded.42"); } catch { /* noop */ }
    expect(stored).toBe("1");
  });

  it("GuideButton 点击触发 onClick（老用户重开入口）", () => {
    const onClick = vi.fn();
    render(<GuideButton onClick={onClick} />);
    fireEvent.click(screen.getByRole("button", { name: "打开新手指南" }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// 4. ProjectsPage hero 首次显示 / 有项目收起
// ---------------------------------------------------------------------------
describe("ProjectsPage 欢迎 hero", () => {
  function renderPage() {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <ProjectsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  it("formatDate 对缺失/非法输入返回占位符", () => {
    expect(formatDate(undefined)).toBe("—");
    expect(formatDate(null)).toBe("—");
    expect(formatDate("not-a-date")).toBe("—");
  });

  it("首次（无项目）显示欢迎 hero + 五步工作流", () => {
    mockUseProjects.mockReturnValue({ data: { projects: [] }, isLoading: false, error: null });
    renderPage();
    expect(screen.getByLabelText("平台介绍")).toBeInTheDocument();
    expect(screen.getByLabelText("五步文献综述工作流")).toBeInTheDocument();
    // hero 内五步序号
    expect(screen.getByText("文献计量")).toBeInTheDocument();
  });

  it("有项目时 hero 收起，仅显示一行工作流提示", () => {
    mockUseProjects.mockReturnValue({
      data: { projects: [{ id: 1, name: "项目甲", createdAt: "2026-01-01T00:00:00Z" }] },
      isLoading: false,
      error: null,
    });
    renderPage();
    expect(screen.queryByLabelText("平台介绍")).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByLabelText("工作流提示")).toBeInTheDocument();
    expect(screen.getByText("项目甲")).toBeInTheDocument();
  });

  it("首屏项目加载失败时显示排查提示、原始错误和重试按钮", () => {
    const refetch = vi.fn();
    const friendlyMessage = `无法连接后端服务（${API_BASE}）。请确认后端服务已启动，或在项目根目录运行 docker compose up -d 后重试。`;
    const error = Object.assign(
      new Error(friendlyMessage),
      {
        friendlyMessage,
        originalMessage: "Failed to fetch",
        isFriendly: true,
      },
    );
    mockUseProjects.mockReturnValue({
      data: undefined,
      isLoading: false,
      isFetching: false,
      error,
      refetch,
    });
    renderPage();

    expect(screen.getByText(/无法连接后端服务/)).toBeInTheDocument();
    expect(screen.getByText(/docker compose up -d/)).toBeInTheDocument();
    expect(screen.getByText("查看原始错误")).toBeInTheDocument();
    expect(screen.getByText("Failed to fetch")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(refetch).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("新建 SLR 项目")).toBeNull();
  });
});
