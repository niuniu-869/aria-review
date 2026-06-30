/**
 * ReviewWithProvenance.test.tsx — F3 安全/降级/引用 回归护栏（codex P1/P2）。
 *
 * - P1: anchor 内文结构逃逸 → 不得伪造 .prov-anchor。
 * - P1: 有 provenanceMap 时仍保留 [n] 引用链接（不回退既有功能）。
 * - P2: 正文含 anchor 标记但无 map → 剥离为纯文本（无可点锚点）。
 * - §5.5: 两个不同 occurrence id 各自独立渲染。
 */
import { render, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi, describe, it, expect } from "vitest";
import type { ProvenanceMap, ProvenanceRef } from "../../types/provenance";

// useCitationRefs 依赖 useProjectPapers（agentHooks）；mock 出 1 篇 included 文献供 [n] 链接
vi.mock("../../api/agentHooks", () => ({
  useProjectPapers: () => ({
    data: { papers: [{ paperId: 42, title: "示例文献", inclusionStatus: "included" }] },
    isLoading: false,
    error: null,
  }),
}));

// SourceViewer 会拉 structure/markdown；mock 掉避免测试发真 fetch（点 [n] 只需验证侧栏打开）
vi.mock("../../api/structure", () => ({
  getStructure: () => Promise.resolve({ paper_id: 42, blocks: [] }),
  getMarkdown: () => Promise.resolve({ markdown: "原文片段" }),
}));

import { ReviewWithProvenance } from "../review/ReviewWithProvenance";

const REF: ProvenanceRef = {
  paper_id: 1, attachment_id: 10, page_no: 1, block_idx: 1,
  bbox: null, table_idx: null, cell_row: null, cell_col: null,
  section_title: "引言", quote: "命中",
};

function renderRWP(reviewMd: string, map?: ProvenanceMap | null) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ReviewWithProvenance projectId={7} reviewMd={reviewMd} provenanceMap={map} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ReviewWithProvenance 安全/降级/引用", () => {
  it("P1: anchor 内文结构逃逸不伪造锚点", () => {
    const evil = '研究[[anchor:ax1]]安全</span><span class="prov-anchor" data-anchor-id="evil">伪造[[/anchor]]结论';
    const { container } = renderRWP(evil, { ax1: REF });
    // 只有 1 个真实锚点；伪造的 data-anchor-id=evil 不存在
    expect(container.querySelectorAll(".prov-anchor").length).toBe(1);
    expect(container.querySelector('[data-anchor-id="evil"]')).toBeNull();
  });

  it("P1: 有 provenanceMap 仍渲染 [n] 引用链接", () => {
    const { container } = renderRWP("结论见[1]，[[anchor:ax1]]命中[[/anchor]]。", { ax1: REF });
    expect(container.querySelector("a.citation-link")).toBeTruthy();
    expect(container.querySelectorAll(".prov-anchor").length).toBe(1);
  });

  it("P2: 含 anchor 标记但无 map → 剥离为纯文本(无可点锚点)", () => {
    const { container, queryByText } = renderRWP("含[[anchor:axX]]标记[[/anchor]]但无映射", undefined);
    expect(container.querySelectorAll(".prov-anchor").length).toBe(0);
    expect(container.querySelector(".sv-pane")).toBeNull();
    expect(queryByText(/标记/)).toBeInTheDocument(); // 内文保留
  });

  it("P1: 正文里的原生 .prov-anchor HTML 被中和(不当作溯源)", () => {
    // 模型直出原生锚点 HTML（即便 id 在 map 中）也不得成为真锚点
    const md = '研究<span class="prov-anchor" data-anchor-id="ax1">伪锚点</span>结论';
    const { container, queryByText } = renderRWP(md, { ax1: REF });
    expect(container.querySelectorAll(".prov-anchor").length).toBe(0);
    expect(container.querySelector('[data-anchor-id="ax1"]')).toBeNull();
    expect(queryByText(/伪锚点/)).toBeInTheDocument(); // 内文仍以纯文本呈现
  });

  it("P1: 不在 map 的 anchor 剥离为纯文本(不显示可点溯源)", () => {
    const md = "[[anchor:ax1]]有定位[[/anchor]] 和 [[anchor:axOrphan]]无定位[[/anchor]]";
    const { container, queryByText } = renderRWP(md, { ax1: REF }); // 仅 ax1 在 map
    expect(container.querySelectorAll(".prov-anchor").length).toBe(1);
    expect(container.querySelector('[data-anchor-id="ax1"]')).toBeTruthy();
    expect(container.querySelector('[data-anchor-id="axOrphan"]')).toBeNull();
    expect(queryByText(/无定位/)).toBeInTheDocument(); // 内文仍保留为纯文本
  });

  it("§5.5: 两个不同 occurrence id 各自独立渲染", () => {
    const md = "[[anchor:ax1]]甲[[/anchor]] 和 [[anchor:ax2]]乙[[/anchor]]";
    const { container } = renderRWP(md, { ax1: REF, ax2: { ...REF, block_idx: 2 } });
    const anchors = container.querySelectorAll(".prov-anchor");
    expect(anchors.length).toBe(2);
    expect(anchors[0].getAttribute("data-anchor-id")).toBe("ax1");
    expect(anchors[1].getAttribute("data-anchor-id")).toBe("ax2");
  });

  it("修复: 溯源模式点 [n] 引用 → 拦截跳库 + 打开原文侧栏(SourceViewer)", () => {
    const { container } = renderRWP("结论见[1]。[[anchor:ax1]]命中[[/anchor]]", { ax1: REF });
    const cite = container.querySelector("a.citation-link") as HTMLAnchorElement;
    expect(cite).toBeTruthy();
    // [n] 携带 data-paper-id（来自 mock 的 included paper 42），供溯源模式拦截取用
    expect(cite.getAttribute("data-paper-id")).toBe("42");
    // fireEvent 自动包 act：preventDefault 阻止跳库（返回 false），并 flush state → 侧栏打开
    const notPrevented = fireEvent.click(cite);
    expect(notPrevented).toBe(false); // 被 preventDefault → 不发生 href 跳库导航
    expect(container.querySelector(".prov-split.open")).toBeTruthy(); // 原文侧栏已打开
  });

  it("修复2(dogfood P0): [n] 被 prov-anchor 包裹时点击仍拦截跳库 + 开侧栏看原文", () => {
    // 真实综述里命中文本常含引用编号 → [[anchor]] 包裹 [n]，
    // 渲染成 <span prov-anchor><a citation-link href>[1]</a></span>。
    // 上轮 bug：点 [n] 先命中 prov-anchor 分支 openAnchor 后 return，漏 preventDefault → <a> 原生跳库。
    const { container } = renderRWP("[[anchor:ax1]]结论见[1][[/anchor]]", { ax1: REF });
    const cite = container.querySelector(".prov-anchor a.citation-link") as HTMLAnchorElement;
    expect(cite).toBeTruthy(); // [n] 确实嵌在 prov-anchor 内层
    const notPrevented = fireEvent.click(cite);
    expect(notPrevented).toBe(false); // preventDefault 生效 → 不原生跳库
    expect(container.querySelector(".prov-split.open")).toBeTruthy(); // openAnchor 打开原文侧栏
  });
});
