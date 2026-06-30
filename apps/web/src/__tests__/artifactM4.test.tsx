/**
 * artifactM4.test.tsx — M4 工件化 + Canvas + Grounding 前端单元测试
 *
 * 策略：
 *   1. ArtifactCard：渲染工件卡（类型徽章/标题/操作按钮）+ pin 状态
 *   2. GroundingOverlay：无 evidence 时渲染 markdown；有 evidence 时渲染溯源侧注
 *   3. ArtifactCanvas：渲染 Canvas 头部和正文
 *   4. ChatWorkbench（工件化集成）：展示已 pin 工件侧栏
 *
 * 全部使用 mock，无真实网络请求。
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import type { ArtifactItem } from "../api/client";

// ---- 全局 mock ----

vi.mock("../lib/markdown", () => ({
  renderMarkdown: (md: string) => `<p>${md}</p>`,
}));

const { mockUsePatchArtifact, mockUseArtifacts, mockUseCreateArtifact } = vi.hoisted(() => ({
  mockUsePatchArtifact: vi.fn(),
  mockUseArtifacts: vi.fn(),
  mockUseCreateArtifact: vi.fn(),
}));

vi.mock("../api/agentHooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/agentHooks")>();
  return {
    ...actual,
    usePatchArtifact: mockUsePatchArtifact,
    useArtifacts: mockUseArtifacts,
    useCreateArtifact: mockUseCreateArtifact,
    useProject: () => ({
      data: { name: "测试项目", paperCount: 5, includedCount: 3, researchQuestion: "测试问题", activeCorpus: null },
      isLoading: false,
      error: null,
    }),
  };
});

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    createRun: vi.fn(),
    streamAgentRun: vi.fn(),
    getRun: vi.fn().mockResolvedValue({ finalOutput: "## 综述\n内容", evidenceRefs: [] }),
  };
});

vi.mock("../api/hooks", () => ({
  useHealth: () => ({ data: undefined, isError: false }),
}));

import { ArtifactCard } from "../components/ArtifactCard";
import { ArtifactCanvas } from "../components/ArtifactCanvas";
import { GroundingOverlay } from "../components/GroundingOverlay";
import type { FrontendEvidenceRef } from "../components/GroundingOverlay";
import { ChatWorkbench } from "../pages/ChatWorkbench";

// ---- 测试数据 ----

const MOCK_ARTIFACT: ArtifactItem = {
  id: 1,
  projectId: 5,
  runId: 10,
  type: "review",
  title: "IPO 文本分析综述",
  pinned: false,
  order: 0,
};

const PINNED_ARTIFACT: ArtifactItem = {
  ...MOCK_ARTIFACT,
  id: 2,
  title: "已 Pin 综述",
  pinned: true,
};

// ---- Helper ----

function withProviders(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

function withRouterAndProviders(ui: React.ReactElement, path = "/projects/5") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/projects/:pid" element={ui} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ===========================================================================
// 1. ArtifactCard
// ===========================================================================

describe("ArtifactCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUsePatchArtifact.mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({ ...MOCK_ARTIFACT }),
      isPending: false,
    });
  });

  it("渲染类型徽章 review → '综述'", () => {
    withProviders(
      <ArtifactCard
        artifact={MOCK_ARTIFACT}
        projectId={5}
        onExpand={vi.fn()}
      />,
    );
    expect(screen.getByText("综述")).toBeInTheDocument();
  });

  it("渲染工件标题", () => {
    withProviders(
      <ArtifactCard
        artifact={MOCK_ARTIFACT}
        projectId={5}
        onExpand={vi.fn()}
      />,
    );
    expect(screen.getByText("IPO 文本分析综述")).toBeInTheDocument();
  });

  it("渲染「展开」和「Pin」按钮", () => {
    withProviders(
      <ArtifactCard
        artifact={MOCK_ARTIFACT}
        projectId={5}
        onExpand={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /展开/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Pin/ })).toBeInTheDocument();
  });

  it("已 pin 时按钮文字显示「已 Pin」", () => {
    withProviders(
      <ArtifactCard
        artifact={PINNED_ARTIFACT}
        projectId={5}
        onExpand={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /已 Pin/ })).toBeInTheDocument();
  });

  it("点击「展开」调用 onExpand 回调", () => {
    const onExpand = vi.fn();
    withProviders(
      <ArtifactCard
        artifact={MOCK_ARTIFACT}
        projectId={5}
        onExpand={onExpand}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /展开/ }));
    expect(onExpand).toHaveBeenCalledWith(MOCK_ARTIFACT);
  });

  it("点击「Pin」调用 patchArtifact.mutateAsync", async () => {
    const mockMutateAsync = vi.fn().mockResolvedValue({ ...MOCK_ARTIFACT, pinned: true });
    mockUsePatchArtifact.mockReturnValue({
      mutateAsync: mockMutateAsync,
      isPending: false,
    });
    withProviders(
      <ArtifactCard
        artifact={MOCK_ARTIFACT}
        projectId={5}
        onExpand={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Pin/ }));
    await waitFor(() => {
      expect(mockMutateAsync).toHaveBeenCalledWith({ aid: 1, pinned: true });
    });
  });

  it("有 onRerun 时渲染「重跑」按钮", () => {
    withProviders(
      <ArtifactCard
        artifact={MOCK_ARTIFACT}
        projectId={5}
        onExpand={vi.fn()}
        onRerun={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /重跑/ })).toBeInTheDocument();
  });
});

// ===========================================================================
// 2. GroundingOverlay
// ===========================================================================

describe("GroundingOverlay", () => {
  it("无 evidenceRefs 时直接渲染 markdownHtml", () => {
    const { container } = withProviders(
      <GroundingOverlay evidenceRefs={null} markdownHtml="<p>综述正文</p>" />,
    );
    expect(container.querySelector(".md")).not.toBeNull();
    expect(container.textContent).toContain("综述正文");
  });

  it("空 evidenceRefs 时直接渲染 markdownHtml", () => {
    const { container } = withProviders(
      <GroundingOverlay evidenceRefs={[]} markdownHtml="<p>正文</p>" />,
    );
    expect(container.querySelector(".md")).not.toBeNull();
  });

  it("有 evidenceRefs 时渲染溯源侧注", () => {
    const refs: FrontendEvidenceRef[] = [
      { paper_id: 1, span: "Smith (2020)", claim: "研究表明 Smith (2020) 提出了新方法。", match_quality: "green" },
    ];
    withProviders(
      <GroundingOverlay evidenceRefs={refs} markdownHtml="<p>综述</p>" />,
    );
    // 侧注应显示 "引用溯源"
    expect(screen.getByText(/引用溯源/)).toBeInTheDocument();
  });

  it("有 evidenceRefs 时渲染 claim 文本（点击展开 popover 后显示 paper id）", () => {
    const refs: FrontendEvidenceRef[] = [
      { paper_id: 42, span: "10.xxx/doi", claim: "研究 10.xxx/doi 证实。", match_quality: "green" },
    ];
    withProviders(
      <GroundingOverlay evidenceRefs={refs} markdownHtml="<p>内容</p>" />,
    );
    // 渲染 claim 文本作为可点击的溯源句
    expect(screen.getByText(/研究 10.xxx\/doi 证实/)).toBeInTheDocument();
    // 点击展开 popover
    fireEvent.click(screen.getByText(/研究 10.xxx\/doi 证实/));
    // popover 显示 paper id
    expect(screen.getByText(/Paper #42/)).toBeInTheDocument();
  });

  it("match_quality=yellow 点击 claim 后 popover 渲染「待核」徽章", () => {
    const refs: FrontendEvidenceRef[] = [
      { paper_id: 3, span: "Jones (2019)", claim: "Jones (2019) 认为。", match_quality: "yellow" },
    ];
    withProviders(
      <GroundingOverlay evidenceRefs={refs} markdownHtml="<p>内容</p>" />,
    );
    // 点击 claim 展开 popover
    fireEvent.click(screen.getByText(/Jones \(2019\) 认为/));
    // popover 里应有「待核」
    const badges = screen.getAllByText("待核");
    expect(badges.length).toBeGreaterThanOrEqual(1);
  });
});

// ===========================================================================
// 3. ArtifactCanvas
// ===========================================================================

describe("ArtifactCanvas", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUsePatchArtifact.mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue(MOCK_ARTIFACT),
      isPending: false,
    });
  });

  it("artifact=null 时不渲染任何内容", () => {
    const { container } = withProviders(
      <ArtifactCanvas
        artifact={null}
        projectId={5}
        content={null}
        onClose={vi.fn()}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("渲染 Canvas 头部类型徽章", () => {
    withProviders(
      <ArtifactCanvas
        artifact={MOCK_ARTIFACT}
        projectId={5}
        content="## 综述\n内容"
        onClose={vi.fn()}
      />,
    );
    // 头部应有「综述」徽章
    const badges = screen.getAllByText("综述");
    expect(badges.length).toBeGreaterThanOrEqual(1);
  });

  it("渲染 Canvas 中的 content", () => {
    const { container } = withProviders(
      <ArtifactCanvas
        artifact={MOCK_ARTIFACT}
        projectId={5}
        content="## 综述\n内容"
        onClose={vi.fn()}
      />,
    );
    // renderMarkdown mock 把内容包在 <p> 里，body 区 .md 里有内容
    expect(container.querySelector(".artifact-canvas-body")).not.toBeNull();
    // 检查至少包含标题中的「综述」
    const allTexts = screen.getAllByText(/综述/);
    expect(allTexts.length).toBeGreaterThanOrEqual(1);
  });

  it("点击关闭按钮调用 onClose", () => {
    const onClose = vi.fn();
    withProviders(
      <ArtifactCanvas
        artifact={MOCK_ARTIFACT}
        projectId={5}
        content="内容"
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /关闭 Canvas/ }));
    expect(onClose).toHaveBeenCalled();
  });
});

// ===========================================================================
// 4. ChatWorkbench 工件化集成（已 pin 工件侧栏）
// ===========================================================================

describe("ChatWorkbench 工件化", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUsePatchArtifact.mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue(PINNED_ARTIFACT),
      isPending: false,
    });
    mockUseCreateArtifact.mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue(MOCK_ARTIFACT),
      isPending: false,
    });
  });

  it("无 pin 工件时不显示「已 Pin 工件」侧栏", () => {
    mockUseArtifacts.mockReturnValue({
      data: { artifacts: [] },
      isLoading: false,
      refetch: vi.fn(),
    });
    withRouterAndProviders(<ChatWorkbench />);
    expect(screen.queryByText("已 Pin 工件")).toBeNull();
  });

  it("有 pin 工件时侧栏显示「已 Pin 工件」标题", () => {
    mockUseArtifacts.mockReturnValue({
      data: { artifacts: [PINNED_ARTIFACT] },
      isLoading: false,
      refetch: vi.fn(),
    });
    withRouterAndProviders(<ChatWorkbench />);
    expect(screen.getByText("已 Pin 工件")).toBeInTheDocument();
  });

  it("已 pin 工件卡渲染标题", () => {
    mockUseArtifacts.mockReturnValue({
      data: { artifacts: [PINNED_ARTIFACT] },
      isLoading: false,
      refetch: vi.fn(),
    });
    withRouterAndProviders(<ChatWorkbench />);
    expect(screen.getByText("已 Pin 综述")).toBeInTheDocument();
  });
});
