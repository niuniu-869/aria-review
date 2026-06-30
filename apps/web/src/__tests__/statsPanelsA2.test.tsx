/**
 * statsPanelsA2.test.tsx — A2 统计概览组（Overview/Sources/Authors）渲染 + 空态单测
 *
 * 策略（同 vizPrimitives.test.tsx）：
 *  - jsdom 无真 canvas → mock echartsSetup，断言传给 setOption 的 option 结构（出图）。
 *  - mock api/hooks 的 useOverview/useSources/useAuthors，构造数据 / 空数据两类场景。
 *  - 断言：KPI 卡 / 表格行 / 折线+面积 / Lotka 散点+理论曲线出图；空态文案不崩。
 */
import { render, screen } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";

// ---- mock echarts 实例（捕获 setOption 的 option 供断言出图）----
const { setOptionSpy, initSpy } = vi.hoisted(() => {
  const setOptionSpy = vi.fn((_opt: Record<string, unknown>, _cfg?: unknown) => {});
  const initSpy = vi.fn((_el: unknown, _theme?: string, _cfg?: unknown) => ({
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
}));
vi.mock("../theme/echartsTheme", () => ({ registerBiblioTheme: vi.fn() }));

// ---- mock api/hooks（纯渲染，不触网）----
const { overviewSpy, sourcesSpy, authorsSpy } = vi.hoisted(() => ({
  overviewSpy: vi.fn(),
  sourcesSpy: vi.fn(),
  authorsSpy: vi.fn(),
}));
vi.mock("../api/hooks", () => ({
  useOverview: (...a: unknown[]) => overviewSpy(...a),
  useSources: (...a: unknown[]) => sourcesSpy(...a),
  useAuthors: (...a: unknown[]) => authorsSpy(...a),
  // A4: AuthorsPanel 现也调用作者年度产出信封 hook；A2 测试只关心既有功能，
  // 故给一个稳定的 loading 桩，避免「No export」错误（热力图卡显示加载中，不影响断言）。
  useAuthorProduction: () => ({ data: undefined, isLoading: true, isError: false }),
  // A5: OverviewPanel 现也调用三字段信封 hook；同上给 loading 桩。
  useThreefield: () => ({ data: undefined, isLoading: true, isError: false }),
}));

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((q: string) => ({
      matches: false,
      media: q,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
});

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

// 延迟 import：mock 先生效
import { OverviewPanel } from "../components/OverviewPanel";
import { SourcesPanel } from "../components/SourcesPanel";
import { AuthorsPanel } from "../components/AuthorsPanel";

/** 取最后一次 setOption 的 option（多图共用 spy 时取最近一次） */
function lastOption(): Record<string, unknown> {
  const calls = setOptionSpy.mock.calls;
  return calls[calls.length - 1][0];
}
type Series = { type: string };

// ============================================================
// OverviewPanel
// ============================================================
describe("OverviewPanel", () => {
  const stats = {
    documents: 50,
    sources: 12,
    authors: 80,
    avgCitationsPerDoc: 3.2,
    timespanFrom: 2016,
    timespanTo: 2024,
  };

  it("渲染 5 张 KPI 卡 + 年度产出折线面积图", () => {
    overviewSpy.mockReturnValue({
      data: {
        stats,
        annualProduction: [
          { year: 2022, articles: 10 },
          { year: 2023, articles: 18 },
        ],
      },
      isLoading: false,
      isError: false,
    });
    render(<OverviewPanel projectId="1" corpusId="c1" />);
    // KPI
    expect(screen.getByText("文献数")).toBeInTheDocument();
    expect(screen.getByText("50")).toBeInTheDocument();
    expect(screen.getByText("2016–2024")).toBeInTheDocument();
    // 年度产出图：line series + 面积渐变
    const opt = lastOption();
    const series = opt.series as Array<Series & { areaStyle?: unknown }>;
    expect(series[0].type).toBe("line");
    expect(series[0].areaStyle).toBeTruthy();
    // 导出菜单存在
    expect(screen.getByRole("button", { name: /导出/ })).toBeInTheDocument();
  });

  it("年度产出为空 → ChartCard 空态，不崩", () => {
    overviewSpy.mockReturnValue({
      data: { stats, annualProduction: [] },
      isLoading: false,
      isError: false,
    });
    render(<OverviewPanel projectId="1" corpusId="c1" />);
    expect(screen.getByText("暂无年度产出数据")).toBeInTheDocument();
    // 空态时不渲染导出
    expect(screen.queryByRole("button", { name: /导出/ })).not.toBeInTheDocument();
  });

  it("加载态 → ChartCard 显示加载中", () => {
    overviewSpy.mockReturnValue({ data: undefined, isLoading: true, isError: false });
    render(<OverviewPanel projectId="1" corpusId="c1" />);
    // A5: 三字段卡同处 loading 桩 → 页面可能有多张加载卡，断言至少一张即可。
    expect(screen.getAllByText("加载中…").length).toBeGreaterThan(0);
  });
});

// ============================================================
// SourcesPanel
// ============================================================
describe("SourcesPanel", () => {
  it("有数据 → 三表渲染来源行", () => {
    sourcesSpy.mockReturnValue({
      data: {
        topSources: [{ source: "Journal A", articles: 9 }],
        hIndex: [{ source: "Journal A", h: 5 }],
        bradford: [{ source: "Journal A", zone: "Zone 1", freq: 9 }],
      },
      isLoading: false,
      isError: false,
    });
    render(<SourcesPanel projectId="1" corpusId="c1" />);
    expect(screen.getByText("最相关来源")).toBeInTheDocument();
    expect(screen.getByText("来源 H 指数")).toBeInTheDocument();
    expect(screen.getByText("Bradford 分区")).toBeInTheDocument();
    expect(screen.getAllByText("Journal A").length).toBeGreaterThanOrEqual(3);
    expect(screen.getByText("Zone 1")).toBeInTheDocument();
  });

  it("三数组全空（PDF 语料）→ 三处友好空态，不崩", () => {
    sourcesSpy.mockReturnValue({
      data: { topSources: [], hIndex: [], bradford: [] },
      isLoading: false,
      isError: false,
    });
    render(<SourcesPanel projectId="1" corpusId="c1" />);
    expect(screen.getAllByText("当前语料无期刊/来源字段数据")).toHaveLength(3);
  });

  it("出错态 → ChartCard 显示错误", () => {
    sourcesSpy.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("加载失败"),
    });
    render(<SourcesPanel projectId="1" corpusId="c1" />);
    expect(screen.getAllByRole("alert")[0]).toHaveTextContent("加载失败");
  });
});

// ============================================================
// AuthorsPanel
// ============================================================
describe("AuthorsPanel", () => {
  it("topAuthors 出表、hIndex 空态、Lotka 散点+理论曲线出图", () => {
    authorsSpy.mockReturnValue({
      data: {
        topAuthors: [{ author: "张三", articles: 6 }],
        hIndex: [],
        lotka: {
          beta: 2.1,
          distribution: [
            { articles: 1, authors: 40 },
            { articles: 2, authors: 10 },
            { articles: 3, authors: 4 },
          ],
        },
      },
      isLoading: false,
      isError: false,
    });
    render(<AuthorsPanel projectId="1" corpusId="c1" />);
    // topAuthors 出表
    expect(screen.getByText("张三")).toBeInTheDocument();
    // hIndex 空态
    expect(screen.getByText("当前语料无作者 H 指数数据")).toBeInTheDocument();
    // Lotka：scatter(观测) + line(理论)
    const opt = lastOption();
    const series = opt.series as Series[];
    expect(series.map((s) => s.type)).toEqual(["scatter", "line"]);
    // 图例含 β
    const legend = opt.legend as { data: string[] };
    expect(legend.data).toContain("理论 (β=2.1)");
  });

  it("lotka 缺失 → ChartCard 空态，不崩", () => {
    authorsSpy.mockReturnValue({
      data: {
        topAuthors: [{ author: "李四", articles: 3 }],
        hIndex: [],
        lotka: { beta: null, distribution: [] },
      },
      isLoading: false,
      isError: false,
    });
    render(<AuthorsPanel projectId="1" corpusId="c1" />);
    expect(screen.getByText("暂无 Lotka 分布数据")).toBeInTheDocument();
  });

  it("beta<=0 → 空态（不画非法理论曲线）", () => {
    authorsSpy.mockReturnValue({
      data: {
        topAuthors: [],
        hIndex: [],
        lotka: { beta: 0, distribution: [{ articles: 1, authors: 10 }] },
      },
      isLoading: false,
      isError: false,
    });
    render(<AuthorsPanel projectId="1" corpusId="c1" />);
    expect(screen.getByText("暂无 Lotka 分布数据")).toBeInTheDocument();
  });

  it("distribution 含 articles<=0 → 空态（防 C/x^β 出 Infinity/NaN）", () => {
    authorsSpy.mockReturnValue({
      data: {
        topAuthors: [],
        hIndex: [],
        lotka: { beta: 2, distribution: [{ articles: 0, authors: 5 }, { articles: 1, authors: 10 }] },
      },
      isLoading: false,
      isError: false,
    });
    render(<AuthorsPanel projectId="1" corpusId="c1" />);
    expect(screen.getByText("暂无 Lotka 分布数据")).toBeInTheDocument();
  });

  it("无 articles=1 锚点 → 仅观测散点，不画理论曲线", () => {
    authorsSpy.mockReturnValue({
      data: {
        topAuthors: [],
        hIndex: [],
        lotka: { beta: 2, distribution: [{ articles: 2, authors: 8 }, { articles: 3, authors: 3 }] },
      },
      isLoading: false,
      isError: false,
    });
    render(<AuthorsPanel projectId="1" corpusId="c1" />);
    const opt = lastOption();
    const series = opt.series as Series[];
    expect(series.map((s) => s.type)).toEqual(["scatter"]); // 仅散点
    const legend = opt.legend as { data: string[] };
    expect(legend.data).toEqual(["观测"]); // 无理论图例
  });
});
