/**
 * libraryM1.test.tsx — M1 三栏文献库新增单测
 *
 * 覆盖：
 * 1. LibFilterPanel 状态分面渲染（正确显示各状态计数）
 * 2. ScreeningMode 键盘快捷键触发 PATCH（I/E/M）
 * 3. LibPaperList 批量操作（批量设为 included）
 * 4. ImportDialog 文件选择后点击「开始导入」触发 onImport
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { vi, describe, it, expect } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { LibFilterPanel } from "../pages/library/LibFilterPanel";
import { ScreeningMode } from "../pages/library/ScreeningMode";
import { LibPaperList } from "../pages/library/LibPaperList";
import { ImportDialog } from "../pages/library/ImportDialog";
import type { ProjectPaperItem, InclusionStatus } from "../api/client";

/** P1-5 修复后 ScreeningMode 使用 usePaper（react-query），渲染需要 QueryClientProvider 包裹。
 *  每次测试创建独立 QueryClient，避免测试间缓存污染。 */
function renderWithQueryClient(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
  );
}

// ---- 1. LibFilterPanel 状态分面渲染 ----

describe("LibFilterPanel", () => {
  it("渲染全部五个状态分面并显示正确计数", () => {
    const counts = { all: 10, candidate: 5, included: 3, excluded: 1, maybe: 1 };
    render(
      <LibFilterPanel
        counts={counts}
        statusFilter="all"
        onStatusFilter={vi.fn()}
        search=""
        onSearch={vi.fn()}
      />
    );
    // 分面标签
    expect(screen.getByText("全部")).toBeInTheDocument();
    expect(screen.getByText("待筛选")).toBeInTheDocument();
    expect(screen.getByText("已纳入")).toBeInTheDocument();
    expect(screen.getByText("已排除")).toBeInTheDocument();
    expect(screen.getByText("待定")).toBeInTheDocument();
    // 计数显示
    expect(screen.getByText("10")).toBeInTheDocument(); // all
    expect(screen.getByText("5")).toBeInTheDocument();  // candidate
    expect(screen.getByText("3")).toBeInTheDocument();  // included
  });

  it("点击状态分面时调用 onStatusFilter", () => {
    const onStatusFilter = vi.fn();
    const counts = { all: 4, candidate: 2, included: 1, excluded: 1, maybe: 0 };
    render(
      <LibFilterPanel
        counts={counts}
        statusFilter="all"
        onStatusFilter={onStatusFilter}
        search=""
        onSearch={vi.fn()}
      />
    );
    // 点击「已纳入」分面
    fireEvent.click(screen.getByText("已纳入"));
    expect(onStatusFilter).toHaveBeenCalledWith("included");
  });

  it("active 状态分面有 active 类名", () => {
    const counts = { all: 4, candidate: 2, included: 1, excluded: 1, maybe: 0 };
    render(
      <LibFilterPanel
        counts={counts}
        statusFilter="included"
        onStatusFilter={vi.fn()}
        search=""
        onSearch={vi.fn()}
      />
    );
    const includedBtn = screen.getByRole("button", { name: /已纳入/ });
    expect(includedBtn.className).toContain("active");
  });
});

// ---- 2. ScreeningMode I/E/M 键盘快捷键 ----

describe("ScreeningMode 键盘快捷键", () => {
  const mockPaper: ProjectPaperItem = {
    paperId: 1,
    title: "测试文献标题",
    year: 2024,
    inclusionStatus: "candidate",
    screeningScore: 0.8,
    hasAbstract: true,
    hasPdf: false,
    ocrStatus: "none",
    hasExtraction: false,
  };

  // mock fetch（摘要请求）
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ abstract: "这是摘要内容", creators: ["张三"], year: 2024 }),
    }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("按 I 键触发 onDecide('included')", async () => {
    const onDecide = vi.fn().mockResolvedValue(undefined);
    // P1-5: ScreeningMode 改用 usePaper(react-query)，需 QueryClientProvider 包裹
    renderWithQueryClient(
      <ScreeningMode
        paper={mockPaper}
        current={0}
        total={5}
        researchQuestion="系统综述方法论"
        onDecide={onDecide}
        onClose={vi.fn()}
      />
    );
    fireEvent.keyDown(window, { key: "i" });
    await waitFor(() => {
      expect(onDecide).toHaveBeenCalledWith("included", undefined);
    });
  });

  it("按 M 键触发 onDecide('maybe')", async () => {
    const onDecide = vi.fn().mockResolvedValue(undefined);
    renderWithQueryClient(
      <ScreeningMode
        paper={mockPaper}
        current={0}
        total={5}
        researchQuestion=""
        onDecide={onDecide}
        onClose={vi.fn()}
      />
    );
    fireEvent.keyDown(window, { key: "m" });
    await waitFor(() => {
      expect(onDecide).toHaveBeenCalledWith("maybe", undefined);
    });
  });

  it("按 E 键打开排除理由弹层", () => {
    renderWithQueryClient(
      <ScreeningMode
        paper={mockPaper}
        current={0}
        total={5}
        researchQuestion=""
        onDecide={vi.fn()}
        onClose={vi.fn()}
      />
    );
    fireEvent.keyDown(window, { key: "e" });
    expect(screen.getByText("排除理由")).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "选择排除理由" })).toBeInTheDocument();
  });

  it("排除理由弹层确认后触发 onDecide('excluded', reason)", async () => {
    const onDecide = vi.fn().mockResolvedValue(undefined);
    renderWithQueryClient(
      <ScreeningMode
        paper={mockPaper}
        current={0}
        total={5}
        researchQuestion=""
        onDecide={onDecide}
        onClose={vi.fn()}
      />
    );
    // 打开排除弹层
    fireEvent.click(screen.getByRole("button", { name: /排除/ }));
    // 确认排除
    const confirmBtn = screen.getByRole("button", { name: "确认排除" });
    fireEvent.click(confirmBtn);
    await waitFor(() => {
      expect(onDecide).toHaveBeenCalledWith("excluded", expect.any(String));
    });
  });
});

// ---- 3. LibPaperList 批量操作 ----

describe("LibPaperList 批量操作", () => {
  const makePaper = (id: number, status: InclusionStatus = "candidate"): ProjectPaperItem => ({
    paperId: id,
    title: `文献 ${id}`,
    year: 2020 + id,
    inclusionStatus: status,
    hasAbstract: false,
    hasPdf: false,
    ocrStatus: "none",
    hasExtraction: false,
  });

  // 新 props 的默认值（P3-T2/T4）
  const aiProps = {
    extractionFilter: "all" as const,
    onExtractionFilter: vi.fn(),
    isBackfilling: false,
    isExtracting: false,
    backfillResult: null,
    extractResult: null,
    onBackfill: vi.fn(),
    onExtract: vi.fn(),
    onClearBackfillResult: vi.fn(),
    onClearExtractResult: vi.fn(),
  };

  it("有选中项时显示批量操作条", () => {
    const papers = [makePaper(1), makePaper(2)];
    render(
      <LibPaperList
        papers={papers}
        allPapers={papers}
        selected={new Set([1])}
        selectedPaperId={null}
        sortField="year"
        sortDir="desc"
        onSort={vi.fn()}
        onSelectRow={vi.fn()}
        onToggleSelect={vi.fn()}
        onSelectAll={vi.fn()}
        onBulkStatus={vi.fn()}
        onStartScreening={vi.fn()}
        onShowImport={vi.fn()}
        isBulkPending={false}
        {...aiProps}
      />
    );
    expect(screen.getByText("已选 1 篇")).toBeInTheDocument();
  });

  it("点击批量「纳入」按钮触发 onBulkStatus('included')", () => {
    const onBulkStatus = vi.fn();
    const papers = [makePaper(1), makePaper(2)];
    render(
      <LibPaperList
        papers={papers}
        allPapers={papers}
        selected={new Set([1, 2])}
        selectedPaperId={null}
        sortField="year"
        sortDir="desc"
        onSort={vi.fn()}
        onSelectRow={vi.fn()}
        onToggleSelect={vi.fn()}
        onSelectAll={vi.fn()}
        onBulkStatus={onBulkStatus}
        onStartScreening={vi.fn()}
        onShowImport={vi.fn()}
        isBulkPending={false}
        {...aiProps}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: "纳入" }));
    expect(onBulkStatus).toHaveBeenCalledWith("included");
  });

  it("无文献时显示空状态提示", () => {
    render(
      <LibPaperList
        papers={[]}
        allPapers={[]}
        selected={new Set()}
        selectedPaperId={null}
        sortField="year"
        sortDir="desc"
        onSort={vi.fn()}
        onSelectRow={vi.fn()}
        onToggleSelect={vi.fn()}
        onSelectAll={vi.fn()}
        onBulkStatus={vi.fn()}
        onStartScreening={vi.fn()}
        onShowImport={vi.fn()}
        isBulkPending={false}
        {...aiProps}
      />
    );
    expect(screen.getByText("暂无文献")).toBeInTheDocument();
  });
});

// ---- 4. ImportDialog 文件选择与提交 ----

describe("ImportDialog", () => {
  it("未选文件时「开始导入」按钮禁用", () => {
    render(
      <ImportDialog
        importing={false}
        result={undefined}
        error={null}
        onImport={vi.fn()}
        onClose={vi.fn()}
      />
    );
    const submitBtn = screen.getByRole("button", { name: "开始导入" });
    expect(submitBtn).toBeDisabled();
  });

  it("导入结果显示正确数字", () => {
    render(
      <ImportDialog
        importing={false}
        result={{ imported: 5, skipped: 2, failed: [], paperIds: [] }}
        error={null}
        onImport={vi.fn()}
        onClose={vi.fn()}
      />
    );
    expect(screen.getByText(/导入完成/)).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument(); // imported
    expect(screen.getByText(/重复跳过：2/)).toBeInTheDocument();
  });
});
