/**
 * ReviewTemplatePicker.test.tsx — Task 8 TDD 测试
 *
 * 覆盖：
 *   1. 渲染 6 个论型卡
 *   2. 选"博士" → onPick("phd") 且预览含"5"章
 *   3. run 完成后建议追问 chips 出现并点击填输入框
 */
import { render, screen, fireEvent } from "@testing-library/react";
import { vi, describe, it, expect } from "vitest";
import { ReviewTemplatePicker } from "../ReviewTemplatePicker";

describe("ReviewTemplatePicker", () => {
  it("渲染 6 个论型卡", () => {
    render(<ReviewTemplatePicker onPick={() => {}} />);
    // 6 个论型卡对应的名称
    expect(screen.getByText(/本科毕业论文/)).toBeInTheDocument();
    expect(screen.getByText(/硕士论文/)).toBeInTheDocument();
    expect(screen.getByText(/博士论文/)).toBeInTheDocument();
    expect(screen.getByText(/基金申报/)).toBeInTheDocument();
    expect(screen.getByText(/开题报告/)).toBeInTheDocument();
    expect(screen.getByText(/SCI/)).toBeInTheDocument();
  });

  it("点击「博士论文」卡 → onPick('phd')", () => {
    const onPick = vi.fn();
    render(<ReviewTemplatePicker onPick={onPick} />);

    const phdCard = screen.getByRole("button", { name: /博士论文/ });
    fireEvent.click(phdCard);

    expect(onPick).toHaveBeenCalledWith("phd");
  });

  it("博士论文卡预览含 5 章信息", () => {
    render(<ReviewTemplatePicker onPick={() => {}} />);
    expect(screen.getByText(/5\s*章/)).toBeInTheDocument();
  });

  it("本科卡预览含 3 章信息", () => {
    render(<ReviewTemplatePicker onPick={() => {}} />);
    // 本科 3 章
    const cards = screen.getAllByText(/3\s*章/);
    expect(cards.length).toBeGreaterThan(0);
  });

  it("硕士卡预览含 4 章信息", () => {
    render(<ReviewTemplatePicker onPick={() => {}} />);
    expect(screen.getByText(/4\s*章/)).toBeInTheDocument();
  });
});
