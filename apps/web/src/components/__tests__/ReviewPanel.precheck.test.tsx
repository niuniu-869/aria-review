/**
 * ReviewPanel.precheck.test.tsx — 生成综述前置检查
 *
 * 目标：空项目 / 未纳入 / 无可读全文时，前端直接禁用生成并给出中文引导，
 * 不再让用户点击后看到后端裸 422/400。
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi, describe, it, expect, beforeEach } from "vitest";
import { asRCorpusId } from "../../api/corpusIds";

const CID = asRCorpusId("r1");

const { getAiJobSpy, listAiJobsSpy, createAiJobSpy, backfillFulltextSpy, trackSpy } = vi.hoisted(() => ({
  getAiJobSpy: vi.fn(),
  listAiJobsSpy: vi.fn(),
  createAiJobSpy: vi.fn(),
  backfillFulltextSpy: vi.fn(),
  trackSpy: vi.fn(),
}));

vi.mock("../../api/client", async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return {
    ...actual,
    getAiJob: (...a: unknown[]) => getAiJobSpy(...a),
    listAiJobs: (...a: unknown[]) => listAiJobsSpy(...a),
    createAiJob: (...a: unknown[]) => createAiJobSpy(...a),
    backfillFulltext: (...a: unknown[]) => backfillFulltextSpy(...a),
  };
});

// 埋点是 best-effort 网络副作用；单测里 mock 掉，避免渲染组件触发未声明的 fetch（codex P1-review）。
vi.mock("../../lib/track", () => ({ track: (...a: unknown[]) => trackSpy(...a) }));

import { ReviewPanel } from "../ReviewPanel";

function renderPanel(stats: { includedCount: number; readableFulltextCount: number }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={qc}>
      <ReviewPanel projectId="7" corpusId={CID} projectStats={stats} />
    </QueryClientProvider>,
  );
  return { ...view, qc };
}

function fillTopic() {
  fireEvent.change(screen.getByLabelText("研究主题"), {
    target: { value: "人工智能教育应用" },
  });
}

beforeEach(() => {
  localStorage.clear();
  getAiJobSpy.mockReset();
  listAiJobsSpy.mockReset();
  createAiJobSpy.mockReset();
  backfillFulltextSpy.mockReset();
  trackSpy.mockReset();
  listAiJobsSpy.mockResolvedValue({ jobs: [] });
  backfillFulltextSpy.mockResolvedValue({ total: 1, fetched: 1, skipped: 0, failed: [], remaining: 0 });
});

describe("ReviewPanel 生成前置检查", () => {
  it("空项目 includedCount=0：禁用生成，并引导去文献库纳排", () => {
    renderPanel({ includedCount: 0, readableFulltextCount: 0 });
    fillTopic();

    expect(screen.getByRole("button", { name: "生成综述" })).toBeDisabled();
    expect(screen.getByText("先纳入文献", { exact: false })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "去文献库纳排" })).toHaveAttribute(
      "href",
      "/projects/7/library",
    );
  });

  it("有全文但未纳入 includedCount=0：仍先要求纳排", () => {
    renderPanel({ includedCount: 0, readableFulltextCount: 2 });
    fillTopic();

    expect(screen.getByRole("button", { name: "生成综述" })).toBeDisabled();
    expect(screen.getByText("先纳入文献", { exact: false })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "去文献库纳排" })).toBeInTheDocument();
  });

  it("已纳入但 readableFulltextCount=0：禁用生成，并引导导入/解析全文", () => {
    renderPanel({ includedCount: 3, readableFulltextCount: 0 });
    fillTopic();

    expect(screen.getByRole("button", { name: "生成综述" })).toBeDisabled();
    expect(screen.getByText("先导入/解析全文", { exact: false })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "自动补全文" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "去文献库导入全文" })).toHaveAttribute(
      "href",
      "/projects/7/library",
    );
  });

  it("点击自动补全文后显示进度，成功时失效项目缓存并上报结果", async () => {
    let resolveBackfill!: (value: { total: number; fetched: number; skipped: number; failed: never[]; remaining: number }) => void;
    backfillFulltextSpy
      .mockResolvedValueOnce({ total: 2, fetched: 1, skipped: 1, failed: [], remaining: 1 })
      .mockReturnValueOnce(new Promise((resolve) => {
        resolveBackfill = resolve;
      }));
    const { qc } = renderPanel({ includedCount: 3, readableFulltextCount: 0 });
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");

    fireEvent.click(screen.getByRole("button", { name: "自动补全文" }));

    expect(await screen.findByText("已处理 1/2")).toBeInTheDocument();
    expect(trackSpy).toHaveBeenCalledWith("review_backfill_click", {}, 7);

    resolveBackfill({ total: 1, fetched: 1, skipped: 0, failed: [], remaining: 0 });

    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["project", 7] });
    });
    expect(trackSpy).toHaveBeenCalledWith("review_backfill_done", { succeeded: 2, failed: 0 }, 7);
  });

  it("自动补全文全部失败时显示摘要并保留手动导入链接", async () => {
    backfillFulltextSpy.mockResolvedValueOnce({
      total: 2,
      fetched: 0,
      skipped: 0,
      failed: [
        { paperId: 1, reason: "not found" },
        { paperId: 2, reason: "forbidden" },
      ],
      remaining: 0,
    });
    renderPanel({ includedCount: 3, readableFulltextCount: 0 });

    fireEvent.click(screen.getByRole("button", { name: "自动补全文" }));

    expect(await screen.findByText("自动补全文失败 2 篇，请手动导入 PDF。")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "去文献库导入全文" })).toBeInTheDocument();
    expect(trackSpy).toHaveBeenCalledWith("review_backfill_done", { succeeded: 0, failed: 2 }, 7);
  });

  it("没有可自动补全文候选时提示手动导入 PDF", async () => {
    backfillFulltextSpy.mockResolvedValueOnce({
      total: 0,
      fetched: 0,
      skipped: 0,
      failed: [],
      remaining: 0,
    });
    renderPanel({ includedCount: 3, readableFulltextCount: 0 });

    fireEvent.click(screen.getByRole("button", { name: "自动补全文" }));

    expect(await screen.findByText("未找到可自动补全文的文献，请手动导入 PDF。")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "去文献库导入全文" })).toBeInTheDocument();
  });

  it("no_included 卡不显示自动补全文按钮", () => {
    renderPanel({ includedCount: 0, readableFulltextCount: 0 });

    expect(screen.queryByRole("button", { name: "自动补全文" })).not.toBeInTheDocument();
  });

  it("已纳入且有可读全文：填写主题后生成按钮可用", () => {
    renderPanel({ includedCount: 3, readableFulltextCount: 2 });
    fillTopic();

    expect(screen.getByRole("button", { name: "生成综述" })).toBeEnabled();
    expect(screen.queryByText(/先纳入文献|先导入\/解析全文/)).not.toBeInTheDocument();
  });
});
