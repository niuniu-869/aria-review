/**
 * GapPanel.test.tsx — 研究空白发现面板（B2）。
 * 覆盖：主题分组渲染 / lens·status·verdict 徽章 / 展开见支撑证据(引文+源坐标+跳转) /
 *       反证 / 选中联动 / 空·加载·错误态 / 诚信说明。fixture 驱动（单源）。
 */
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { GapPanel } from "../GapPanel";
import {
  ALL_GAPS,
  gapVerifiedMethod,
  gapDraftConcept,
  gapVerifiedTheory,
} from "../../../api/research.fixtures";

describe("GapPanel", () => {
  it("按 theme 分组渲染 + 计数 + 诚信说明（置信非裁决）", () => {
    render(<GapPanel projectId={5} gaps={ALL_GAPS} />);
    // 两个主题簇都出现
    expect(screen.getByText("MD&A 文本特征与信息含量")).toBeInTheDocument();
    expect(screen.getByText("盈余管理识别与文本语气")).toBeInTheDocument();
    // 总计数
    expect(screen.getByText("5 条")).toBeInTheDocument();
    // 论断文本（折叠态也渲染 statement）
    expect(screen.getByText(gapDraftConcept.statement)).toBeInTheDocument();
    // 诚信说明：价值由确定性核验给出
    expect(screen.getByText(/价值由确定性核验给出/)).toBeInTheDocument();
  });

  it("lens / status / verdict 徽章如实渲染", () => {
    render(<GapPanel projectId={5} gaps={ALL_GAPS} />);
    expect(screen.getAllByText("概念").length).toBeGreaterThan(0); // concept lens
    expect(screen.getAllByText("方法").length).toBeGreaterThan(0); // method lens
    expect(screen.getByText("理论")).toBeInTheDocument(); // theory lens
    expect(screen.getAllByText("草稿").length).toBeGreaterThan(0); // draft
    expect(screen.getAllByText("已核验").length).toBeGreaterThan(0); // verified
    expect(screen.getByText("已采纳")).toBeInTheDocument(); // accepted
    // verdict 徽章
    expect(screen.getAllByText("有研究价值").length).toBeGreaterThan(0); // valuable
    expect(screen.getByText("疑似伪空白")).toBeInTheDocument(); // likely_filled
  });

  it("展开卡片 → 见支撑引文 + Paper 跳转 href + 源坐标锚点 + 反证", () => {
    render(<GapPanel projectId={5} gaps={ALL_GAPS} />);
    // 折叠态：支撑引文不在 DOM
    expect(screen.queryByText("现有研究多依赖 LM 词典统计语气。")).toBeNull();
    // 点击该 GAP 的卡头（method/verified 那条）
    const head = screen.getByText(gapVerifiedMethod.statement).closest("button")!;
    fireEvent.click(head);
    // 支撑引文逐字呈现
    expect(screen.getByText("现有研究多依赖 LM 词典统计语气。")).toBeInTheDocument();
    // Paper 跳转 href 指向文献详情（projectId 作用域）
    const link = screen.getByRole("link", { name: "Paper #7" });
    expect(link).toHaveAttribute("href", "/projects/5/library/7");
    // 源坐标锚点 chip 带 data-anchor-id
    expect(document.querySelector('[data-anchor-id="p7_b9__occ1"]')).not.toBeNull();
    // 反证（counter_evidence）呈现
    expect(screen.getByText("反证")).toBeInTheDocument();
    expect(screen.getByText(/个别研究已尝试嵌入法/)).toBeInTheDocument();
  });

  it("选中联动：点击触发 onSelectGap 并高亮", () => {
    const onSelect = vi.fn();
    const { rerender } = render(
      <GapPanel projectId={5} gaps={ALL_GAPS} onSelectGap={onSelect} selectedGapId={null} />,
    );
    fireEvent.click(screen.getByText(gapVerifiedTheory.statement).closest("button")!);
    expect(onSelect).toHaveBeenCalledWith(gapVerifiedTheory);
    // 受控高亮
    rerender(
      <GapPanel projectId={5} gaps={ALL_GAPS} onSelectGap={onSelect} selectedGapId={gapVerifiedTheory.gap_id} />,
    );
    expect(document.querySelector(`.gap-card.is-selected[data-gap-id="${gapVerifiedTheory.gap_id}"]`)).not.toBeNull();
  });

  it("空 / 加载 / 错误态", () => {
    const { rerender } = render(<GapPanel projectId={5} gaps={[]} />);
    expect(screen.getByText("尚未发现研究空白")).toBeInTheDocument();

    rerender(<GapPanel projectId={5} gaps={[]} isLoading />);
    expect(screen.getByText("发现研究空白中…")).toBeInTheDocument();
    expect(screen.queryByText("尚未发现研究空白")).toBeNull();

    rerender(<GapPanel projectId={5} gaps={[]} error={new Error("发现失败")} />);
    expect(screen.getByText("发现失败")).toBeInTheDocument();
  });
});
