/**
 * aiParseP3.test.tsx — P3-T2/T4 AI 解析 UI 测试
 *
 * 覆盖：
 * 1. 工具栏「AI 补全元数据」按钮 — 触发 onBackfill + 进行中态
 * 2. 工具栏「AI 解析（结构化）」按钮 — 触发 onExtract + 进行中态
 * 3. backfillResult 反馈条 — 显示 updated/skipped/failed/available
 * 4. extractResult 反馈条 — 显示 extracted/skipped/available
 * 5. 已解析过滤 chip — 全部/已解析/未解析切换
 * 6. 详情卡：extraction 有数据时渲染五字段
 * 7. 详情卡：extraction 为 null 时渲染"尚未 AI 解析"提示
 * 8. 详情卡：extraction 为 undefined 时不渲染卡
 * 9. useBackfillMetadata onSuccess 失效正确 queryKeys
 * 10. useExtractStructured onSuccess 失效正确 queryKeys
 */
import { render, screen, fireEvent } from "@testing-library/react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";
import React from "react";
import { LibPaperList } from "../pages/library/LibPaperList";
import { LibPaperDetail } from "../pages/library/LibPaperDetail";
import { useBackfillMetadata, useExtractStructured } from "../api/agentHooks";
import type { BackfillMetadataResult, ExtractStructuredResult, PaperDetail, ProjectPaperItem } from "../api/client";

// ---- 辅助 ----

function makeWrapper(qc: QueryClient) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client: qc }, children);
  };
}

// makePaper kept for possible future tests
const _makePaper = (id: number, hasExtraction = false): ProjectPaperItem => ({
  paperId: id,
  title: `文献 ${id}`,
  year: 2020,
  inclusionStatus: "candidate",
  hasAbstract: false,
  hasPdf: false,
  ocrStatus: "none",
  hasExtraction,
});
void _makePaper; // suppress unused warning

const baseListProps = {
  allPapers: [] as ProjectPaperItem[],
  selected: new Set<number>(),
  selectedPaperId: null,
  sortField: "year" as const,
  sortDir: "desc" as const,
  onSort: vi.fn(),
  onSelectRow: vi.fn(),
  onToggleSelect: vi.fn(),
  onSelectAll: vi.fn(),
  onBulkStatus: vi.fn(),
  onStartScreening: vi.fn(),
  onShowImport: vi.fn(),
  isBulkPending: false,
  extractionFilter: "all" as const,
  onExtractionFilter: vi.fn(),
  backfillResult: null,
  extractResult: null,
  onClearBackfillResult: vi.fn(),
  onClearExtractResult: vi.fn(),
};

// ======================================================
// 1-5: LibPaperList AI 按钮与反馈
// ======================================================

describe("LibPaperList — AI 补全元数据按钮", () => {
  it("渲染「AI 补全元数据」按钮", () => {
    const onBackfill = vi.fn();
    render(
      <LibPaperList
        papers={[]}
        {...baseListProps}
        isBackfilling={false}
        isExtracting={false}
        onBackfill={onBackfill}
        onExtract={vi.fn()}
      />
    );
    expect(screen.getByRole("button", { name: /AI 补全元数据/ })).toBeInTheDocument();
  });

  it("点击「AI 补全元数据」触发 onBackfill", () => {
    const onBackfill = vi.fn();
    render(
      <LibPaperList
        papers={[]}
        {...baseListProps}
        isBackfilling={false}
        isExtracting={false}
        onBackfill={onBackfill}
        onExtract={vi.fn()}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: /AI 补全元数据/ }));
    expect(onBackfill).toHaveBeenCalledOnce();
  });

  it("isBackfilling=true 时按钮禁用并显示「补全中…」", () => {
    render(
      <LibPaperList
        papers={[]}
        {...baseListProps}
        isBackfilling={true}
        isExtracting={false}
        onBackfill={vi.fn()}
        onExtract={vi.fn()}
      />
    );
    const btn = screen.getByRole("button", { name: /补全中/ });
    expect(btn).toBeDisabled();
  });
});

describe("LibPaperList — AI 解析（结构化）按钮", () => {
  it("渲染「AI 解析（结构化）」按钮", () => {
    render(
      <LibPaperList
        papers={[]}
        {...baseListProps}
        isBackfilling={false}
        isExtracting={false}
        onBackfill={vi.fn()}
        onExtract={vi.fn()}
      />
    );
    expect(screen.getByRole("button", { name: /AI 解析/ })).toBeInTheDocument();
  });

  it("点击「AI 解析」触发 onExtract", () => {
    const onExtract = vi.fn();
    render(
      <LibPaperList
        papers={[]}
        {...baseListProps}
        isBackfilling={false}
        isExtracting={false}
        onBackfill={vi.fn()}
        onExtract={onExtract}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: /AI 解析/ }));
    expect(onExtract).toHaveBeenCalledOnce();
  });

  it("isExtracting=true 时「解析中…」按钮禁用", () => {
    render(
      <LibPaperList
        papers={[]}
        {...baseListProps}
        isBackfilling={false}
        isExtracting={true}
        onBackfill={vi.fn()}
        onExtract={vi.fn()}
      />
    );
    const btn = screen.getByRole("button", { name: /解析中/ });
    expect(btn).toBeDisabled();
  });
});

describe("LibPaperList — backfillResult 反馈条", () => {
  const result: BackfillMetadataResult = {
    processed: 10, updated: 7, skipped: 2, failed: 1, available: 3,
  };

  it("显示 updated/skipped/failed 计数", () => {
    render(
      <LibPaperList
        papers={[]}
        {...baseListProps}
        isBackfilling={false}
        isExtracting={false}
        backfillResult={result}
        onBackfill={vi.fn()}
        onExtract={vi.fn()}
      />
    );
    expect(screen.getByText("7")).toBeInTheDocument(); // updated (in <strong>)
    expect(screen.getByText(/待补 3 篇/)).toBeInTheDocument();
  });

  it("点击 × 按钮触发 onClearBackfillResult", () => {
    const onClearBackfillResult = vi.fn();
    render(
      <LibPaperList
        papers={[]}
        {...baseListProps}
        isBackfilling={false}
        isExtracting={false}
        backfillResult={result}
        onBackfill={vi.fn()}
        onExtract={vi.fn()}
        onClearBackfillResult={onClearBackfillResult}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: "关闭补全反馈" }));
    expect(onClearBackfillResult).toHaveBeenCalledOnce();
  });
});

describe("LibPaperList — extractResult 反馈条", () => {
  const result: ExtractStructuredResult = {
    processed: 5, extracted: 4, skipped: 1, failed: 0, available: 6,
  };

  it("显示 extracted + 待解析提示", () => {
    render(
      <LibPaperList
        papers={[]}
        {...baseListProps}
        isBackfilling={false}
        isExtracting={false}
        extractResult={result}
        onBackfill={vi.fn()}
        onExtract={vi.fn()}
      />
    );
    expect(screen.getByText("4")).toBeInTheDocument(); // extracted
    expect(screen.getByText(/待解析 6 篇/)).toBeInTheDocument();
  });

  it("点击 × 按钮触发 onClearExtractResult", () => {
    const onClearExtractResult = vi.fn();
    render(
      <LibPaperList
        papers={[]}
        {...baseListProps}
        isBackfilling={false}
        isExtracting={false}
        extractResult={result}
        onBackfill={vi.fn()}
        onExtract={vi.fn()}
        onClearExtractResult={onClearExtractResult}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: "关闭解析反馈" }));
    expect(onClearExtractResult).toHaveBeenCalledOnce();
  });
});

describe("LibPaperList — 已解析过滤 chip", () => {
  it("渲染全部/已解析/未解析三个 chip", () => {
    render(
      <LibPaperList
        papers={[]}
        {...baseListProps}
        isBackfilling={false}
        isExtracting={false}
        onBackfill={vi.fn()}
        onExtract={vi.fn()}
      />
    );
    expect(screen.getByRole("button", { name: "全部" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "已解析" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "未解析" })).toBeInTheDocument();
  });

  it("点击「已解析」chip 触发 onExtractionFilter('extracted')", () => {
    const onExtractionFilter = vi.fn();
    render(
      <LibPaperList
        papers={[]}
        {...baseListProps}
        onExtractionFilter={onExtractionFilter}
        isBackfilling={false}
        isExtracting={false}
        onBackfill={vi.fn()}
        onExtract={vi.fn()}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: "已解析" }));
    expect(onExtractionFilter).toHaveBeenCalledWith("extracted");
  });

  it("点击「未解析」chip 触发 onExtractionFilter('not-extracted')", () => {
    const onExtractionFilter = vi.fn();
    render(
      <LibPaperList
        papers={[]}
        {...baseListProps}
        onExtractionFilter={onExtractionFilter}
        isBackfilling={false}
        isExtracting={false}
        onBackfill={vi.fn()}
        onExtract={vi.fn()}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: "未解析" }));
    expect(onExtractionFilter).toHaveBeenCalledWith("not-extracted");
  });

  it("active chip 有 active 类名", () => {
    render(
      <LibPaperList
        papers={[]}
        {...baseListProps}
        extractionFilter="extracted"
        isBackfilling={false}
        isExtracting={false}
        onBackfill={vi.fn()}
        onExtract={vi.fn()}
      />
    );
    const extractedBtn = screen.getByRole("button", { name: "已解析" });
    expect(extractedBtn.className).toContain("active");
  });
});

// ======================================================
// 6-8: LibPaperDetail 结构化抽取卡
// ======================================================

describe("LibPaperDetail — ExtractionCard", () => {
  beforeEach(() => {
    // mock usePaper hook via fetch
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  function renderDetail(paperDetail: PaperDetail) {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    // 直接注入缓存，避免真实 HTTP 请求
    qc.setQueryData(["paper", 1, 10], paperDetail);
    return render(
      <QueryClientProvider client={qc}>
        <LibPaperDetail pid={1} paperId={10} />
      </QueryClientProvider>
    );
  }

  it("extraction 有数据时渲染五字段标签", () => {
    renderDetail({
      paperId: 10,
      title: "测试",
      inclusionStatus: "candidate",
      extraction: {
        researchQuestion: "研究问题内容",
        method: "方法论详述",
        findings: "研究发现内容",
        dataset: "数据集A",
        contribution: "贡献说明",
      },
    });
    expect(screen.getByText("研究问题")).toBeInTheDocument();
    expect(screen.getByText("研究问题内容")).toBeInTheDocument();
    expect(screen.getByText("研究方法")).toBeInTheDocument();
    expect(screen.getByText("方法论详述")).toBeInTheDocument();
    // findings 字段标签是"主要结论"，内容是"研究发现内容"
    expect(screen.getByText("主要结论")).toBeInTheDocument(); // dt 标签
    expect(screen.getByText("研究发现内容")).toBeInTheDocument();
    expect(screen.getByText("数据集")).toBeInTheDocument();
    expect(screen.getByText("学术贡献")).toBeInTheDocument();
  });

  it("extraction 部分字段为 null 时显示「（未抽取）」", () => {
    renderDetail({
      paperId: 10,
      title: "测试",
      inclusionStatus: "candidate",
      extraction: {
        researchQuestion: "有内容",
        method: null,
        findings: null,
        dataset: null,
        contribution: null,
      },
    });
    // 4个字段为null，应有4个"（未抽取）"
    const notExtracted = screen.getAllByText("（未抽取）");
    expect(notExtracted.length).toBe(4);
  });

  it("extraction 为 null 时显示「尚未 AI 解析」提示", () => {
    renderDetail({
      paperId: 10,
      title: "测试",
      inclusionStatus: "candidate",
      extraction: null,
    });
    expect(screen.getByText(/尚未 AI 解析/)).toBeInTheDocument();
  });

  it("extraction 为 undefined 时不渲染 AI 解析卡", () => {
    renderDetail({
      paperId: 10,
      title: "测试",
      inclusionStatus: "candidate",
    });
    expect(screen.queryByText("AI 结构化解析")).not.toBeInTheDocument();
  });
});

// ======================================================
// 9-10: hooks queryKey 失效测试
// ======================================================

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    backfillMetadata: vi.fn().mockResolvedValue({
      processed: 5, updated: 3, skipped: 2, failed: 0, available: 0,
    }),
    extractStructured: vi.fn().mockResolvedValue({
      processed: 4, extracted: 4, skipped: 0, failed: 0, available: 2,
    }),
  };
});

function spiedKeys(calls: unknown[][]): unknown[][] {
  return calls.map((c) => (c[0] as { queryKey?: unknown[] }).queryKey ?? []);
}

describe("useBackfillMetadata — onSuccess 失效 queryKeys", () => {
  let qc: QueryClient;
  const calls: unknown[][] = [];

  beforeEach(() => {
    calls.length = 0;
    qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const orig = qc.invalidateQueries.bind(qc);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    qc.invalidateQueries = (...args: any[]) => { calls.push(args); return orig(...args); };
  });

  it("成功后失效 projectPapers / projectLibraryStats / globalLibraryStats / project", async () => {
    const { result } = renderHook(() => useBackfillMetadata(5), {
      wrapper: makeWrapper(qc),
    });
    result.current.mutate({ onlyMissing: true });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const keys = spiedKeys(calls);
    expect(keys).toContainEqual(["projectPapers", 5]);
    expect(keys).toContainEqual(["projectLibraryStats", 5]);
    expect(keys).toContainEqual(["globalLibraryStats"]);
    expect(keys).toContainEqual(["project", 5]);
  });
});

describe("useExtractStructured — onSuccess 失效 queryKeys", () => {
  let qc: QueryClient;
  const calls: unknown[][] = [];

  beforeEach(() => {
    calls.length = 0;
    qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const orig = qc.invalidateQueries.bind(qc);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    qc.invalidateQueries = (...args: any[]) => { calls.push(args); return orig(...args); };
  });

  it("成功后失效 projectPapers / projectLibraryStats / globalLibraryStats / project / paper(pid)", async () => {
    const { result } = renderHook(() => useExtractStructured(5), {
      wrapper: makeWrapper(qc),
    });
    result.current.mutate({ reextract: false });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const keys = spiedKeys(calls);
    expect(keys).toContainEqual(["projectPapers", 5]);
    expect(keys).toContainEqual(["projectLibraryStats", 5]);
    expect(keys).toContainEqual(["globalLibraryStats"]);
    expect(keys).toContainEqual(["project", 5]);
    expect(keys).toContainEqual(["paper", 5]);
  });
});
