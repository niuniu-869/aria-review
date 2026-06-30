/**
 * ScratchpadLive.test.tsx — 研究笔记本实时视图（B3）。
 * 覆盖：运行脉冲/状态徽标 / 状态流转计数(draft→verified→accepted) / 失败显式 /
 *       选中联动 / 空·加载 / 接线轮询更新 + 终态停轮询(P1)。
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const { mockGetScratchpad } = vi.hoisted(() => ({ mockGetScratchpad: vi.fn() }));
vi.mock("../../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../../api/client")>("../../../api/client");
  return { ...actual, getScratchpad: mockGetScratchpad };
});

import { ScratchpadLive, ScratchpadLiveConnected } from "../ScratchpadLive";
import {
  SCRATCHPAD_TICKS,
  scratchpadState,
  gapDraftConcept,
  gapVerifiedTheory,
} from "../../../api/research.fixtures";

describe("ScratchpadLive · 展示", () => {
  it("运行中：脉冲指示 + 运行中徽标 + 计数 + 首条论断", () => {
    const { container } = render(<ScratchpadLive state={SCRATCHPAD_TICKS[0]} />);
    expect(screen.getByText("运行中")).toBeInTheDocument();
    expect(container.querySelector(".sp-pulse.is-live")).not.toBeNull();
    expect(screen.getByText("1 条")).toBeInTheDocument();
    expect(screen.getByText(gapDraftConcept.statement)).toBeInTheDocument();
  });

  it("状态流转计数：draft → verified 随轮询推进（rerender 模拟一拍）", () => {
    const { rerender } = render(<ScratchpadLive state={SCRATCHPAD_TICKS[0]} />);
    expect(screen.getByText("已核验 0")).toBeInTheDocument();
    // 第三拍：3 条，含 1 条 verified
    rerender(<ScratchpadLive state={SCRATCHPAD_TICKS[2]} />);
    expect(screen.getByText("3 条")).toBeInTheDocument();
    expect(screen.getByText("已核验 1")).toBeInTheDocument();
  });

  it("完成态：已完成徽标、无脉冲、含已采纳计数", () => {
    const { container } = render(<ScratchpadLive state={scratchpadState} />);
    expect(screen.getByText("已完成")).toBeInTheDocument();
    expect(container.querySelector(".sp-pulse.is-live")).toBeNull();
    expect(screen.getByText("已采纳 1")).toBeInTheDocument();
  });

  it("失败态：显式标注失败（不静默成完成）", () => {
    const failed = { ...scratchpadState, run_status: "failed" as const, entries: [] };
    render(<ScratchpadLive state={failed} />);
    expect(screen.getByText("运行失败")).toBeInTheDocument();
    expect(screen.getByText(/未静默成完成/)).toBeInTheDocument();
  });

  it("选中联动：点击条目触发 onSelectGap", () => {
    const onSelect = vi.fn();
    render(<ScratchpadLive state={scratchpadState} onSelectGap={onSelect} />);
    fireEvent.click(screen.getByText(gapVerifiedTheory.statement).closest("button")!);
    expect(onSelect).toHaveBeenCalledWith(gapVerifiedTheory);
  });

  it("空态：运行中提示 vs 无 run 提示", () => {
    const { rerender } = render(
      <ScratchpadLive state={{ run_id: "r", run_status: "running", entries: [], updated_at: "" }} />,
    );
    expect(screen.getByText(/正在翻阅文献/)).toBeInTheDocument();
    rerender(<ScratchpadLive state={null} />);
    expect(screen.getByText(/暂无条目/)).toBeInTheDocument();
  });
});

describe("ScratchpadLiveConnected · 轮询", () => {
  beforeEach(() => mockGetScratchpad.mockReset());
  afterEach(() => vi.clearAllMocks());

  function renderConnected() {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={qc}>
        <ScratchpadLiveConnected projectId={5} runId="run_gap_001" pollMs={20} />
      </QueryClientProvider>,
    );
  }

  it("轮询累积更新，run_status=completed 后停轮询（codex B1-P1）", async () => {
    // 每次返回序列下一拍；越界后恒返回末拍(completed)
    let i = 0;
    mockGetScratchpad.mockImplementation(async () => SCRATCHPAD_TICKS[Math.min(i++, SCRATCHPAD_TICKS.length - 1)]);

    renderConnected();
    // 轮询推进到完成态（运行中→已完成）
    await waitFor(() => expect(screen.getByText("已完成")).toBeInTheDocument(), { timeout: 3000 });
    expect(screen.getByText("已采纳 1")).toBeInTheDocument();

    const callsAtComplete = mockGetScratchpad.mock.calls.length;
    expect(callsAtComplete).toBeGreaterThanOrEqual(SCRATCHPAD_TICKS.length); // 至少轮询到末拍
    // 终态后停轮询：等待 >10×pollMs，调用次数不再增长
    await new Promise((r) => setTimeout(r, 250));
    expect(mockGetScratchpad.mock.calls.length).toBe(callsAtComplete);
  });
});
