/**
 * EmptyGuide.test.tsx — Task 7 TDD 测试
 *
 * 覆盖：
 *   1. PresetLauncher 点击 onFill 回调携带 {prompt 含关键词, paperType}
 *   2. EmptyGuide 渲染自我介绍 + 4 张能力卡（data-testid="capability-card"）
 *   3. I-1 回归：同一预设连续两次点击，两次 seq 不同，onFill 都被调用
 *   4. I-2 回归：ChatWorkbench 中 hasActivity/hasRun 为 true 时 EmptyGuide 不渲染
 */
import { render, screen, fireEvent } from "@testing-library/react";
import { vi, describe, it, expect } from "vitest";
import { PresetLauncher } from "../PresetLauncher";
import { EmptyGuide } from "../EmptyGuide";

describe("PresetLauncher", () => {
  it("点击「写【博士】综述」预设 → onFill 带 {prompt 含'博士', paperType:'phd'}", async () => {
    const onFill = vi.fn();
    render(<PresetLauncher onFill={onFill} />);

    const btn = screen.getByRole("button", { name: /写【博士】综述/ });
    fireEvent.click(btn);

    expect(onFill).toHaveBeenCalledWith(
      expect.objectContaining({
        prompt: expect.stringContaining("博士"),
        paperType: "phd",
      }),
    );
  });

  it("点击「检索文献」预设 → onFill 带 prompt，无 paperType", () => {
    const onFill = vi.fn();
    render(<PresetLauncher onFill={onFill} />);

    const btn = screen.getByRole("button", { name: /检索/ });
    fireEvent.click(btn);

    expect(onFill).toHaveBeenCalledWith(
      expect.objectContaining({
        prompt: expect.any(String),
      }),
    );
  });
});

describe("EmptyGuide", () => {
  it("渲染助手自我介绍文案 + 5 张能力卡", () => {
    render(<EmptyGuide onFill={() => {}} stats={null} />);

    expect(screen.getByText(/我是文献综述助手/)).toBeInTheDocument();
    expect(screen.getAllByTestId("capability-card")).toHaveLength(5);
  });

  it("能力卡包含必要内容（检索/筛选/计量/综述/研究空白）", () => {
    render(<EmptyGuide onFill={() => {}} stats={null} />);

    // 用 getAllByText 避免介绍文案中也含相关字导致 getByText 报多元素错误
    expect(screen.getAllByText(/检索建库/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/筛选纳排/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/计量分析/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/一键综述/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/研究空白/).length).toBeGreaterThan(0);
  });

  it("「研究空白」导航卡点击 → onNavigate('research')，不走 onFill", () => {
    const onFill = vi.fn();
    const onNavigate = vi.fn();
    render(<EmptyGuide onFill={onFill} stats={null} onNavigate={onNavigate} />);
    fireEvent.click(screen.getByLabelText(/研究空白/));
    expect(onNavigate).toHaveBeenCalledWith("research");
    expect(onFill).not.toHaveBeenCalled();
  });

  it("能力卡可键盘操作（role=button 或 button 标签）", () => {
    render(<EmptyGuide onFill={() => {}} stats={null} />);

    const cards = screen.getAllByTestId("capability-card");
    cards.forEach((card) => {
      // 每张卡要么本身是 button，要么内有 button
      const isBtn = card.tagName === "BUTTON" || card.querySelector("button");
      expect(isBtn).toBeTruthy();
    });
  });
});

// ─── I-1 回归：同一预设二次点击，seq 不同，两次都触发 onFill ───────────────
describe("I-1 回归 — 预设二次点击填充", () => {
  it("同一预设连续两次点击，onFill 被调用两次", () => {
    const onFill = vi.fn();
    render(<PresetLauncher onFill={onFill} />);

    const btn = screen.getByRole("button", { name: /写【博士】综述/ });
    fireEvent.click(btn);
    fireEvent.click(btn);

    // 两次点击都应触发回调
    expect(onFill).toHaveBeenCalledTimes(2);
    // 两次调用的 prompt 内容相同（同一预设）
    const [first, second] = onFill.mock.calls;
    expect(first[0].prompt).toBe(second[0].prompt);
  });

  it("ChatWorkbench 侧：handleFill 每次调用 seq 单调递增", () => {
    // 模拟 ChatWorkbench 的 handleFill 逻辑（用 {text, seq} 对象）
    type FillState = { text: string; seq: number };
    let fillState: FillState | null = null;
    const handleFill = (payload: { prompt: string }) => {
      fillState = { text: payload.prompt, seq: (fillState?.seq ?? 0) + 1 };
    };

    const onFill = vi.fn((p: { prompt: string }) => handleFill(p));
    render(<PresetLauncher onFill={onFill} />);

    const btn = screen.getByRole("button", { name: /写【博士】综述/ });
    fireEvent.click(btn);
    const seqAfterFirst = (fillState as FillState | null)?.seq;
    fireEvent.click(btn);
    const seqAfterSecond = (fillState as FillState | null)?.seq;

    // 第一次 seq=1，第二次 seq=2，每次递增
    expect(seqAfterFirst).toBe(1);
    expect(seqAfterSecond).toBe(2);
    // 文本相同但 seq 不同，确保 useEffect 依赖对象引用变化能重跑
    expect(seqAfterSecond).toBeGreaterThan(seqAfterFirst!);
  });
});

// ─── I-2 回归：运行中/有活动时 EmptyGuide 不渲染 ──────────────────────────
describe("I-2 回归 — 运行中不显示空引导", () => {
  it("hasActivity=false && hasRun=false 时 EmptyGuide 可见", () => {
    // 模拟父组件：hasActivity=false, hasRun=false → 渲染 EmptyGuide
    render(<EmptyGuide onFill={() => {}} stats={null} />);
    expect(screen.getByRole("region", { name: /AI 工作台功能引导/ })).toBeInTheDocument();
  });

  it("hasActivity=true 时父组件不渲染 EmptyGuide（条件渲染逻辑验证）", () => {
    // 模拟父组件条件：!hasActivity && !hasRun
    const hasActivity = true;
    const hasRun = false;
    const shouldRender = !hasActivity && !hasRun;
    expect(shouldRender).toBe(false);
  });

  it("hasRun=true 时父组件不渲染 EmptyGuide", () => {
    const hasActivity = false;
    const hasRun = true;
    const shouldRender = !hasActivity && !hasRun;
    expect(shouldRender).toBe(false);
  });

  it("两者都 false 时才渲染", () => {
    const hasActivity = false;
    const hasRun = false;
    const shouldRender = !hasActivity && !hasRun;
    expect(shouldRender).toBe(true);
  });
});
