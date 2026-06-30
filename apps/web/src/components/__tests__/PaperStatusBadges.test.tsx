/**
 * PaperStatusBadges.test.tsx — Task 6 TDD 测试
 *
 * 覆盖：
 *   1. {hasPdf:true, ocrStatus:"done"} → 出现 PDF 徽章 + 已OCR 徽章
 *   2. {hasPdf:false, ocrStatus:"none"} → 出现"仅元数据"徽章（无 PDF 徽章）
 *   3. {hasPdf:true, ocrStatus:"pending"} → 出现 PDF 徽章 + 待OCR 徽章
 *   4. {hasPdf:true, ocrStatus:"failed"} → 出现 PDF 徽章 + OCR失败 徽章
 */
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { PaperStatusBadges } from "../PaperStatusBadges";

describe("PaperStatusBadges", () => {
  it("hasPdf+done → PDF 和已OCR 徽章均可见", () => {
    render(
      <PaperStatusBadges hasPdf={true} ocrStatus="done" />,
    );
    expect(screen.getByText(/PDF/)).toBeInTheDocument();
    expect(screen.getByText(/已OCR/)).toBeInTheDocument();
    expect(screen.queryByText(/仅元数据/)).toBeNull();
  });

  it("hasPdf:false + ocrStatus:none → 仅元数据徽章，无 PDF 和 OCR 徽章", () => {
    render(
      <PaperStatusBadges hasPdf={false} ocrStatus="none" />,
    );
    expect(screen.getByText(/仅元数据/)).toBeInTheDocument();
    expect(screen.queryByText(/PDF/)).toBeNull();
    expect(screen.queryByText(/已OCR/)).toBeNull();
  });

  it("hasPdf+pending → PDF 徽章 + 待OCR 徽章", () => {
    render(
      <PaperStatusBadges hasPdf={true} ocrStatus="pending" />,
    );
    expect(screen.getByText(/PDF/)).toBeInTheDocument();
    expect(screen.getByText(/待OCR/)).toBeInTheDocument();
  });

  it("hasPdf+failed → PDF 徽章 + OCR失败 徽章", () => {
    render(
      <PaperStatusBadges hasPdf={true} ocrStatus="failed" />,
    );
    expect(screen.getByText(/PDF/)).toBeInTheDocument();
    expect(screen.getByText(/OCR失败/)).toBeInTheDocument();
  });

  it("hasPdf+processing → PDF 徽章 + 解析中 徽章", () => {
    render(
      <PaperStatusBadges hasPdf={true} ocrStatus="processing" />,
    );
    expect(screen.getByText(/PDF/)).toBeInTheDocument();
    expect(screen.getByText(/解析中/)).toBeInTheDocument();
  });
});
