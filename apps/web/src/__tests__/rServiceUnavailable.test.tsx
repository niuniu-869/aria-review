/**
 * rServiceUnavailable.test.tsx — R 分析服务缺席时的开源体验提示
 *
 * 覆盖：
 * 1. TopBar 区分 Agent 不可达与 R 分析服务未启动。
 * 2. AnalysisView 在 agent up / R down 时显示可执行启动命令。
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";
import type { ActiveCorpus } from "../api/agentHooks";
import { asDbCorpusId, asRCorpusId } from "../api/corpusIds";

const { mockUseHealth, mockUseProject } = vi.hoisted(() => ({
  mockUseHealth: vi.fn(),
  mockUseProject: vi.fn(),
}));

vi.mock("../api/hooks", () => ({
  useHealth: mockUseHealth,
}));

vi.mock("../api/agentHooks", () => ({
  getPanelRCorpusId: (activeCorpus: ActiveCorpus | null | undefined) => activeCorpus?.rCorpusId ?? "",
  useProjects: () => ({ data: { projects: [] }, isLoading: false, error: null }),
  useProject: mockUseProject,
  useMaterializeCorpus: () => ({
    mutate: vi.fn(),
    isPending: false,
    isError: false,
    error: null,
  }),
  useBackfillFulltext: () => ({
    mutateAsync: vi.fn(),
    reset: vi.fn(),
    error: null,
  }),
}));

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({
    user: { id: 1, email: "qa@example.com" },
    isLoading: false,
    isAuthenticated: true,
    refresh: vi.fn(),
    logout: vi.fn(),
  }),
}));

import { TopBar } from "../components/shell/TopBar";
import { AnalysisView } from "../pages/AnalysisView";

function renderAnalysis() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/projects/7/analysis/overview"]}>
        <Routes>
          <Route path="/projects/:pid/analysis/:view" element={<AnalysisView />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("R 分析服务缺席提示", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("TopBar 在 agent up / R down 时提示 R 分析服务未启动", () => {
    mockUseHealth.mockReturnValue({
      data: { status: "ok", service: "agent", rService: "down" },
      isError: false,
    });

    render(
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>,
    );

    expect(screen.getByText("R 分析服务未启动")).toBeInTheDocument();
    expect(screen.queryByText("后端部分不可用")).not.toBeInTheDocument();
  });

  it("TopBar 使用统一产品副标题", () => {
    mockUseHealth.mockReturnValue({
      data: { status: "ok", service: "agent", rService: "up" },
      isError: false,
    });

    render(
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>,
    );

    expect(screen.getByText("可信文献综述 Agent 工作台")).toBeInTheDocument();
    expect(screen.queryByText("文献计量与综述助手")).toBeNull();
  });

  it("TopBar 在 Agent 不可达时给出独立提示", () => {
    mockUseHealth.mockReturnValue({ data: undefined, isError: true });

    render(
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>,
    );

    expect(screen.getByText("Agent 不可达")).toBeInTheDocument();
  });

  it("TopBar 在后台重试期间（pending 但已有失败）保持 Agent 不可达标签", () => {
    // 无数据 query 的 refetchInterval 会把 error 拉回 pending，凭 failureCount 避免标签振荡
    mockUseHealth.mockReturnValue({ data: undefined, isError: false, failureCount: 2 });

    render(
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>,
    );

    expect(screen.getByText("Agent 不可达")).toBeInTheDocument();
    expect(screen.queryByText("连接中")).not.toBeInTheDocument();
  });

  it("AnalysisView 在 agent up / R down 时显示启动引导与命令", () => {
    const activeCorpus: ActiveCorpus = {
      corpusId: asDbCorpusId(1),
      rCorpusId: asRCorpusId("r-corpus-1"),
      status: "ready",
      documentCount: 3,
      contentHash: "abc",
      stale: false,
    };
    mockUseHealth.mockReturnValue({
      data: { status: "ok", service: "agent", rService: "down" },
      isError: false,
    });
    mockUseProject.mockReturnValue({ data: { activeCorpus } });

    renderAnalysis();

    expect(screen.getByRole("status", { name: "R 分析服务未启动" })).toBeInTheDocument();
    expect(screen.getByText("分析功能需要 R 服务")).toBeInTheDocument();
    expect(screen.getByText("docker compose --profile analysis up -d")).toBeInTheDocument();
  });

  it("review 视图不依赖 R，R down 时不被启动引导拦截", () => {
    mockUseHealth.mockReturnValue({
      data: { status: "ok", service: "agent", rService: "down" },
      isError: false,
    });
    mockUseProject.mockReturnValue({ data: { activeCorpus: null } });

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/projects/7/analysis/review"]}>
          <Routes>
            <Route path="/projects/:pid/analysis/:view" element={<AnalysisView />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.queryByText("分析功能需要 R 服务")).not.toBeInTheDocument();
  });
});
