/**
 * TrustBadgeStrip.test.tsx — Phase 5 静态可信徽章条测试
 *
 * 覆盖：渲染三枚徽章（哈希链 / grounding 溯源 / 零伪造）。
 */
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { TrustBadgeStrip } from "../TrustBadgeStrip";

describe("TrustBadgeStrip", () => {
  it("渲染三枚可信徽章", () => {
    render(<TrustBadgeStrip />);
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(screen.getByText(/RunLog 哈希链/)).toBeTruthy();
    expect(screen.getByText(/grounding 溯源/)).toBeTruthy();
    expect(screen.getByText(/零伪造约束/)).toBeTruthy();
  });
});
