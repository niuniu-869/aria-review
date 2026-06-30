/**
 * SourceViewerBoundary.test.tsx — F2 行级高亮边界（codex P1 回归护栏）。
 *
 * 零伪造铁律：block 的 md_line 范围非法（越界 / end<start）时必须判为"无法定位"，
 * 绝不把非法范围 clamp 到错误行而显示假溯源。
 */
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi, describe, it, expect, beforeEach } from "vitest";
import * as structureApi from "../../api/structure";
import type { StructureBlock, StructureResponse, MarkdownResponse } from "../../types/provenance";

vi.mock("../../api/structure");

import { SourceViewer } from "../source/SourceViewer";

const MD: MarkdownResponse = { markdown: "L1\nL2\nL3\nL4\nL5\nL6", length: 17, truncated: false, sha256: "x" };

function block(partial: Partial<StructureBlock>): StructureBlock {
  return {
    block_idx: 1,
    type: "text",
    text_level: null,
    page_no: 1,
    md_line_start: 2,
    md_line_end: 3,
    bbox: null,
    section_title: "引言",
    text_preview: "",
    ...partial,
  };
}

function structureWith(b: StructureBlock): StructureResponse {
  return {
    paper_id: 1,
    attachment_id: 1,
    page_count: 1,
    blocks: [b],
    tables: [],
    has_bbox: false,
    markdown_sha256: "x",
  };
}

function renderSV(focusBlockIdx: number) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SourceViewer projectId={1} paperId={1} focusBlockIdx={focusBlockIdx} anchorId="ax" />
    </QueryClientProvider>,
  );
}

describe("SourceViewer 行级高亮边界", () => {
  beforeEach(() => {
    // jsdom 未实现 scrollIntoView；命中合法块时 effect 会调用
    Element.prototype.scrollIntoView = vi.fn();
    vi.mocked(structureApi.getMarkdown).mockResolvedValue(MD);
  });

  it("合法行范围(2..3) → 高亮该段", async () => {
    vi.mocked(structureApi.getStructure).mockResolvedValue(
      structureWith(block({ md_line_start: 2, md_line_end: 3 })),
    );
    const { container } = renderSV(1);
    await waitFor(() =>
      expect(container.querySelector("[data-block-highlight='true']")).toBeTruthy(),
    );
  });

  it("越界(start>行数) → 不高亮 + 显示无法定位", async () => {
    vi.mocked(structureApi.getStructure).mockResolvedValue(
      structureWith(block({ md_line_start: 99, md_line_end: 100 })),
    );
    const { container } = renderSV(1);
    await screen.findByText(/无法精确定位/);
    expect(container.querySelector("[data-block-highlight='true']")).toBeNull();
  });

  it("end<start → 不高亮 + 显示无法定位", async () => {
    vi.mocked(structureApi.getStructure).mockResolvedValue(
      structureWith(block({ md_line_start: 3, md_line_end: 2 })),
    );
    const { container } = renderSV(1);
    await screen.findByText(/无法精确定位/);
    expect(container.querySelector("[data-block-highlight='true']")).toBeNull();
  });

  it("md_line null(契约 §5.3 无法定位) → 不高亮 + 显示无法定位", async () => {
    vi.mocked(structureApi.getStructure).mockResolvedValue(
      structureWith(block({ md_line_start: null, md_line_end: null })),
    );
    const { container } = renderSV(1);
    await screen.findByText(/无法精确定位/);
    expect(container.querySelector("[data-block-highlight='true']")).toBeNull();
  });
});
