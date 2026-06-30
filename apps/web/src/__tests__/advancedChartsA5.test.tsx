/**
 * advancedChartsA5.test.tsx — A5 高级图② 前端渲染单测
 *
 * 覆盖:
 *  1) 纯函数 option 构造: buildThematicScatterOption / buildEvolutionSankeyOption /
 *     buildHistciteGraphOption / buildThreeFieldSankeyOption
 *  2) 组件三态: ConceptualPanel(主题战略图+演进) / IntellectualPanel(历史引文) /
 *     OverviewPanel(三字段) 的 loading / unavailable(InsufficientData) / error / available
 *  3) 缺字段降级: available:false / missing_field → InsufficientData 文案
 *
 * 策略（同 advancedChartsA4.test.tsx）: jsdom mock echarts 实例，断言传给 setOption 的 option。
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
  ensureWordCloud: vi.fn(() => Promise.resolve(false)),
}));
vi.mock("../theme/echartsTheme", () => ({ registerBiblioTheme: vi.fn() }));

// NetworkGraphLazy 在 jsdom 下不渲染真实 vis-network；mock 为占位（不影响信封图断言）
vi.mock("../components/NetworkGraphLazy", () => ({
  NetworkGraphLazy: () => null,
}));

// ---- mock api/hooks ----
const {
  conceptualSpy,
  thematicSpy,
  evolutionSpy,
  intellectualSpy,
  histciteSpy,
  overviewSpy,
  threefieldSpy,
} = vi.hoisted(() => ({
  conceptualSpy: vi.fn(),
  thematicSpy: vi.fn(),
  evolutionSpy: vi.fn(),
  intellectualSpy: vi.fn(),
  histciteSpy: vi.fn(),
  overviewSpy: vi.fn(),
  threefieldSpy: vi.fn(),
}));
vi.mock("../api/hooks", () => ({
  useConceptual: (...a: unknown[]) => conceptualSpy(...a),
  useThematic: (...a: unknown[]) => thematicSpy(...a),
  useEvolution: (...a: unknown[]) => evolutionSpy(...a),
  useIntellectual: (...a: unknown[]) => intellectualSpy(...a),
  useHistcite: (...a: unknown[]) => histciteSpy(...a),
  useOverview: (...a: unknown[]) => overviewSpy(...a),
  useThreefield: (...a: unknown[]) => threefieldSpy(...a),
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
  // 网络 hook 默认返回空图（不影响信封图断言）
  conceptualSpy.mockReturnValue({ data: { graph: { nodes: [], edges: [] } }, isLoading: false, isError: false });
  intellectualSpy.mockReturnValue({ data: { graph: { nodes: [], edges: [] } }, isLoading: false, isError: false });
  overviewSpy.mockReturnValue({ data: { stats: undefined, annualProduction: [] }, isLoading: false, isError: false });
  // 信封 hook 默认 loading（各 describe 内 override）
  thematicSpy.mockReturnValue({ data: undefined, isLoading: true, isError: false });
  evolutionSpy.mockReturnValue({ data: undefined, isLoading: true, isError: false });
  histciteSpy.mockReturnValue({ data: undefined, isLoading: true, isError: false });
  threefieldSpy.mockReturnValue({ data: undefined, isLoading: true, isError: false });
});
afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

// 延迟 import：mock 先生效
import {
  buildThematicScatterOption,
  buildEvolutionSankeyOption,
  buildHistciteGraphOption,
  buildThreeFieldSankeyOption,
} from "../components/viz/advancedCharts";
import { ConceptualPanel } from "../components/ConceptualPanel";
import { IntellectualPanel } from "../components/IntellectualPanel";
import { OverviewPanel } from "../components/OverviewPanel";

function optionsWith(predicate: (o: Record<string, unknown>) => boolean) {
  return setOptionSpy.mock.calls.map((c) => c[0]).filter(predicate);
}
function seriesType(o: Record<string, unknown>): string | undefined {
  const s = o.series as Array<{ type?: string }> | undefined;
  return Array.isArray(s) ? s[0]?.type : undefined;
}

// ============================================================
// 1) 纯函数: option 构造
// ============================================================
describe("buildThematicScatterOption", () => {
  it("scatter series + 四象限 markLine + 象限标注 markPoint + 气泡大小按 freq", () => {
    const opt = buildThematicScatterOption({
      clusters: [
        { label: "A", centrality: 10, density: 8, freq: 40 },
        { label: "B", centrality: 2, density: 3, freq: 5 },
      ],
    });
    const series = opt.series as Array<{
      type: string;
      data: Array<{ value: [number, number, number]; symbolSize: number }>;
      markLine: { data: unknown[] };
      markPoint: { data: Array<{ value: string }> };
    }>;
    expect(series[0].type).toBe("scatter");
    expect(series[0].data[0].value).toEqual([10, 8, 40]);
    // 高频气泡更大
    expect(series[0].data[0].symbolSize).toBeGreaterThan(series[0].data[1].symbolSize);
    // 四象限参考线（x 中位 + y 中位）
    expect(series[0].markLine.data.length).toBe(2);
    // 四象限标注文案
    const labels = series[0].markPoint.data.map((d) => d.value);
    expect(labels).toContain("驱动主题");
    expect(labels).toContain("基础主题");
    expect(labels).toContain("小众主题");
    expect(labels).toContain("新兴或衰退主题");
  });
});

describe("buildEvolutionSankeyOption", () => {
  it("sankey series + 节点按 period 唯一键 + links 用 id 解析为节点键", () => {
    const opt = buildEvolutionSankeyOption({
      nodes: [
        { name: "AI", period: "2010-2015", id: 0 },
        { name: "AI", period: "2016-2020", id: 1 },
      ],
      links: [{ source: 0, target: 1, value: 0.8 }],
    });
    const series = opt.series as Array<{ type: string; data: unknown[]; links: Array<{ source: string; target: string; value: number }> }>;
    expect(series[0].type).toBe("sankey");
    expect(series[0].data).toHaveLength(2);
    // link 用唯一键（period｜name｜id）连
    expect(series[0].links[0].source).toContain("2010-2015");
    expect(series[0].links[0].target).toContain("2016-2020");
    expect(series[0].links[0].value).toBe(0.8);
  });

  it("link 引用了不存在的 id → 丢弃，不崩", () => {
    const opt = buildEvolutionSankeyOption({
      nodes: [{ name: "X", period: "P1", id: 0 }],
      links: [{ source: 0, target: 99, value: 1 }],
    });
    const series = opt.series as Array<{ links: unknown[] }>;
    expect(series[0].links).toHaveLength(0);
  });
});

describe("buildHistciteGraphOption", () => {
  it("graph series layout=none + 节点带 x/y 坐标 + 边带箭头", () => {
    const opt = buildHistciteGraphOption({
      nodes: [
        { id: "1", year: 2010, label: "A 2010", localCites: 30 },
        { id: "2", year: 2015, label: "B 2015", localCites: 10 },
      ],
      edges: [{ from: "2", to: "1" }],
    });
    const series = opt.series as Array<{
      type: string;
      layout: string;
      data: Array<{ id: string; x: number; y: number; symbolSize: number }>;
      edges: Array<{ source: string; target: string }>;
      edgeSymbol: string[];
    }>;
    expect(series[0].type).toBe("graph");
    expect(series[0].layout).toBe("none");
    // 早年节点 y 更小（自上而下）
    const n2010 = series[0].data.find((n) => n.id === "1")!;
    const n2015 = series[0].data.find((n) => n.id === "2")!;
    expect(n2010.y).toBeLessThan(n2015.y);
    // 高被引节点更大
    expect(n2010.symbolSize).toBeGreaterThan(n2015.symbolSize);
    expect(series[0].edges[0]).toEqual({ source: "2", target: "1" });
    expect(series[0].edgeSymbol[1]).toBe("arrow");
  });

  it("年份缺失（null）节点不崩，归入末层", () => {
    const opt = buildHistciteGraphOption({
      nodes: [
        { id: "1", year: 2010, label: "A", localCites: 5 },
        { id: "2", year: null, label: "B", localCites: 1 },
      ],
      edges: [],
    });
    const series = opt.series as Array<{ data: unknown[] }>;
    expect(series[0].data).toHaveLength(2);
  });
});

describe("buildThreeFieldSankeyOption", () => {
  it("sankey series + 三层配色 + label 去前缀", () => {
    const opt = buildThreeFieldSankeyOption({
      nodes: [
        { name: "A:ARIA M", layer: 0 },
        { name: "K:BIBLIO", layer: 1 },
        { name: "S:J INFO", layer: 2 },
      ],
      links: [
        { source: "A:ARIA M", target: "K:BIBLIO", value: 3 },
        { source: "K:BIBLIO", target: "S:J INFO", value: 2 },
      ],
    });
    const series = opt.series as Array<{
      type: string;
      data: Array<{ name: string; itemStyle: { color: string }; label: { formatter: () => string } }>;
      links: unknown[];
    }>;
    expect(series[0].type).toBe("sankey");
    expect(series[0].data).toHaveLength(3);
    expect(series[0].links).toHaveLength(2);
    // 作者层朱砂、关键词层靛蓝、来源层金
    expect(series[0].data[0].itemStyle.color).toBe("#c0432b");
    expect(series[0].data[1].itemStyle.color).toBe("#2f4858");
    expect(series[0].data[2].itemStyle.color).toBe("#b08423");
    // label 去前缀
    expect(series[0].data[0].label.formatter()).toBe("ARIA M");
  });
});

// ============================================================
// 2+3) ConceptualPanel — 主题战略图 + 演进 三态
// ============================================================
describe("ConceptualPanel 主题战略图 + 演进", () => {
  it("thematic available:true → scatter 出图；evolution available:true → sankey 出图", () => {
    thematicSpy.mockReturnValue({
      data: { available: true, data: { clusters: [{ label: "A", centrality: 5, density: 4, freq: 20 }] } },
      isLoading: false, isError: false,
    });
    evolutionSpy.mockReturnValue({
      data: {
        available: true,
        data: { nodes: [{ name: "AI", period: "P1", id: 0 }, { name: "AI", period: "P2", id: 1 }], links: [{ source: 0, target: 1, value: 1 }] },
      },
      isLoading: false, isError: false,
    });
    render(<ConceptualPanel projectId="1" corpusId="c1" />);
    expect(screen.getByText("主题战略图")).toBeInTheDocument();
    expect(screen.getByText("主题演进图")).toBeInTheDocument();
    expect(optionsWith((o) => seriesType(o) === "scatter").length).toBeGreaterThan(0);
    expect(optionsWith((o) => seriesType(o) === "sankey").length).toBeGreaterThan(0);
  });

  it("thematic missing_field（缺 DE）→ InsufficientData 降级", () => {
    thematicSpy.mockReturnValue({
      data: { available: false, reason: "missing_field", missingField: "DE", message: "缺关键词字段" },
      isLoading: false, isError: false,
    });
    evolutionSpy.mockReturnValue({
      data: { available: false, reason: "not_enough_data", message: "年份跨度不足" },
      isLoading: false, isError: false,
    });
    render(<ConceptualPanel projectId="1" corpusId="c1" />);
    expect(screen.getByText("缺少字段「DE」")).toBeInTheDocument();
    expect(screen.getByText("年份跨度不足")).toBeInTheDocument();
  });

  it("thematic error 态 → 错误，不显示 InsufficientData", () => {
    thematicSpy.mockReturnValue({ data: undefined, isLoading: false, isError: true, error: new Error("HTTP 502") });
    render(<ConceptualPanel projectId="1" corpusId="c1" />);
    expect(screen.getAllByRole("alert").some((e) => e.textContent?.includes("HTTP 502"))).toBe(true);
    expect(screen.queryByText(/缺少字段/)).not.toBeInTheDocument();
  });

  it("thematic loading 态 → 加载中", () => {
    render(<ConceptualPanel projectId="1" corpusId="c1" />);
    expect(screen.getAllByText("加载中…").length).toBeGreaterThan(0);
  });
});

// ============================================================
// 2+3) IntellectualPanel — 历史引文 三态
// ============================================================
describe("IntellectualPanel 历史引文图", () => {
  it("histcite available:true → graph 出图", () => {
    histciteSpy.mockReturnValue({
      data: {
        available: true,
        data: { nodes: [{ id: "1", year: 2010, label: "A", localCites: 5 }, { id: "2", year: 2015, label: "B", localCites: 3 }], edges: [{ from: "2", to: "1" }] },
      },
      isLoading: false, isError: false,
    });
    render(<IntellectualPanel projectId="1" corpusId="c1" />);
    expect(screen.getByText("历史引文图")).toBeInTheDocument();
    expect(optionsWith((o) => seriesType(o) === "graph").length).toBeGreaterThan(0);
  });

  it("histcite missing_field（缺 CR）→ InsufficientData 降级", () => {
    histciteSpy.mockReturnValue({
      data: { available: false, reason: "missing_field", missingField: "CR", message: "缺参考文献字段" },
      isLoading: false, isError: false,
    });
    render(<IntellectualPanel projectId="1" corpusId="c1" />);
    expect(screen.getByText("缺少字段「CR」")).toBeInTheDocument();
  });

  it("histcite not_enough_data → InsufficientData 降级", () => {
    histciteSpy.mockReturnValue({
      data: { available: false, reason: "not_enough_data", message: "历史引文网络节点过少" },
      isLoading: false, isError: false,
    });
    render(<IntellectualPanel projectId="1" corpusId="c1" />);
    expect(screen.getByText("历史引文网络节点过少")).toBeInTheDocument();
  });

  it("histcite error 态 → 错误", () => {
    histciteSpy.mockReturnValue({ data: undefined, isLoading: false, isError: true, error: new Error("网络错误") });
    render(<IntellectualPanel projectId="1" corpusId="c1" />);
    expect(screen.getAllByRole("alert").some((e) => e.textContent?.includes("网络错误"))).toBe(true);
  });
});

// ============================================================
// 2+3) OverviewPanel — 三字段 Sankey 三态
// ============================================================
describe("OverviewPanel 三字段流向图", () => {
  it("threefield available:true → sankey 出图", () => {
    threefieldSpy.mockReturnValue({
      data: {
        available: true,
        data: {
          nodes: [{ name: "A:X", layer: 0 }, { name: "K:Y", layer: 1 }, { name: "S:Z", layer: 2 }],
          links: [{ source: "A:X", target: "K:Y", value: 2 }, { source: "K:Y", target: "S:Z", value: 1 }],
        },
      },
      isLoading: false, isError: false,
    });
    render(<OverviewPanel projectId="1" corpusId="c1" />);
    expect(screen.getByText("三字段流向图")).toBeInTheDocument();
    expect(optionsWith((o) => seriesType(o) === "sankey").length).toBeGreaterThan(0);
  });

  it("threefield missing_field（缺 SO）→ InsufficientData 降级", () => {
    threefieldSpy.mockReturnValue({
      data: { available: false, reason: "missing_field", missingField: "SO", message: "缺来源字段" },
      isLoading: false, isError: false,
    });
    render(<OverviewPanel projectId="1" corpusId="c1" />);
    expect(screen.getByText("缺少字段「SO」")).toBeInTheDocument();
  });

  it("threefield error 态 → 错误", () => {
    threefieldSpy.mockReturnValue({ data: undefined, isLoading: false, isError: true, error: new Error("HTTP 500") });
    render(<OverviewPanel projectId="1" corpusId="c1" />);
    expect(screen.getAllByRole("alert").some((e) => e.textContent?.includes("HTTP 500"))).toBe(true);
  });
});
