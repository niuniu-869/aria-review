/**
 * LibraryStatusBar.test.tsx — Task 5 TDD 测试
 *
 * 覆盖：
 *   1. 正常渲染 — 全局共享库计数、本项目计数、已OCR 标签、语料就绪状态
 *   2. loading 态 — stats 为 null 时渲染骨架占位不崩溃
 *   3. LibraryModelInfo — 点击 ⓘ 弹出说明弹层，包含关键文案；ESC 关闭
 *   4. 失败/待 OCR 徽章可见
 */
import { render, screen, fireEvent, within } from "@testing-library/react";
import { vi, describe, it, expect } from "vitest";
import type { components } from "../../api/schema";

// 在 import 前 vi.mock 保证 mock 生效
vi.mock("../../api/agentHooks", () => ({
  useProject: () => ({ data: null }),
}));

import { LibraryStatusBar } from "../LibraryStatusBar";

type ProjectLibraryStats = components["schemas"]["ProjectLibraryStats"];

const FULL_STATS: ProjectLibraryStats = {
  projectPapers: 89,
  inclusion: { included: 42, candidate: 30, excluded: 15, maybe: 2 },
  withMetadata: 89,
  withPdf: 76,
  ocr: { done: 71, processing: 0, pending: 5, failed: 0, none: 13 },
};

const CORPUS_READY = { status: "ready" as const, documentCount: 74, stale: false };

describe("LibraryStatusBar", () => {
  it("renders counts and corpus readiness", () => {
    render(
      <LibraryStatusBar
        stats={FULL_STATS}
        globalTotal={1240}
        corpus={CORPUS_READY}
      />,
    );
    // 全局共享库 chip（包含"全局共享库"文字）
    expect(screen.getByText(/全局共享库/)).toBeInTheDocument();
    // 全局总量数字（1240 只出现在全局 chip 的 value 里）
    expect(screen.getAllByText(/1240/).length).toBeGreaterThan(0);
    // 本项目 chip value（89（纳入 42））
    expect(screen.getByText(/89（纳入 42）/)).toBeInTheDocument();
    // 已OCR 标签
    expect(screen.getByText(/已OCR/)).toBeInTheDocument();
    // 语料就绪
    expect(screen.getByText(/就绪/)).toBeInTheDocument();
  });

  it("shows loading skeleton when stats is null", () => {
    const { container } = render(
      <LibraryStatusBar stats={null} globalTotal={null} corpus={null} />,
    );
    // 不崩溃，渲染某种占位
    expect(container.firstChild).toBeInTheDocument();
  });

  it("opens LibraryModelInfo popover on ⓘ click and closes with ESC", () => {
    render(
      <LibraryStatusBar
        stats={FULL_STATS}
        globalTotal={500}
        corpus={null}
      />,
    );
    // 点击 ⓘ 按钮
    const infoBtn = screen.getByRole("button", { name: /库说明/ });
    fireEvent.click(infoBtn);
    // 弹层出现，含关键文案（用 within(dialog) + getAllByText 避免歧义）
    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeInTheDocument();
    // 弹层内有"全局共享库"标题
    expect(within(dialog).getByRole("heading", { name: "全局共享库" })).toBeInTheDocument();
    // 弹层内有"项目纳排"标题
    expect(within(dialog).getByRole("heading", { name: "项目纳排" })).toBeInTheDocument();

    // ESC 关闭
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("shows pending and failed OCR badges when counts > 0", () => {
    const statsWithFailures: ProjectLibraryStats = {
      ...FULL_STATS,
      ocr: { done: 5, processing: 0, pending: 3, failed: 2, none: 0 },
    };
    render(
      <LibraryStatusBar
        stats={statsWithFailures}
        globalTotal={100}
        corpus={null}
      />,
    );
    expect(screen.getByText(/待OCR/)).toBeInTheDocument();
    expect(screen.getByText(/失败/)).toBeInTheDocument();
  });

  it("shows stale corpus badge when corpus is stale", () => {
    render(
      <LibraryStatusBar
        stats={FULL_STATS}
        globalTotal={100}
        corpus={{ status: "ready", documentCount: 50, stale: true }}
      />,
    );
    expect(screen.getByText(/需更新/)).toBeInTheDocument();
  });

  it("LibraryModelInfo focus trap — Tab 在弹层内循环，焦点不逃出 dialog", () => {
    render(
      <LibraryStatusBar
        stats={FULL_STATS}
        globalTotal={500}
        corpus={null}
      />,
    );
    // 打开弹层
    const infoBtn = screen.getByRole("button", { name: /库说明/ });
    fireEvent.click(infoBtn);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeInTheDocument();

    // 对话框应有 aria-describedby
    expect(dialog).toHaveAttribute("aria-describedby", "lib-model-info-desc");

    // 按 Tab — 焦点应仍在 dialog 内
    fireEvent.keyDown(document, { key: "Tab" });
    // dialog 或其子元素持有焦点（document.activeElement 在 dialog 内）
    expect(dialog.contains(document.activeElement) || document.activeElement === dialog).toBe(true);

    // 按 Shift+Tab — 焦点应仍在 dialog 内
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(dialog.contains(document.activeElement) || document.activeElement === dialog).toBe(true);
  });
});
