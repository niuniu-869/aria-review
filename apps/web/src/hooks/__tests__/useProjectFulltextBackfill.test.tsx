import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { mutateAsync, resetMutation } = vi.hoisted(() => ({
  mutateAsync: vi.fn(),
  resetMutation: vi.fn(),
}));

vi.mock("../../api/agentHooks", () => ({
  useBackfillFulltext: () => ({
    mutateAsync,
    reset: resetMutation,
    error: null,
  }),
}));

vi.mock("../../api/useSciverseSettings", () => ({
  useSciverseSettings: () => ({
    settings: { apiToken: "user-token", baseUrl: "https://sciverse.test" },
  }),
}));

import { useProjectFulltextBackfill } from "../useProjectFulltextBackfill";

function makeWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

describe("useProjectFulltextBackfill", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("分批执行并聚合 fetched、failed 与进度", async () => {
    mutateAsync
      .mockResolvedValueOnce({
        total: 75,
        fetched: 49,
        failed: [{ paperId: 10, reason: "not found" }],
        skipped: 25,
        remaining: 25,
      })
      .mockResolvedValueOnce({
        total: 25,
        fetched: 24,
        failed: [{ paperId: 11, reason: "forbidden" }],
        skipped: 0,
        remaining: 0,
      });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useProjectFulltextBackfill(7), {
      wrapper: makeWrapper(queryClient),
    });

    let aggregate;
    await act(async () => {
      aggregate = await result.current.run();
    });

    expect(aggregate).toEqual({
      total: 75,
      fetched: 73,
      failed: [
        { paperId: 10, reason: "not found" },
        { paperId: 11, reason: "forbidden" },
      ],
      skipped: 0,
      remaining: 0,
    });
    expect(mutateAsync).toHaveBeenNthCalledWith(2, {
      maxPapers: 50,
      excludePaperIds: [10],
      sciverse: { apiToken: "user-token", baseUrl: "https://sciverse.test" },
    });
    expect(result.current.progress).toEqual({ done: 75, total: 75, failed: 2 });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["project", 7] });
  });

  it("跨批保留全部失败项，并在后续批次排除已失败论文", async () => {
    mutateAsync
      .mockResolvedValueOnce({
        total: 3,
        fetched: 0,
        failed: [
          { paperId: 1, reason: "missing" },
          { paperId: 2, reason: "timeout" },
        ],
        skipped: 1,
        remaining: 1,
      })
      .mockResolvedValueOnce({
        total: 1,
        fetched: 0,
        failed: [{ paperId: 3, reason: "forbidden" }],
        skipped: 0,
        remaining: 0,
      });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result } = renderHook(() => useProjectFulltextBackfill(9), {
      wrapper: makeWrapper(queryClient),
    });

    await act(async () => {
      await result.current.run();
    });

    await waitFor(() => expect(result.current.result?.failed).toHaveLength(3));
    expect(result.current.result?.fetched).toBe(0);
    expect(result.current.progress).toEqual({ done: 3, total: 3, failed: 3 });
    expect(mutateAsync).toHaveBeenNthCalledWith(2, expect.objectContaining({
      excludePaperIds: [1, 2],
    }));
  });
});
