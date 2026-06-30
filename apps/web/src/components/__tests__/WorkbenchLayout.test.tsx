/**
 * WorkbenchLayout.test.tsx — F1 IA 重定位的单测护栏（codex P2#1）。
 *
 * 覆盖真实变更：landing 是"语料工作台"，且内嵌的 ProjectsPage 进入流不破坏
 * （"我的项目" / "新建 SLR 项目" / 项目名称输入仍在），④ 下游应用保留综述/分析入口。
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi, describe, it, expect } from "vitest";

// 有一个项目 → ④ 下游入口走 Link 深链（覆盖 latestPid 分支）
vi.mock("../../api/agentHooks", () => ({
  useProjects: () => ({
    data: { projects: [{ id: 7, name: "示例项目", createdAt: "2026-06-14T00:00:00Z" }] },
    isLoading: false,
    error: null,
  }),
  useCreateProject: () => ({ mutateAsync: vi.fn(), isPending: false }),
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
});
