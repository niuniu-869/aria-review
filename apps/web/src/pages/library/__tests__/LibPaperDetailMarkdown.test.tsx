/**
 * LibPaperDetailMarkdown.test.tsx — Phase 3：MinerU 解析全文折叠区
 *
 * 覆盖：
 *   ① 默认折叠：不拉取、不渲染全文内容。
 *   ② 点击展开：调 getPaperMarkdown → 渲染 markdown + 「由 MinerU 解析」副标题。
 *   ③ available=false：展开后显示「暂无 MinerU 解析全文」提示。
 */
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// usePaper 返回一篇最小 paper（含 extraction=null 走空态）。
const { mockUsePaper, mockGetPaperMarkdown } = vi.hoisted(() => ({
  mockUsePaper: vi.fn(),
  mockGetPaperMarkdown: vi.fn(),
}));
vi.mock("../../../api/agentHooks", async () => {
  const actual = await vi.importActual<typeof import("../../../api/agentHooks")>(
    "../../../api/agentHooks",
  );
  return { ...actual, usePaper: mockUsePaper };
});
vi.mock("../../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../../api/client")>(
    "../../../api/client",
  );
  return { ...actual, getPaperMarkdown: mockGetPaperMarkdown };
});

import { LibPaperDetail } from "../LibPaperDetail";

function renderDetail() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <LibPaperDetail pid={5} paperId={42} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockUsePaper.mockReturnValue({
    data: { id: 42, title: "测试文献", inclusionStatus: "included", extraction: null },
    isLoading: false,
    error: null,
  });
  mockGetPaperMarkdown.mockReset();
});

describe("LibPaperDetail · MinerU 解析全文折叠区", () => {
  it("默认折叠：不拉取 markdown、不渲染全文", () => {
    renderDetail();
    expect(screen.getByRole("button", { name: /MinerU 解析全文/ })).toBeTruthy();
    expect(mockGetPaperMarkdown).not.toHaveBeenCalled();
  });

  it("展开后渲染解析全文 + 「由 MinerU 解析」副标题", async () => {
    mockGetPaperMarkdown.mockResolvedValue({
      available: true,
      markdown: "# 标题\n\n这是 MinerU 解析的正文段落。",
      length: 1234,
      truncated: false,
      sha256: "abcdef",
    });
    renderDetail();
    fireEvent.click(screen.getByRole("button", { name: /MinerU 解析全文/ }));
    await waitFor(() => expect(mockGetPaperMarkdown).toHaveBeenCalledWith(5, 42));
    await screen.findByText(/由 MinerU 解析/);
    await screen.findByText(/MinerU 解析的正文段落/);
  });

  it("available=false：展开后提示暂无解析全文", async () => {
    mockGetPaperMarkdown.mockResolvedValue({
      available: false,
      markdown: "",
      length: 0,
      truncated: false,
      sha256: null,
    });
    renderDetail();
    fireEvent.click(screen.getByRole("button", { name: /MinerU 解析全文/ }));
    await screen.findByText(/暂无 MinerU 解析全文/);
  });
});
