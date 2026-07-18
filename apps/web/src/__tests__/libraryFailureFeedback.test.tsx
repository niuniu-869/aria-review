/**
 * libraryFailureFeedback.test.tsx — P2-5 Library 操作失败反馈
 *
 * 覆盖：
 * 1. AI 补全 mutation 500 失败时，列表工具条下方显示错误与重试按钮
 * 2. AI 解析 mutation 500 失败时，列表工具条下方显示错误与重试按钮
 * 3. ScreeningMode 决策保存 500 失败时，保留当前文献并显示可重试错误
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LibraryView } from "../pages/LibraryView";

const mocks = vi.hoisted(() => ({
  backfillMutate: vi.fn(),
  extractMutate: vi.fn(),
  patchMutateAsync: vi.fn(),
  backfillErrorMessage: "HTTP 500: backfill failed",
}));

vi.mock("../api/agentHooks", async () => {
  const React = await import("react");
  const papers = [
    {
      paperId: 1,
      title: "第一篇",
      year: 2024,
      inclusionStatus: "candidate",
      hasAbstract: true,
      hasPdf: false,
      ocrStatus: "none",
      hasExtraction: false,
    },
    {
      paperId: 2,
      title: "第二篇",
      year: 2023,
      inclusionStatus: "candidate",
      hasAbstract: true,
      hasPdf: false,
      ocrStatus: "none",
      hasExtraction: false,
    },
  ];

  return {
    useProjectPapers: () => ({ data: { papers }, isLoading: false, error: null }),
    useProject: () => ({ data: { researchQuestion: "系统综述" } }),
    useImportPapers: () => ({ mutate: vi.fn(), isPending: false, data: undefined, error: null, reset: vi.fn() }),
    usePatchInclusion: () => ({ mutateAsync: mocks.patchMutateAsync, isPending: false }),
    usePaper: () => ({
      data: { abstract: "这是摘要内容", creators: [{ literal: "张三" }] },
      isLoading: false,
    }),
    useBackfillMetadata: () => {
      const [error, setError] = React.useState<Error | null>(null);
      return {
        isPending: false,
        error,
        reset: () => setError(null),
        mutate: (vars: unknown, opts?: { onError?: (error: Error) => void }) => {
          mocks.backfillMutate(vars);
          const err = new Error(mocks.backfillErrorMessage);
          setError(err);
          opts?.onError?.(err);
        },
      };
    },
    useBackfillFulltext: () => ({
      isPending: false,
      error: null,
      reset: vi.fn(),
      mutateAsync: vi.fn().mockResolvedValue({ total: 0, fetched: 0, skipped: 0, failed: [], remaining: 0 }),
    }),
    useExtractStructured: () => {
      const [error, setError] = React.useState<Error | null>(null);
      return {
        isPending: false,
        error,
        reset: () => setError(null),
        mutate: (vars: unknown, opts?: { onError?: (error: Error) => void }) => {
          mocks.extractMutate(vars);
          const err = new Error("HTTP 500: extract failed");
          setError(err);
          opts?.onError?.(err);
        },
      };
    },
  };
});

function renderLibrary() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/projects/1/library"]}>
        <Routes>
          <Route path="/projects/:pid/library" element={<LibraryView />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Library 操作失败反馈", () => {
  beforeEach(() => {
    mocks.backfillMutate.mockClear();
    mocks.extractMutate.mockClear();
    mocks.patchMutateAsync.mockReset();
    mocks.backfillErrorMessage = "HTTP 500: backfill failed";
    mocks.patchMutateAsync.mockRejectedValue(new Error("HTTP 500: inclusion save failed"));
  });

  it("AI 补全 500 失败时显示错误信息与重试入口", async () => {
    renderLibrary();

    fireEvent.click(screen.getByRole("button", { name: /AI 补全元数据/ }));

    expect(await screen.findByText("AI 补全元数据失败，请重试。")).toBeInTheDocument();
    expect(screen.getByText("HTTP 500: backfill failed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试补全" })).toBeInTheDocument();
  });

  it("网络错误同样显示可见反馈，不静默失败", async () => {
    mocks.backfillErrorMessage = "Failed to fetch";
    renderLibrary();

    fireEvent.click(screen.getByRole("button", { name: /AI 补全元数据/ }));

    expect(await screen.findByText("AI 补全元数据失败，请重试。")).toBeInTheDocument();
    expect(screen.getByText("Failed to fetch")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试补全" })).toBeInTheDocument();
  });

  it("AI 解析 500 失败时显示错误信息与重试入口", async () => {
    renderLibrary();

    fireEvent.click(screen.getByRole("button", { name: /AI 解析/ }));

    expect(await screen.findByText("AI 解析结构化字段失败，请重试。")).toBeInTheDocument();
    expect(screen.getByText("HTTP 500: extract failed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试解析" })).toBeInTheDocument();
  });

  it("筛选决策保存失败时保留当前文献，不跳到下一篇", async () => {
    renderLibrary();

    fireEvent.click(screen.getByRole("button", { name: "进入筛选模式" }));
    const overlay = await screen.findByRole("dialog", { name: "文献筛选模式" });
    expect(within(overlay).getByRole("heading", { name: "第一篇" })).toBeInTheDocument();
    expect(within(overlay).getByText("1 / 2")).toBeInTheDocument();

    fireEvent.click(within(overlay).getByRole("button", { name: /纳入/ }));

    await waitFor(() => {
      expect(within(overlay).getByText("筛选决策保存失败，当前文献已保留，请重试。")).toBeInTheDocument();
    });
    expect(within(overlay).getByText("HTTP 500: inclusion save failed")).toBeInTheDocument();
    expect(within(overlay).getByRole("button", { name: "重试上次决策" })).toBeInTheDocument();
    expect(within(overlay).getByRole("heading", { name: "第一篇" })).toBeInTheDocument();
    expect(within(overlay).getByText("1 / 2")).toBeInTheDocument();
  });
});
