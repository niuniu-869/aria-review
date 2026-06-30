/**
 * TrustCard.test.tsx — Phase 2 可信凭证卡测试
 *
 * 覆盖：
 *   ① 正常（有指标，scoreable）→ 渲染「溯源命中率」等百分比数字 + 哈希链事件数。
 *   ② scoreable:false → 渲染「不可评分」+ 诚实提示，且不出现伪装的「100%」。
 *   ③ 404（run 无 grounding）→ 静默不渲染。
 */
import { render, screen, waitFor } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// mock client：getGrounding / getRunLog 由测试逐例覆盖。
// ApiError 用真实实现（404 静默判定依赖 instanceof + status），故保留 importActual。
// 用 vi.hoisted 让 mock fn 与 vi.mock 工厂同被提升，避免「before initialization」。
const { mockGetGrounding } = vi.hoisted(() => ({ mockGetGrounding: vi.fn() }));
vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  return {
    ...actual,
    getGrounding: mockGetGrounding,
    getRunLog: vi.fn(),
  };
});

import { TrustCard } from "../TrustCard";
import { ApiError } from "../../api/client";

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TrustCard projectId={5} runId={42} />
    </QueryClientProvider>,
  );
}

const SCOREABLE = {
  runId: 42,
  status: "done",
  modelUsed: "deepseek-chat",
  createdAt: "2026-05-29T00:00:00",
  manifest: {
    eventCount: 7,
    toolInvocationCount: 3,
    evidenceCount: 5,
    fabricatedCount: 0,
    chainHead: "abcdef0123456789aaaa",
    contentSha256: "0011223344556677aabb",
  },
  metrics: {
    groundingAccuracy: 1.0,
    provenanceHitRate: 0.8,
    zeroFabricationRate: 1.0,
    insufficientEvidence: false,
    scoreable: true,
    evidenceCount: 5,
    fabricatedCount: 0,
    greenCount: 4,
    yellowCount: 1,
  },
  corpusHashCount: 6,
  verifyHint: "python scripts/verify_runlog.py ...",
};

const NOT_SCOREABLE = {
  ...SCOREABLE,
  metrics: {
    groundingAccuracy: null,
    provenanceHitRate: null,
    zeroFabricationRate: null,
    insufficientEvidence: true,
    scoreable: false,
    evidenceCount: 0,
    fabricatedCount: 0,
    greenCount: 0,
    yellowCount: 0,
  },
};

describe("TrustCard", () => {
  beforeEach(() => {
    mockGetGrounding.mockReset();
  });

  it("① 正常：渲染溯源命中率/grounding 准确率百分比 + 哈希链事件数", async () => {
    mockGetGrounding.mockResolvedValue(SCOREABLE);
    renderCard();

    // findByText 异步重试，等查询 resolve 后内容到位
    expect(await screen.findByText("溯源命中率")).toBeInTheDocument();
    expect(screen.getByText(/可信凭证/)).toBeInTheDocument();
    expect(screen.getByText("grounding 准确率")).toBeInTheDocument();
    expect(screen.getByText("零伪造率")).toBeInTheDocument();
    // 百分比数字（provenanceHitRate 0.8 → 80%）
    expect(screen.getByText("80%")).toBeInTheDocument();
    // groundingAccuracy 1.0 与 zeroFabricationRate 1.0 → 多个 100%
    expect(screen.getAllByText("100%").length).toBeGreaterThanOrEqual(2);
    // 哈希链事件数
    expect(screen.getByText("7")).toBeInTheDocument();
    // 不出现「不可评分」
    expect(screen.queryByText(/不可评分/)).toBeNull();
  });

  it("② scoreable:false：显示「不可评分」+ 诚实提示，三率不伪装 100%", async () => {
    mockGetGrounding.mockResolvedValue(NOT_SCOREABLE);
    renderCard();

    // 诚实提示含「未伪装满分」
    expect(await screen.findByText(/未伪装满分/)).toBeInTheDocument();
    expect(screen.getByText(/未产生引用证据/)).toBeInTheDocument();
    // 三率均显示「不可评分」
    expect(screen.getAllByText("不可评分").length).toBeGreaterThanOrEqual(3);
    // 关键诚信断言：不得出现伪装满分的 100%
    expect(screen.queryByText("100%")).toBeNull();
  });

  it("③ 404（run 无 grounding）→ 静默不渲染", async () => {
    mockGetGrounding.mockRejectedValue(new ApiError("RUN_NOT_FOUND", 404, "run 不存在"));
    const { container } = renderCard();

    await waitFor(() => {
      expect(mockGetGrounding).toHaveBeenCalled();
    });
    // 无可信凭证标题（静默）
    await waitFor(() => {
      expect(screen.queryByText(/可信凭证/)).toBeNull();
    });
    // 容器内无 trust-card
    expect(container.querySelector(".trust-card")).toBeNull();
  });
});
