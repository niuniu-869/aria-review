/**
 * agentHooks.test.ts — W1 审查：mutation 库统计失效测试
 *
 * 覆盖：
 *   1. useImportPapers onSuccess 失效 projectPapers / projectLibraryStats / globalLibraryStats
 *   2. usePatchInclusion onSuccess 失效 projectPapers / projectLibraryStats / globalLibraryStats
 *   3. useMaterializeCorpus onSuccess 失效 project / projectLibraryStats
 */
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi, describe, it, expect, afterEach, beforeEach } from "vitest";
import React from "react";
import { useImportPapers, usePatchInclusion, useMaterializeCorpus } from "./agentHooks";

// ---- mock client ----
vi.mock("./client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./client")>();
  return {
    ...actual,
    importPapers: vi.fn().mockResolvedValue({ imported: 1, skipped: 0, failed: [], paperIds: [1] }),
    patchInclusion: vi.fn().mockResolvedValue({ paperId: 1, inclusionStatus: "included" }),
    materializeCorpus: vi.fn().mockResolvedValue({ corpusId: 1, rCorpusId: "x", status: "ready", documentCount: 1 }),
  };
});

afterEach(() => {
  vi.restoreAllMocks();
});

function makeWrapper(qc: QueryClient) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client: qc }, children);
  };
}

/** 从 spy 调用记录中提取所有 queryKey */
function spiedKeys(calls: unknown[][]): unknown[][] {
  return calls.map((c) => (c[0] as { queryKey?: unknown[] }).queryKey ?? []);
}

describe("useImportPapers — onSuccess 库统计失效", () => {
  let qc: QueryClient;
  const calls: unknown[][] = [];

  beforeEach(() => {
    calls.length = 0;
    qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    // 拦截 invalidateQueries，记录调用参数
    const orig = qc.invalidateQueries.bind(qc);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    qc.invalidateQueries = (...args: any[]) => {
      calls.push(args);
      return orig(...args);
    };
  });

  it("成功后失效 projectPapers / projectLibraryStats / globalLibraryStats", async () => {
    const { result } = renderHook(() => useImportPapers(42), {
      wrapper: makeWrapper(qc),
    });

    result.current.mutate({ files: [new File(["x"], "test.ris")] });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const keys = spiedKeys(calls);
    expect(keys).toContainEqual(["projectPapers", 42]);
    expect(keys).toContainEqual(["projectLibraryStats", 42]);
    expect(keys).toContainEqual(["globalLibraryStats"]);
  });
});

describe("usePatchInclusion — onSuccess 库统计失效", () => {
  let qc: QueryClient;
  const calls: unknown[][] = [];

  beforeEach(() => {
    calls.length = 0;
    qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const orig = qc.invalidateQueries.bind(qc);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    qc.invalidateQueries = (...args: any[]) => {
      calls.push(args);
      return orig(...args);
    };
  });

  it("成功后失效 projectPapers / projectLibraryStats / globalLibraryStats", async () => {
    const { result } = renderHook(() => usePatchInclusion(7), {
      wrapper: makeWrapper(qc),
    });

    result.current.mutate({ paperId: 1, inclusionStatus: "included" });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const keys = spiedKeys(calls);
    expect(keys).toContainEqual(["projectPapers", 7]);
    expect(keys).toContainEqual(["projectLibraryStats", 7]);
    expect(keys).toContainEqual(["globalLibraryStats"]);
  });
});

describe("useMaterializeCorpus — onSuccess 失效 project + projectLibraryStats", () => {
  let qc: QueryClient;
  const calls: unknown[][] = [];

  beforeEach(() => {
    calls.length = 0;
    qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const orig = qc.invalidateQueries.bind(qc);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    qc.invalidateQueries = (...args: any[]) => {
      calls.push(args);
      return orig(...args);
    };
  });

  it("成功后失效 project 和 projectLibraryStats", async () => {
    const { result } = renderHook(() => useMaterializeCorpus(3), {
      wrapper: makeWrapper(qc),
    });

    result.current.mutate();
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const keys = spiedKeys(calls);
    expect(keys).toContainEqual(["project", 3]);
    expect(keys).toContainEqual(["projectLibraryStats", 3]);
  });
});
