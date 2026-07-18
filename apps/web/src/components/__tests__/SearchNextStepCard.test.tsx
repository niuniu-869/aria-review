/**
 * SearchNextStepCard — 检索完成下一步推荐卡（0.6.2 S7）。
 * 断言四阶段文案/按钮映射、曝光与点击埋点、关闭回调。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { SearchNextStepCard } from "../SearchNextStepCard";
import { getProjectReadiness } from "../../hooks/useProjectReadiness";

const trackMock = vi.hoisted(() => vi.fn());
vi.mock("../../lib/track", () => ({ track: trackMock }));

function renderCard(
  stats: { paperCount: number; includedCount: number; readableFulltextCount: number; ocrDoneCount?: number | null },
  onClose = vi.fn(),
) {
  const readiness = getProjectReadiness(stats, 7)!;
  render(
    <MemoryRouter>
      <SearchNextStepCard projectId={7} readiness={readiness} onClose={onClose} />
    </MemoryRouter>,
  );
  return { readiness, onClose };
}

beforeEach(() => {
  trackMock.mockClear();
});

describe("SearchNextStepCard", () => {
  it("no_papers：提示换关键词重试，无跳转按钮", () => {
    renderCard({ paperCount: 0, includedCount: 0, readableFulltextCount: 0 });
    expect(screen.getByText("本次检索没有新入库的文献")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(trackMock).toHaveBeenCalledWith("search_next_step_view", { stage: "no_papers" }, 7);
  });

  it("no_included：主按钮去筛选纳入", () => {
    renderCard({ paperCount: 10, includedCount: 0, readableFulltextCount: 0 });
    const link = screen.getByRole("link", { name: "去筛选纳入" });
    expect(link).toHaveAttribute("href", "/projects/7/library");
  });

  it("no_fulltext：主按钮去补全文，副文案提示一键补全文", () => {
    renderCard({ paperCount: 10, includedCount: 5, readableFulltextCount: 0 });
    expect(screen.getByRole("link", { name: "去补全文" })).toBeInTheDocument();
    expect(screen.getByText(/自动补全文/)).toBeInTheDocument();
  });

  it("not_parsed：主按钮去文献库解析全文，副文案提示 OCR/AI 解析", () => {
    renderCard({ paperCount: 10, includedCount: 5, readableFulltextCount: 0, ocrDoneCount: 0 });
    const link = screen.getByRole("link", { name: "去文献库解析全文" });
    expect(link).toHaveAttribute("href", "/projects/7/library");
    expect(screen.getByText(/OCR 解析/)).toBeInTheDocument();
  });

  it("ready：生成综述 + 发现研究空白双按钮，点击带埋点", () => {
    renderCard({ paperCount: 10, includedCount: 5, readableFulltextCount: 5 });
    const review = screen.getByRole("link", { name: "生成综述" });
    expect(review).toHaveAttribute("href", "/projects/7/analysis/review");
    expect(screen.getByRole("link", { name: "发现研究空白" })).toHaveAttribute("href", "/projects/7/research");
    fireEvent.click(review);
    expect(trackMock).toHaveBeenCalledWith(
      "search_next_step_click",
      { stage: "ready", action: "生成综述" },
      7,
    );
  });

  it("关闭按钮触发 onClose", () => {
    const { onClose } = renderCard({ paperCount: 10, includedCount: 5, readableFulltextCount: 5 });
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
