/**
 * QualityPanel.test.tsx — F5 降级/回退覆盖（codex F5 覆盖建议）。
 * 404 静默、未知类型回退 ql-pill-other、无问题清洁态。
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi, describe, it, expect } from "vitest";

vi.mock("../../api/client", async (orig) => {
  const actual = await orig<typeof import("../../api/client")>();
  return { ...actual, getQualityReport: vi.fn() };
});

import { getQualityReport, ApiError } from "../../api/client";
import { QualityPanel } from "../quality/QualityPanel";

function renderQP() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <QualityPanel projectId={5} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("QualityPanel 降级/回退", () => {
  it("404(尚未生成质检) → 静默不渲染", async () => {
    vi.mocked(getQualityReport).mockRejectedValue(new ApiError("NOT_FOUND", 404, "no report"));
    const { container } = renderQP();
    await waitFor(() => expect(container.querySelector(".ql-panel")).toBeNull());
  });

  it("未知 by_type → 回退 ql-pill-other", async () => {
    vi.mocked(getQualityReport).mockResolvedValue({
      total: 3,
      by_type: { weird_type: 2 },
      issues: [{ paper_id: 1, type: "weird_type", detail: "未知问题" }],
    });
    renderQP();
    await waitFor(() => expect(document.querySelector(".ql-pill-other")).toBeTruthy());
  });

  it("无问题 → 显示清洁态", async () => {
    vi.mocked(getQualityReport).mockResolvedValue({ total: 5, by_type: {}, issues: [] });
    renderQP();
    await screen.findByText(/未发现质量问题/);
  });
});
