/**
 * prismaPanelA6.test.tsx — A6 PRISMA 面板单测
 *
 * 覆盖：
 *  - 纯函数：deriveCounts（按 inclusionStatus 推导）/ validateCounts（一致性）/ parseReasons / serializeSvg。
 *  - 组件：自动填充计数 → 输入框 + SVG 计数；本地一致性告警；导出 SVG/PNG/PDF 触发。
 *
 * 策略：mock useProjectPapers（不触网）+ buildPrisma；jsdom 下断言 SVG <text> 计数文本。
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ---- mock useProjectPapers（纯渲染，不触网）----
const { papersSpy } = vi.hoisted(() => ({ papersSpy: vi.fn() }));
vi.mock("../api/agentHooks", () => ({
  useProjectPapers: (...a: unknown[]) => papersSpy(...a),
}));

// ---- mock buildPrisma（后端校验按钮）----
const { buildPrismaSpy } = vi.hoisted(() => ({ buildPrismaSpy: vi.fn() }));
vi.mock("../api/client", async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return { ...actual, buildPrisma: (...a: unknown[]) => buildPrismaSpy(...a) };
});

import {
  PrismaPanel,
  deriveCounts,
  validateCounts,
  parseReasons,
  serializeSvg,
} from "../components/PrismaPanel";
import type { PrismaRequest } from "../api/client";

function setPapers(papers: { inclusionStatus: string }[]) {
  papersSpy.mockReturnValue({ data: { papers }, isLoading: false, error: null });
}

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <PrismaPanel projectId="7" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  setPapers([]);
  buildPrismaSpy.mockResolvedValue({ stages: [], warnings: [] });
});
afterEach(() => {
  vi.clearAllMocks();
});

// ============================================================
// 纯函数
// ============================================================
describe("deriveCounts", () => {
  it("按 inclusionStatus 推导计数（duplicates 默认 0，screened = 总数）", () => {
    const papers = [
      { inclusionStatus: "included" as const },
      { inclusionStatus: "included" as const },
      { inclusionStatus: "excluded" as const },
      { inclusionStatus: "candidate" as const },
      { inclusionStatus: "maybe" as const },
    ];
    expect(deriveCounts(papers)).toEqual<PrismaRequest>({
      identified: 5,
      duplicates: 0,
      screened: 5,
      excluded: 1,
      included: 2,
    });
  });

  it("空列表返回全 0", () => {
    expect(deriveCounts([])).toEqual<PrismaRequest>({
      identified: 0,
      duplicates: 0,
      screened: 0,
      excluded: 0,
      included: 0,
    });
  });
});

describe("validateCounts", () => {
  it("一致计数无告警", () => {
    expect(
      validateCounts({ identified: 10, duplicates: 2, screened: 8, excluded: 5, included: 3 }),
    ).toEqual([]);
  });

  it("screened ≠ identified − duplicates 时告警", () => {
    const w = validateCounts({ identified: 10, duplicates: 2, screened: 9, excluded: 5, included: 3 });
    expect(w.some((m) => m.includes("筛选记录数"))).toBe(true);
  });

  it("included + excluded ≠ screened 时告警", () => {
    const w = validateCounts({ identified: 10, duplicates: 0, screened: 10, excluded: 4, included: 4 });
    expect(w.some((m) => m.includes("纳入"))).toBe(true);
  });

  it("去重>识别 时优先该告警，且不给出负数期望筛选数", () => {
    const w = validateCounts({ identified: 5, duplicates: 8, screened: 0, excluded: 0, included: 0 });
    expect(w.some((m) => m.includes("去重数") && m.includes("不应大于"))).toBe(true);
    // 不应出现 "= -3" 这类负期望值
    expect(w.some((m) => m.includes("= -"))).toBe(false);
  });
});

describe("parseReasons", () => {
  it("每行一条，去空行/去前缀符号", () => {
    expect(parseReasons("· 类型不符\n\n- 非同行评审\n  全文不可获取  ")).toEqual([
      "类型不符",
      "非同行评审",
      "全文不可获取",
    ]);
  });
});

describe("serializeSvg", () => {
  it("产出带 xmlns 的 SVG 字符串", () => {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    const str = serializeSvg(svg);
    expect(str).toContain("xmlns=\"http://www.w3.org/2000/svg\"");
    expect(str.startsWith("<?xml")).toBe(true);
  });
});

// ============================================================
// 组件
// ============================================================
describe("PrismaPanel 组件", () => {
  it("空数据时不绘制流程图、不显示导出按钮", () => {
    renderPanel();
    expect(screen.queryByRole("img", { name: "PRISMA 2020 流程图" })).toBeNull();
    expect(screen.queryByText("导出 SVG")).toBeNull();
    expect(screen.getByText(/此处将绘制 PRISMA 流程图/)).toBeInTheDocument();
  });

  it("自动填充：从语料推导并写入输入框 + SVG 计数", async () => {
    setPapers([
      { inclusionStatus: "included" },
      { inclusionStatus: "included" },
      { inclusionStatus: "included" },
      { inclusionStatus: "excluded" },
    ]);
    renderPanel();
    fireEvent.click(screen.getByText("从当前语料自动填充"));

    // 输入框值（identified=4, included=3, excluded=1）
    const identified = screen.getByLabelText("识别记录数") as HTMLInputElement;
    const included = screen.getByLabelText("纳入研究数") as HTMLInputElement;
    expect(identified.value).toBe("4");
    expect(included.value).toBe("3");

    // 提示来源
    expect(screen.getByText(/已从当前语料（共 4 篇）填充计数/)).toBeInTheDocument();

    // SVG 渲染且含计数文本
    const svg = await screen.findByRole("img", { name: "PRISMA 2020 流程图" });
    expect(svg.textContent).toContain("4"); // identified
    expect(svg.textContent).toContain("3"); // included
    expect(svg.textContent).toContain("识别 Identification");
    expect(svg.textContent).toContain("纳入 Included");
  });

  it("语料为空时自动填充给出提示、不绘图", () => {
    setPapers([]);
    renderPanel();
    fireEvent.click(screen.getByText("从当前语料自动填充"));
    expect(screen.getByText(/当前语料暂无文献/)).toBeInTheDocument();
  });

  it("不一致计数显示本地告警", () => {
    setPapers([
      { inclusionStatus: "included" },
      { inclusionStatus: "excluded" },
    ]); // identified=2 included=1 excluded=1 screened=2 一致
    renderPanel();
    fireEvent.click(screen.getByText("从当前语料自动填充"));
    // 手动把 included 改成 5 制造不一致
    const included = screen.getByLabelText("纳入研究数") as HTMLInputElement;
    fireEvent.change(included, { target: { value: "5" } });
    expect(screen.getByRole("alert")).toHaveTextContent(/应等于 筛选记录数/);
  });

  it("排除理由渲染到 SVG 旁支", async () => {
    setPapers([{ inclusionStatus: "included" }, { inclusionStatus: "excluded" }]);
    renderPanel();
    fireEvent.click(screen.getByText("从当前语料自动填充"));
    const reasons = screen.getByLabelText("排除理由（每行一条）");
    fireEvent.change(reasons, { target: { value: "研究类型不符" } });
    const svg = await screen.findByRole("img", { name: "PRISMA 2020 流程图" });
    expect(svg.textContent).toContain("研究类型不符");
  });

  it("导出 SVG：触发 a.click 且文件名带 prisma_ 前缀", async () => {
    setPapers([{ inclusionStatus: "included" }]);
    // 拦截下载
    const clickSpy = vi.fn();
    const realCreate = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
      const el = realCreate(tag);
      if (tag === "a") {
        Object.defineProperty(el, "click", { value: clickSpy, configurable: true });
      }
      return el;
    });
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:fake"),
      revokeObjectURL: vi.fn(),
    });

    renderPanel();
    fireEvent.click(screen.getByText("从当前语料自动填充"));
    await screen.findByRole("img", { name: "PRISMA 2020 流程图" });
    fireEvent.click(screen.getByText("导出 SVG"));

    expect(clickSpy).toHaveBeenCalledTimes(1);
    const a = clickSpy.mock.instances[0] as HTMLAnchorElement;
    expect(a.download).toMatch(/^prisma_\d{8}_\d{6}\.svg$/);

    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("导出 PDF：打开打印窗口并以 DOM 方式挂载 SVG（无字符串拼接）", async () => {
    setPapers([{ inclusionStatus: "included" }]);
    const printSpy = vi.fn();
    // 用真实 DOM document 作打印窗口文档，使 createElement/importNode 可用
    const printDoc = document.implementation.createHTMLDocument("");
    const fakeWin = {
      document: printDoc,
      focus: vi.fn(),
      print: printSpy,
      close: vi.fn(),
      onload: null as null | (() => void),
    };
    vi.stubGlobal("open", vi.fn(() => fakeWin as unknown as Window));

    renderPanel();
    fireEvent.click(screen.getByText("从当前语料自动填充"));
    await screen.findByRole("img", { name: "PRISMA 2020 流程图" });
    fireEvent.click(screen.getByText("导出 PDF"));

    // SVG 同步挂载到打印文档（DOM importNode，非 document.write 字符串）
    expect(printDoc.querySelector("svg")).toBeTruthy();
    expect(printDoc.body.textContent).toContain("PRISMA 2020 文献筛选流程图");
    // 触发 onload → 打印
    fakeWin.onload?.();
    expect(printSpy).toHaveBeenCalled();

    vi.unstubAllGlobals();
  });

  it("后端一致性校验：点击调 buildPrisma 并渲染其 warnings", async () => {
    buildPrismaSpy.mockResolvedValue({ stages: [], warnings: ["后端告警：去重数异常"] });
    setPapers([{ inclusionStatus: "included" }]);
    renderPanel();
    fireEvent.click(screen.getByText("从当前语料自动填充"));
    fireEvent.click(screen.getByText("后端一致性校验"));
    await waitFor(() => expect(buildPrismaSpy).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByText("后端告警：去重数异常")).toBeInTheDocument(),
    );
  });
});
