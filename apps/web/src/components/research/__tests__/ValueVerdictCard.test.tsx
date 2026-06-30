/**
 * ValueVerdictCard.test.tsx — 价值裁决卡（B4）。
 * 覆盖：三 verdict 渲染 / decided_by 确定性徽标 / 反向命中刻度+透明阈值 /
 *       可空性(score/year/doi/source_view) / fail-loud skipped / HITL accept·reject·revise。
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { ValueVerdictCard } from "../ValueVerdictCard";
import type { GapVerdictResult } from "../../../types/research";
import {
  verdictResultG2,
  verdictResultG3,
  verdictResultG5,
  gapVerifiedMethod,
  gapAcceptedConcept,
  gapDraftMethod,
} from "../../../api/research.fixtures";

describe("ValueVerdictCard", () => {
  it("valuable：确定性徽标 + 命中刻度(透明阈值) + 计量结构 + 价值分", () => {
    render(<ValueVerdictCard result={verdictResultG2} gap={gapVerifiedMethod} />);
    expect(screen.getByText("有研究价值")).toBeInTheDocument();
    // decided_by 徽标强调非 LLM
    expect(screen.getByText(/确定性裁决/)).toBeInTheDocument();
    expect(screen.getByText(/非 LLM/)).toBeInTheDocument();
    // 命中刻度 aria-label 含命中数与阈值（透明）
    expect(screen.getByRole("img", { name: /命中 2 篇/ })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /低 3、高 25/ })).toBeInTheDocument();
    // 计量结构佐证
    expect(screen.getByText("共现断层")).toBeInTheDocument();
    expect(screen.getByText("取自「conceptual」视图")).toBeInTheDocument();
    // 价值分（score 非空）
    expect(screen.getByText(/价值分 0\.86/)).toBeInTheDocument();
  });

  it("likely_filled：高命中 41 + 疑似伪空白", () => {
    render(<ValueVerdictCard result={verdictResultG3} gap={null} />);
    expect(screen.getByText("疑似伪空白")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /命中 41 篇/ })).toBeInTheDocument();
  });

  it("inconclusive：score=null 不显分 / source_view=null 不显视图 / fail-loud skipped 列出", () => {
    render(<ValueVerdictCard result={verdictResultG5} gap={gapDraftMethod} />);
    expect(screen.getByText("证据不足")).toBeInTheDocument();
    // score 为 null → 不渲染价值分
    expect(screen.queryByText(/价值分/)).toBeNull();
    // source_view 为 null → 不渲染「取自…视图」
    expect(screen.queryByText(/取自「/)).toBeNull();
    // fail-loud：跳过项显式列出
    expect(screen.getByText(/已跳过：/)).toBeInTheDocument();
    expect(screen.getByText(/OpenAlex 近 5 年过滤后候选不足/)).toBeInTheDocument();
  });

  it("可空性渲染：year=null → 年份缺失；doi=null → 无 DOI", () => {
    render(<ValueVerdictCard result={verdictResultG2} gap={gapVerifiedMethod} />);
    expect(screen.getByText("年份缺失")).toBeInTheDocument();
    expect(screen.getByText("无 DOI")).toBeInTheDocument();
  });

  it("HITL：采纳/驳回 触发 onDecide", () => {
    const onDecide = vi.fn();
    render(<ValueVerdictCard result={verdictResultG2} gap={gapVerifiedMethod} onDecide={onDecide} />);
    fireEvent.click(screen.getByRole("button", { name: "采纳" }));
    expect(onDecide).toHaveBeenCalledWith("accept");
    fireEvent.click(screen.getByRole("button", { name: "驳回" }));
    expect(onDecide).toHaveBeenCalledWith("reject");
  });

  it("HITL revise：改写预填当前论断 → 提交回写新 statement，成功后关闭编辑态", async () => {
    const onDecide = vi.fn().mockResolvedValue(undefined);
    render(<ValueVerdictCard result={verdictResultG2} gap={gapVerifiedMethod} onDecide={onDecide} />);
    fireEvent.click(screen.getByRole("button", { name: "改写" }));
    const textarea = screen.getByLabelText("改写 GAP 论断") as HTMLTextAreaElement;
    // 预填当前 statement
    expect(textarea.value).toBe(gapVerifiedMethod.statement);
    fireEvent.change(textarea, { target: { value: "改写后的更聚焦论断" } });
    fireEvent.click(screen.getByRole("button", { name: "提交改写" }));
    expect(onDecide).toHaveBeenCalledWith("revise", "改写后的更聚焦论断");
    // 仅 PATCH(resolve) 成功后关闭编辑态（codex B4-P2）
    await waitFor(() => expect(screen.queryByLabelText("改写 GAP 论断")).toBeNull());
  });

  it("HITL revise 失败：保留编辑态与草稿供重试（codex B4-P2）", async () => {
    const onDecide = vi.fn().mockRejectedValue(new Error("PATCH 失败"));
    render(<ValueVerdictCard result={verdictResultG2} gap={gapVerifiedMethod} onDecide={onDecide} />);
    fireEvent.click(screen.getByRole("button", { name: "改写" }));
    const textarea = screen.getByLabelText("改写 GAP 论断") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "改写草稿" } });
    fireEvent.click(screen.getByRole("button", { name: "提交改写" }));
    await waitFor(() => expect(onDecide).toHaveBeenCalled());
    // 失败后编辑态与草稿保留
    expect((screen.getByLabelText("改写 GAP 论断") as HTMLTextAreaElement).value).toBe("改写草稿");
  });

  it("已定稿 gap：显示人工定稿提示（可重新决策）", () => {
    const onDecide = vi.fn();
    render(<ValueVerdictCard result={verdictResultG2} gap={gapAcceptedConcept} onDecide={onDecide} />);
    expect(screen.getByText(/已采纳（人工定稿/)).toBeInTheDocument();
  });

  it("decided_by 非 deterministic：如实标注待核、不伪装非 LLM（codex B4-P1）", () => {
    // 模拟契约违例的后端返回（类型上 decided_by 恒 deterministic，故 cast）
    const tampered = {
      ...verdictResultG2,
      verdict: { ...verdictResultG2.verdict, decided_by: "llm" },
    } as unknown as GapVerdictResult;
    render(<ValueVerdictCard result={tampered} gap={gapVerifiedMethod} />);
    expect(screen.getByText(/来源待核/)).toBeInTheDocument();
    expect(screen.getByText(/由 llm 决定/)).toBeInTheDocument();
    expect(screen.queryByText("确定性裁决 · 非 LLM")).toBeNull();
  });

  it("阈值异常 low>=high：降级提示，不画错刻度（codex B4-P2）", () => {
    const bad: GapVerdictResult = {
      ...verdictResultG2,
      verdict: { ...verdictResultG2.verdict, thresholds: { reverse_hit_low: 10, reverse_hit_high: 10 } },
    };
    const { container } = render(<ValueVerdictCard result={bad} gap={gapVerifiedMethod} />);
    expect(screen.getByText(/阈值异常/)).toBeInTheDocument();
    expect(container.querySelector(".vv-marker")).toBeNull();
  });

  it("加载 / 错误 / 无裁决态", () => {
    const { rerender, container } = render(<ValueVerdictCard isLoading />);
    expect(screen.getByText("加载价值裁决…")).toBeInTheDocument();
    rerender(<ValueVerdictCard error={new Error("裁决获取失败")} />);
    expect(screen.getByText("裁决获取失败")).toBeInTheDocument();
    rerender(<ValueVerdictCard result={null} />);
    expect(container.querySelector(".vv-card")).toBeNull();
  });
});
