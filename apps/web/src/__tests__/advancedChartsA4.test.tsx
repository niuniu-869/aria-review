/**
 * advancedChartsA4.test.tsx — A4 后端补算① 前端渲染单测
 *
 * 覆盖:
 *  1) 纯函数: resolveEnvelopeBranch / envelopeChartProps（信封→渲染分支映射）
 *  2) 纯函数: buildAuthorHeatmapOption / buildKeywordRiverOption（option 构造）
 *  3) 组件三态: AuthorsPanel 热力图 / DocumentsPanel themeRiver+cited-refs 的
 *     loading / unavailable(InsufficientData) / error / available 分支
 *  4) 缺字段降级: available:false / missing_field → InsufficientData 文案
 *  5) g/m/tc 列 + Bradford rank/cumPct + 核心区高亮 + overview KPI 卡
 *
 * 策略（同 statsPanelsA2.test.tsx）: jsdom mock echarts 实例，断言传给 setOption 的 option。
 */
import { render, screen } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";

// ---- mock echarts 实例 ----
const { setOptionSpy, initSpy } = vi.hoisted(() => {
  const setOptionSpy = vi.fn((_opt: Record<string, unknown>, _cfg?: unknown) => {});
  const initSpy = vi.fn(() => ({
    setOption: setOptionSpy,
    dispose: vi.fn(),
    resize: vi.fn(),
    on: vi.fn(),
    off: vi.fn(),
    getDataURL: vi.fn(() => "data:image/png;base64,FAKE"),
    renderToSVGString: vi.fn(() => "<svg>fake</svg>"),
  }));
  return { setOptionSpy, initSpy };
});
vi.mock("../components/viz/echartsSetup", () => ({
  echarts: { init: initSpy, registerTheme: vi.fn() },
  ensureWordCloud: vi.fn(() => Promise.resolve(false)), // jsdom: 词云不可用，降级
}));
vi.mock("../theme/echartsTheme", () => ({ registerBiblioTheme: vi.fn() }));

// ---- mock api/hooks ----
const {
  authorsSpy,
  authorProductionSpy,
  documentsSpy,
  keywordTrendSpy,
  citedRefsSpy,
  sourcesSpy,
  overviewSpy,
} = vi.hoisted(() => ({
  authorsSpy: vi.fn(),
  authorProductionSpy: vi.fn(),
  documentsSpy: vi.fn(),
  keywordTrendSpy: vi.fn(),
  citedRefsSpy: vi.fn(),
  sourcesSpy: vi.fn(),
  overviewSpy: vi.fn(),
}));
vi.mock("../api/hooks", () => ({
  useAuthors: (...a: unknown[]) => authorsSpy(...a),
  useAuthorProduction: (...a: unknown[]) => authorProductionSpy(...a),
  useDocuments: (...a: unknown[]) => documentsSpy(...a),
  useKeywordTrend: (...a: unknown[]) => keywordTrendSpy(...a),
  useCitedRefs: (...a: unknown[]) => citedRefsSpy(...a),
  useSources: (...a: unknown[]) => sourcesSpy(...a),
  useOverview: (...a: unknown[]) => overviewSpy(...a),
  // A5: OverviewPanel 现也调用三字段信封 hook；A4 测试只关心 KPI 卡，给稳定 loading 桩。
  useThreefield: () => ({ data: undefined, isLoading: true, isError: false }),
}));

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", class {
    observe() {}
    unobserve() {}
    disconnect() {}
  });
  vi.stubGlobal("matchMedia", vi.fn().mockImplementation((q: string) => ({
    matches: false, media: q, addEventListener: vi.fn(), removeEventListener: vi.fn(),
  })));
  // 默认所有信封 hook 返回 loading（各 describe 内按需 override）
  authorProductionSpy.mockReturnValue({ data: undefined, isLoading: true, isError: false });
  keywordTrendSpy.mockReturnValue({ data: undefined, isLoading: true, isError: false });
  citedRefsSpy.mockReturnValue({ data: undefined, isLoading: true, isError: false });
  authorsSpy.mockReturnValue({ data: undefined, isLoading: true, isError: false });
  documentsSpy.mockReturnValue({ data: undefined, isLoading: true, isError: false });
});
afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

// 延迟 import：mock 先生效
import {
  resolveEnvelopeBranch,
  envelopeChartProps,
} from "../components/viz/EnvelopeView";
import {
  buildAuthorHeatmapOption,
  buildKeywordRiverOption,
} from "../components/viz/advancedCharts";
import { AuthorsPanel } from "../components/AuthorsPanel";
import { DocumentsPanel } from "../components/DocumentsPanel";
import { SourcesPanel } from "../components/SourcesPanel";
import { OverviewPanel } from "../components/OverviewPanel";

function optionsWith(predicate: (o: Record<string, unknown>) => boolean) {
  return setOptionSpy.mock.calls.map((c) => c[0]).filter(predicate);
}

// ============================================================
// 1) 纯函数: resolveEnvelopeBranch / envelopeChartProps
// ============================================================
describe("resolveEnvelopeBranch", () => {
  it("loading", () => {
    expect(resolveEnvelopeBranch({ isLoading: true, isError: false, data: undefined }).kind).toBe("loading");
  });
  it("error", () => {
    expect(resolveEnvelopeBranch({ isLoading: false, isError: true, data: undefined }).kind).toBe("error");
  });
  it("无数据未报错 → loading", () => {
    expect(resolveEnvelopeBranch({ isLoading: false, isError: false, data: undefined }).kind).toBe("loading");
  });
  it("unavailable (available:false)", () => {
    const data = { available: false as const, reason: "missing_field" as const, message: "x" };
    expect(resolveEnvelopeBranch({ isLoading: false, isError: false, data }).kind).toBe("unavailable");
  });
  it("available (available:true)", () => {
    const data = { available: true as const, data: { foo: 1 } };
    expect(resolveEnvelopeBranch({ isLoading: false, isError: false, data }).kind).toBe("available");
  });
});

describe("envelopeChartProps", () => {
  it("available:false → empty 为 InsufficientData，loading/error 清空", () => {
    const data = {
      available: false as const,
      reason: "missing_field" as const,
      missingField: "CR",
      message: "缺 CR",
      howto: "导入含 CR 的题录",
    };
    const p = envelopeChartProps({ isLoading: false, isError: false, data });
    expect(p.loading).toBe(false);
    expect(p.error).toBeUndefined();
    expect(p.empty).toBeTruthy(); // InsufficientData 节点
  });
  it("error 态 → error 有值，empty 无", () => {
    const p = envelopeChartProps({ isLoading: false, isError: true, error: new Error("boom"), data: undefined });
    expect(p.empty).toBeUndefined();
    expect(p.error).toBeTruthy();
  });
  it("available:true → 三态皆空（渲染 children）", () => {
    const data = { available: true as const, data: { x: 1 } };
    const p = envelopeChartProps({ isLoading: false, isError: false, data });
    expect(p.loading).toBe(false);
    expect(p.error).toBeUndefined();
    expect(p.empty).toBeUndefined();
  });
});

// ============================================================
// 2) 纯函数: option 构造
// ============================================================
describe("buildAuthorHeatmapOption", () => {
  it("heatmap series + visualMap 宣纸色 + 正确 cell 坐标", () => {
    const opt = buildAuthorHeatmapOption({
      authors: ["A", "B"],
      years: [2020, 2021],
      cells: [
        { author: "A", year: 2020, articles: 3 },
        { author: "B", year: 2021, articles: 5 },
      ],
    });
    const series = opt.series as Array<{ type: string; data: [number, number, number][] }>;
    expect(series[0].type).toBe("heatmap");
    // [yearIdx, authorIdx, articles]
    expect(series[0].data).toContainEqual([0, 0, 3]);
    expect(series[0].data).toContainEqual([1, 1, 5]);
    const vm = opt.visualMap as { inRange: { color: string[] }; max: number };
    expect(vm.max).toBe(5);
    expect(vm.inRange.color).toContain("#c0432b"); // 朱砂
  });
  it("丢弃不在 authors/years 索引内的 cell", () => {
    const opt = buildAuthorHeatmapOption({
      authors: ["A"],
      years: [2020],
      cells: [
        { author: "A", year: 2020, articles: 1 },
        { author: "Z", year: 2099, articles: 9 }, // 不在索引
      ],
    });
    const series = opt.series as Array<{ data: unknown[] }>;
    expect(series[0].data).toHaveLength(1);
  });
});

describe("buildKeywordRiverOption", () => {
  it("themeRiver series + legend 含 terms + data [time,value,name]", () => {
    const opt = buildKeywordRiverOption({
      years: [2020, 2021],
      terms: ["AI", "NLP"],
      cells: [
        { year: 2020, term: "AI", freq: 4 },
        { year: 2021, term: "NLP", freq: 2 },
      ],
    });
    const series = opt.series as Array<{ type: string; data: [string, number, string][] }>;
    expect(series[0].type).toBe("themeRiver");
    expect(series[0].data).toContainEqual(["2020", 4, "AI"]);
    const legend = opt.legend as { data: string[] };
    expect(legend.data).toEqual(["AI", "NLP"]);
  });
});

// ============================================================
// 3+4) AuthorsPanel — 热力图三态 + g/m/tc 列
// ============================================================
describe("AuthorsPanel 作者年度产出热力图", () => {
  const authorsData = {
    data: { topAuthors: [{ author: "张三", articles: 6 }], hIndex: [], lotka: { beta: null, distribution: [] } },
    isLoading: false, isError: false,
  };

  it("available:true → 渲染热力图 (heatmap series)", () => {
    authorsSpy.mockReturnValue(authorsData);
    authorProductionSpy.mockReturnValue({
      data: {
        available: true,
        data: { authors: ["张三"], years: [2020, 2021], cells: [{ author: "张三", year: 2020, articles: 2 }] },
      },
      isLoading: false, isError: false,
    });
    render(<AuthorsPanel projectId="1" corpusId="c1" />);
    expect(screen.getByText("作者年度产出时间线")).toBeInTheDocument();
    const heat = optionsWith((o) => {
      const s = o.series as Array<{ type?: string }> | undefined;
      return Array.isArray(s) && s[0]?.type === "heatmap";
    });
    expect(heat.length).toBeGreaterThan(0);
  });

  it("available:false / missing_field → InsufficientData 降级", () => {
    authorsSpy.mockReturnValue(authorsData);
    authorProductionSpy.mockReturnValue({
      data: { available: false, reason: "missing_field", missingField: "PY", message: "缺 PY 字段", howto: "导入含年份的题录" },
      isLoading: false, isError: false,
    });
    render(<AuthorsPanel projectId="1" corpusId="c1" />);
    expect(screen.getByText("缺少字段「PY」")).toBeInTheDocument();
    expect(screen.getByText("缺 PY 字段")).toBeInTheDocument();
  });

  it("error 态（HTTP 失败）→ ChartCard 错误，不显示 InsufficientData", () => {
    authorsSpy.mockReturnValue(authorsData);
    authorProductionSpy.mockReturnValue({
      data: undefined, isLoading: false, isError: true, error: new Error("网络错误"),
    });
    render(<AuthorsPanel projectId="1" corpusId="c1" />);
    expect(screen.getAllByRole("alert").some((e) => e.textContent?.includes("网络错误"))).toBe(true);
    expect(screen.queryByText(/缺少字段/)).not.toBeInTheDocument();
  });

  it("loading 态 → ChartCard 加载中", () => {
    authorsSpy.mockReturnValue(authorsData);
    authorProductionSpy.mockReturnValue({ data: undefined, isLoading: true, isError: false });
    render(<AuthorsPanel projectId="1" corpusId="c1" />);
    expect(screen.getAllByText("加载中…").length).toBeGreaterThan(0);
  });

  it("hIndex 表含 g/m/tc 列；m=null 显示「—」", () => {
    authorsSpy.mockReturnValue({
      data: {
        topAuthors: [],
        hIndex: [{ author: "李四", h: 3, g: 5, m: null, tc: 40 }],
        lotka: { beta: null, distribution: [] },
      },
      isLoading: false, isError: false,
    });
    render(<AuthorsPanel projectId="1" corpusId="c1" />);
    expect(screen.getByText("g 指数")).toBeInTheDocument();
    expect(screen.getByText("m 指数")).toBeInTheDocument();
    expect(screen.getByText("被引总数")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument(); // g
    expect(screen.getByText("40")).toBeInTheDocument(); // tc
    expect(screen.getByText("—")).toBeInTheDocument(); // m=null
  });
});

// ============================================================
// 3+4) DocumentsPanel — themeRiver + cited-refs 三态
// ============================================================
describe("DocumentsPanel 关键词历时演变 + 高被引参考文献", () => {
  const docData = {
    data: { topCited: [], keywords: [] },
    isLoading: false, isError: false,
  };

  it("keyword-trend available:true → themeRiver 出图；cited-refs available:true → 表格", () => {
    documentsSpy.mockReturnValue(docData);
    keywordTrendSpy.mockReturnValue({
      data: {
        available: true,
        data: { years: [2020, 2021], terms: ["AI"], cells: [{ year: 2020, term: "AI", freq: 3 }] },
      },
      isLoading: false, isError: false,
    });
    citedRefsSpy.mockReturnValue({
      data: { available: true, data: [{ ref: "Loughran 2011", count: 34 }] },
      isLoading: false, isError: false,
    });
    render(<DocumentsPanel projectId="1" corpusId="c1" />);
    expect(screen.getByText("关键词历时演变")).toBeInTheDocument();
    const river = optionsWith((o) => {
      const s = o.series as Array<{ type?: string }> | undefined;
      return Array.isArray(s) && s[0]?.type === "themeRiver";
    });
    expect(river.length).toBeGreaterThan(0);
    // cited-refs 表
    expect(screen.getByText("Loughran 2011")).toBeInTheDocument();
    expect(screen.getByText("34")).toBeInTheDocument();
  });

  it("keyword-trend missing_field（PDF 缺 DE）→ InsufficientData 降级", () => {
    documentsSpy.mockReturnValue(docData);
    keywordTrendSpy.mockReturnValue({
      data: { available: false, reason: "missing_field", missingField: "DE", message: "缺关键词字段" },
      isLoading: false, isError: false,
    });
    citedRefsSpy.mockReturnValue({
      data: { available: false, reason: "missing_field", missingField: "CR", message: "缺参考文献字段" },
      isLoading: false, isError: false,
    });
    render(<DocumentsPanel projectId="1" corpusId="c1" />);
    expect(screen.getByText("缺少字段「DE」")).toBeInTheDocument();
    expect(screen.getByText("缺少字段「CR」")).toBeInTheDocument();
  });

  it("cited-refs error 态 → 错误，不显示 InsufficientData", () => {
    documentsSpy.mockReturnValue(docData);
    keywordTrendSpy.mockReturnValue({ data: undefined, isLoading: true, isError: false });
    citedRefsSpy.mockReturnValue({ data: undefined, isLoading: false, isError: true, error: new Error("HTTP 502") });
    render(<DocumentsPanel projectId="1" corpusId="c1" />);
    expect(screen.getAllByRole("alert").some((e) => e.textContent?.includes("HTTP 502"))).toBe(true);
  });
});

// ============================================================
// 5) SourcesPanel — g/m/tc + Bradford rank/cumPct + 核心区高亮
// ============================================================
describe("SourcesPanel A4 增量列", () => {
  it("H 指数表含 g/m/tc；Bradford 含排名/累计% + 核心区高亮", () => {
    sourcesSpy.mockReturnValue({
      data: {
        topSources: [{ source: "J A", articles: 9 }],
        hIndex: [{ source: "J A", h: 5, g: 6, m: 0.5, tc: 50 }],
        bradford: [
          { source: "J Core", zone: "Zone 1", freq: 9, rank: 1, cumPct: 12.5 },
          { source: "J Edge", zone: "Zone 3", freq: 1, rank: 2, cumPct: 100 },
        ],
      },
      isLoading: false, isError: false,
    });
    const { container } = render(<SourcesPanel projectId="1" corpusId="c1" />);
    // g/m/tc 列
    expect(screen.getByText("g 指数")).toBeInTheDocument();
    expect(screen.getByText("被引总数")).toBeInTheDocument();
    // Bradford 排名/累计%
    expect(screen.getByText("排名")).toBeInTheDocument();
    expect(screen.getByText("累计%")).toBeInTheDocument();
    expect(screen.getByText("12.5%")).toBeInTheDocument();
    // 核心区高亮：Zone 1 行加 row-core 类，分区名加 bradford-core
    expect(container.querySelector("tr.row-core")).toBeTruthy();
    expect(container.querySelector(".bradford-core")).toBeTruthy();
  });

  it("缺 g/m/tc（旧数据）→ 显示「—」不崩", () => {
    sourcesSpy.mockReturnValue({
      data: {
        topSources: [],
        hIndex: [{ source: "J A", h: 5 }], // 无 g/m/tc
        bradford: [],
      },
      isLoading: false, isError: false,
    });
    render(<SourcesPanel projectId="1" corpusId="c1" />);
    // 三处 g/m/tc 单元格均显示 —
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(3);
  });
});

// ============================================================
// 5) OverviewPanel — hIndex / annualGrowthRate KPI 卡
// ============================================================
describe("OverviewPanel A4 KPI 卡", () => {
  const baseStats = {
    documents: 74, sources: 30, authors: 100,
    avgCitationsPerDoc: 5.0, timespanFrom: 2016, timespanTo: 2026,
  };
  it("有 hIndex/annualGrowthRate → 渲染对应卡", () => {
    overviewSpy.mockReturnValue({
      data: { stats: { ...baseStats, hIndex: 21, annualGrowthRate: 12.9 }, annualProduction: [] },
      isLoading: false, isError: false,
    });
    render(<OverviewPanel projectId="1" corpusId="c1" />);
    expect(screen.getByText("H 指数")).toBeInTheDocument();
    expect(screen.getByText("21")).toBeInTheDocument();
    expect(screen.getByText("年均增长率")).toBeInTheDocument();
    expect(screen.getByText("12.9%")).toBeInTheDocument();
  });
  it("缺 hIndex/annualGrowthRate → 隐藏卡，不崩", () => {
    overviewSpy.mockReturnValue({
      data: { stats: baseStats, annualProduction: [] },
      isLoading: false, isError: false,
    });
    render(<OverviewPanel projectId="1" corpusId="c1" />);
    expect(screen.queryByText("H 指数")).not.toBeInTheDocument();
    expect(screen.queryByText("年均增长率")).not.toBeInTheDocument();
  });
});
