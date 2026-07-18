/**
 * WorkbenchLayout.test.tsx — F1 IA 重定位的单测护栏（codex P2#1）。
 *
 * 覆盖真实变更：landing 是"语料工作台"，且内嵌的 ProjectsPage 进入流不破坏
 * （"我的项目" / "新建 SLR 项目" / 项目名称输入仍在），④ 下游应用保留综述/分析入口。
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi, describe, it, expect, beforeEach } from "vitest";

const { mockUseProjects, mockRefetch } = vi.hoisted(() => ({
  mockUseProjects: vi.fn(),
  mockRefetch: vi.fn(),
}));

vi.mock("../../api/agentHooks", () => ({
  useProjects: () => mockUseProjects(),
  useCreateProject: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useRenameProject: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useDeleteProject: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({
    user: { id: 1, email: "qa@example.com" },
    isLoading: false,
    isAuthenticated: true,
    refresh: vi.fn(),
    logout: vi.fn(),
  }),
}));

import { WorkbenchLayout } from "../workbench/WorkbenchLayout";

function renderWorkbench() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <WorkbenchLayout />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("F1 WorkbenchLayout landing", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseProjects.mockReturnValue({
      data: { projects: [{ id: 7, name: "示例项目", createdAt: "2026-06-14T00:00:00Z" }] },
      isLoading: false,
      isFetching: false,
      error: null,
      refetch: mockRefetch,
    });
  });

  it("标题是语料工作台（不是综述生成器）", () => {
    renderWorkbench();
    expect(screen.getByRole("heading", { name: /语料工作台/ })).toBeInTheDocument();
  });

  it("内嵌 ProjectsPage 的进入流不破坏（我的项目 + 新建 SLR 项目 + 项目名称输入）", () => {
    renderWorkbench();
    expect(screen.getByText("我的项目")).toBeInTheDocument();
    expect(screen.getByText("新建 SLR 项目")).toBeInTheDocument();
    expect(screen.getByLabelText("项目名称 *")).toBeInTheDocument();
  });

  it("语料生产线四段标题齐全", () => {
    renderWorkbench();
    // 每段标题在"流水卡 + 段落 head"各出现一次，用 getAllByText 容多处
    for (const t of ["导入文档", "Agent 自主加工", "结构化语料库", "下游应用"]) {
      expect(screen.getAllByText(t).length).toBeGreaterThan(0);
    }
  });

  it("④ 下游应用保留综述/分析入口（有项目时深链到该项目）", () => {
    renderWorkbench();
    const review = screen.getByRole("link", { name: "AI 综述" });
    expect(review).toHaveAttribute("href", "/projects/7/analysis/review");
    const analysis = screen.getByRole("link", { name: "文献计量分析" });
    expect(analysis).toHaveAttribute("href", "/projects/7/analysis/overview");
  });

  it("项目列表 error 态展示错误与重试入口", () => {
    mockUseProjects.mockReturnValue({
      data: undefined,
      isLoading: false,
      isFetching: false,
      error: new Error("项目服务失败"),
      refetch: mockRefetch,
    });

    renderWorkbench();

    expect(screen.getAllByText("项目服务失败").length).toBeGreaterThan(0);
    fireEvent.click(screen.getAllByRole("button", { name: "重试" })[0]);
    expect(mockRefetch).toHaveBeenCalled();
  });
});
