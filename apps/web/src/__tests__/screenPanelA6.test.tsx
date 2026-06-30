/**
 * screenPanelA6.test.tsx — A6 相关性筛选面板单测
 *
 * 覆盖：
 *  - 纯函数：relevanceTier（分级）/ screenStats（统计：总数/高相关/均分）。
 *  - 组件：输入→出结果列表；相关度条分级配色；默认按相关度降序排序；
 *          理由截断展开/收起；空结果空态；错误态；无 LLM key 温和提示。
 *
 * 策略：mock aiScreen（不触网）+ useLlmSettings（控制有无 key）；jsdom 断言条宽/配色/排序。
 */
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ---- mock aiScreen（仅替换该导出，其余 client 保持真实）----
const { aiScreenSpy } = vi.hoisted(() => ({ aiScreenSpy: vi.fn() }));
vi.mock("../api/client", async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return { ...actual, aiScreen: (...a: unknown[]) => aiScreenSpy(...a) };
});

// ---- mock useLlmSettings（控制是否有 key）----
const { llmSpy } = vi.hoisted(() => ({ llmSpy: vi.fn() }));
vi.mock("../api/useLlmSettings", () => ({
  useLlmSettings: () => llmSpy(),
}));

import {
  ScreenPanel,
  relevanceTier,
  screenStats,
  REASON_TRUNCATE,
} from "../components/ScreenPanel";
import type { ScreenResult } from "../api/client";

function setKey(apiKey: string) {
  llmSpy.mockReturnValue({
    settings: { provider: "deepseek", apiKey, model: "deepseek-chat" },
    save: vi.fn(),
    clear: vi.fn(),
  });
}

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ScreenPanel projectId="7" corpusId="c1" />
    </QueryClientProvider>,
  );
}

const SAMPLE: ScreenResult = {
  results: [
    { idx: 1, relevance: 5, reason: "中等相关：方法接近但场景不同。" },
    { idx: 2, relevance: 9, reason: "高度相关：主题与方法均高度吻合。" },
    { idx: 3, relevance: 2, reason: "低相关：仅边缘提及。" },
    { idx: 4, relevance: null, reason: "未能评估：摘要缺失。" },
  ],
};

beforeEach(() => {
  setKey("test-api-key"); // 默认有 key
  aiScreenSpy.mockResolvedValue(SAMPLE);
});
afterEach(() => {
  vi.clearAllMocks();
});

// ============================================================
// 纯函数
// ============================================================
describe("relevanceTier", () => {
  it("≥8 高 / 5-7 中 / <5 低 / null 未评估", () => {
    expect(relevanceTier(8)).toBe("high");
    expect(relevanceTier(10)).toBe("high");
    expect(relevanceTier(7)).toBe("mid");
    expect(relevanceTier(5)).toBe("mid");
    expect(relevanceTier(4)).toBe("low");
    expect(relevanceTier(0)).toBe("low");
    expect(relevanceTier(null)).toBe("none");
    expect(relevanceTier(undefined)).toBe("none");
    expect(relevanceTier(NaN)).toBe("none");
  });
});

describe("screenStats", () => {
  it("统计总数 / 高相关(≥8) / 已评估数 / 均分（仅计已评估）", () => {
    const s = screenStats(SAMPLE.results);
    expect(s.total).toBe(4);
    expect(s.high).toBe(1); // 只有 idx2(9)
    expect(s.scored).toBe(3); // 5,9,2（null 不计）
    expect(s.avg).toBe(5.3); // (5+9+2)/3 = 5.33 → 5.3
  });

  it("全部 null 时均分为 null", () => {
    const s = screenStats([{ idx: 1, relevance: null, reason: "x" }]);
    expect(s.scored).toBe(0);
    expect(s.avg).toBeNull();
    expect(s.high).toBe(0);
  });

  it("空结果返回全 0 / null", () => {
    expect(screenStats([])).toEqual({ total: 0, high: 0, scored: 0, avg: null });
  });
});

// ============================================================
// 组件
// ============================================================
describe("ScreenPanel 组件", () => {
  it("无主题时按钮禁用；填入后启用", () => {
    renderPanel();
    const btn = screen.getByRole("button", { name: "开始筛选" }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("研究主题"), { target: { value: "深度学习" } });
    expect(btn.disabled).toBe(false);
  });

  it("输入主题并筛选 → 调 aiScreen 并渲染结果列表 + 统计条", async () => {
    renderPanel();
    fireEvent.change(screen.getByLabelText("研究主题"), { target: { value: "医学影像" } });
    fireEvent.click(screen.getByRole("button", { name: "开始筛选" }));

    await waitFor(() => expect(aiScreenSpy).toHaveBeenCalled());
    // 透传主题/条数/LLM 配置
    expect(aiScreenSpy).toHaveBeenCalledWith(
      "7",
      "c1",
      "医学影像",
      10,
      expect.objectContaining({
        apiKey: "test-api-key",
        model: "deepseek-chat",
      }),
    );

    // 统计条小结
    await screen.findByText(/共筛选 4 篇/);
    expect(screen.getByText(/高相关\(≥8\) 1 篇/)).toBeInTheDocument();
    expect(screen.getByText("高相关 1")).toBeInTheDocument();
    expect(screen.getByText("已评估 3/4")).toBeInTheDocument();

    // 四行结果都在
    expect(screen.getByText("高度相关：主题与方法均高度吻合。")).toBeInTheDocument();
  });

  it("相关度条分级配色：高=--ok / 中=--gold / 低=--ink-3 / null=--line-2", async () => {
    renderPanel();
    fireEvent.change(screen.getByLabelText("研究主题"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "开始筛选" }));
    await screen.findByText(/共筛选 4 篇/);

    // 各相关度条以 aria-label 定位，断言填充色
    const high = screen.getByLabelText("相关度 9/10（高相关）");
    const mid = screen.getByLabelText("相关度 5/10（中相关）");
    const low = screen.getByLabelText("相关度 2/10（低相关）");
    const none = screen.getByLabelText("未评估");

    const fill = (el: HTMLElement) =>
      (el.querySelector(".screen-bar-fill") as HTMLElement).style.background;
    expect(fill(high)).toContain("--ok");
    expect(fill(mid)).toContain("--gold");
    expect(fill(low)).toContain("--ink-3");
    expect(fill(none)).toContain("--line-2");

    // 数值标注：高相关显示 9/10，未评估显示 —
    expect(screen.getByText("9/10")).toBeInTheDocument();
  });

  it("默认按相关度降序排序（首行最相关 idx=2）", async () => {
    renderPanel();
    fireEvent.change(screen.getByLabelText("研究主题"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "开始筛选" }));
    await screen.findByText(/共筛选 4 篇/);

    const rows = screen.getAllByRole("row").slice(1); // 去表头
    // 首行为 idx=2(rel=9)
    expect(within(rows[0]).getByText("9/10")).toBeInTheDocument();
    expect(within(rows[0]).getByText("2")).toBeInTheDocument(); // 序号 idx=2
    // null(未评估) 沉底：末行为 idx=4、显示「—」（验证 DataTable 降序把 null 排到最后）
    const last = rows[rows.length - 1];
    expect(within(last).getByText("—")).toBeInTheDocument();
    expect(within(last).getByText("4")).toBeInTheDocument(); // 序号 idx=4
  });

  it("理由过长截断 + 展开/收起", async () => {
    const longReason = "相关性分析：".repeat(20); // 远超 REASON_TRUNCATE
    expect(longReason.length).toBeGreaterThan(REASON_TRUNCATE);
    aiScreenSpy.mockResolvedValue({ results: [{ idx: 1, relevance: 7, reason: longReason }] });

    renderPanel();
    fireEvent.change(screen.getByLabelText("研究主题"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "开始筛选" }));

    const expandBtn = await screen.findByRole("button", { name: "展开" });
    // 截断态：含省略号、不含全文
    expect(screen.queryByText(longReason)).toBeNull();
    fireEvent.click(expandBtn);
    // 展开后显示全文 + 收起按钮
    expect(screen.getByText(longReason)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "收起" })).toBeInTheDocument();
  });

  it("空结果显示友好空态（不报错）", async () => {
    aiScreenSpy.mockResolvedValue({ results: [] });
    renderPanel();
    fireEvent.change(screen.getByLabelText("研究主题"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "开始筛选" }));
    await screen.findByText("本次筛选未返回结果");
  });

  it("错误态：aiScreen 抛错时渲染错误信息", async () => {
    aiScreenSpy.mockRejectedValue(new Error("评分服务不可用"));
    renderPanel();
    fireEvent.change(screen.getByLabelText("研究主题"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "开始筛选" }));
    await waitFor(() =>
      expect(screen.getByText("评分服务不可用")).toBeInTheDocument(),
    );
  });

  it("无 LLM key 时显示占位评分温和提示；有 key 时不显示", () => {
    setKey(""); // 无 key
    const { unmount } = renderPanel();
    expect(screen.getByText(/未配置 LLM key，将使用占位评分/)).toBeInTheDocument();
    unmount();

    setKey("test-api-key");
    renderPanel();
    expect(screen.queryByText(/未配置 LLM key/)).toBeNull();
  });
});
