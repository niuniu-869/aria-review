/**
 * knowledgePanelsA3.test.tsx — A3 知识结构组（关键词热点/主题地图/知识脉络/合作网络）+ sidebar 契约
 *
 * 策略（同 statsPanelsA2.test.tsx）：
 *  - jsdom 无真 canvas → mock echartsSetup（echarts.init/setOption + ensureWordCloud 可控）。
 *  - mock NetworkGraph → 不加载 vis-network，断言「切片后的 graph」节点/边数。
 *  - mock api/hooks 构造有数据 / 空数据两类场景。
 *  - 断言：词云 ready 出图 vs 不可用降级 vs 缺字段空态；高被引表（作者列/null→—）；
 *          网络滑块切片节点数；导出 getCanvas / CSV / JSON；sidebar per-view 置灰。
 */
import { render, screen, fireEvent, within, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";
import { asDbCorpusId, asRCorpusId } from "../api/corpusIds";

const CID = asRCorpusId("c1");

// ---- mock echarts 实例（捕获 setOption 的 option 供断言出图）----
const { setOptionSpy, initSpy, ensureWordCloudSpy } = vi.hoisted(() => {
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
  // 默认词云可用（true）；个别用例改为 false 验证降级
  const ensureWordCloudSpy = vi.fn(async () => true);
  return { setOptionSpy, initSpy, ensureWordCloudSpy };
});

vi.mock("../components/viz/echartsSetup", () => ({
  echarts: { init: initSpy, registerTheme: vi.fn() },
  ensureWordCloud: ensureWordCloudSpy,
}));
vi.mock("../theme/echartsTheme", () => ({ registerBiblioTheme: vi.fn() }));

// ---- mock NetworkGraphLazy → 简易桩，渲染节点/边计数 + 一个 canvas（供 getCanvas） ----
// containerRef 透传：把 ref 绑到桩 div，使 querySelector('canvas') 可命中。
const { netCalls } = vi.hoisted(() => ({ netCalls: [] as Array<{ nodes: number; edges: number }> }));
vi.mock("../components/NetworkGraphLazy", () => ({
  NetworkGraphLazy: (props: {
    graph: { nodes: unknown[]; edges: unknown[] };
    height?: number;
    containerRef?: { current: HTMLDivElement | null };
  }) => {
    netCalls.push({ nodes: props.graph.nodes.length, edges: props.graph.edges.length });
    return (
      <div
        ref={props.containerRef}
        data-testid="net-stub"
        data-nodes={props.graph.nodes.length}
        data-edges={props.graph.edges.length}
      >
        <canvas data-testid="net-canvas" />
      </div>
    );
  },
}));

// ---- mock api/hooks（纯渲染，不触网）----
const { documentsSpy, conceptualSpy, intellectualSpy, socialSpy } = vi.hoisted(() => ({
  documentsSpy: vi.fn(),
  conceptualSpy: vi.fn(),
  intellectualSpy: vi.fn(),
  socialSpy: vi.fn(),
}));
vi.mock("../api/hooks", () => ({
  useDocuments: (...a: unknown[]) => documentsSpy(...a),
  useConceptual: (...a: unknown[]) => conceptualSpy(...a),
  useIntellectual: (...a: unknown[]) => intellectualSpy(...a),
  useSocial: (...a: unknown[]) => socialSpy(...a),
  // A4: DocumentsPanel 现也调用关键词历时演变 / 高被引参考文献信封 hook；
  // A3 测试只关心词云 + 高被引文献既有功能，故给稳定 loading 桩避免「No export」错误。
  useKeywordTrend: () => ({ data: undefined, isLoading: true, isError: false }),
  useCitedRefs: () => ({ data: undefined, isLoading: true, isError: false }),
  // A5: ConceptualPanel 现加主题战略图/演进、IntellectualPanel 现加历史引文信封 hook；
  // A3 测试只关心网络图既有功能，故给稳定 loading 桩避免「No export」错误。
  useThematic: () => ({ data: undefined, isLoading: true, isError: false }),
  useEvolution: () => ({ data: undefined, isLoading: true, isError: false }),
  useHistcite: () => ({ data: undefined, isLoading: true, isError: false }),
}));

beforeEach(() => {
  netCalls.length = 0;
  ensureWordCloudSpy.mockResolvedValue(true);
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
import { DocumentsPanel } from "../components/DocumentsPanel";
import { ConceptualPanel } from "../components/ConceptualPanel";
import { IntellectualPanel } from "../components/IntellectualPanel";
import { SocialPanel } from "../components/SocialPanel";
import { AnalysisSidebar, type AnalysisViewId } from "../components/AnalysisSidebar";

/** 取最后一次 setOption 的 option */
function lastOption(): Record<string, unknown> {
  const calls = setOptionSpy.mock.calls;
  return calls[calls.length - 1][0];
}

/** 生成 n 个节点 + 链式边的 graph（节点 value 递减，便于断言 Top-N 切片） */
function makeGraph(n: number) {
  const nodes = Array.from({ length: n }, (_, i) => ({
    id: `n${i}`,
    label: `节点${i}`,
    value: n - i, // value 降序：n0 最大
  }));
  // 相邻链式边 + 一条跨越边
  const edges = Array.from({ length: Math.max(0, n - 1) }, (_, i) => ({
    source: `n${i}`,
    target: `n${i + 1}`,
    weight: 1,
  }));
  return { nodes, edges };
}

// ============================================================
// DocumentsPanel — 关键词热点
// ============================================================
describe("DocumentsPanel 关键词热点", () => {
  const topCited = [
    { title: "论文甲", author: "张三", year: 2022, cited: 30 },
    { title: "论文乙", author: null, year: null, cited: 12 },
  ];

  it("词云 ready → 出 wordCloud series + 高被引表（作者列）+ 导出菜单", async () => {
    documentsSpy.mockReturnValue({
      data: {
        keywords: [
          { term: "区块链", freq: 20 },
          { term: "供应链", freq: 8 },
          { term: "智能合约", freq: 3 },
        ],
        topCited,
      },
      isLoading: false,
      isError: false,
    });
    render(<DocumentsPanel projectId="1" corpusId={CID} />);

    // 等词云惰性注册完成后出图
    await screen.findByRole("img", { name: "关键词词云" });
    await waitFor(() => expect(setOptionSpy).toHaveBeenCalled());
    const opt = lastOption();
    const series = opt.series as Array<{ type: string; data: unknown[] }>;
    expect(series[0].type).toBe("wordCloud");
    expect(series[0].data).toHaveLength(3);

    // 高被引表：标题/作者/年份/被引列 + 行
    expect(screen.getByText("论文甲")).toBeInTheDocument();
    expect(screen.getByText("张三")).toBeInTheDocument();
    // null 作者/年份 → 未标注
    expect(screen.getAllByText("未标注").length).toBeGreaterThanOrEqual(2);

    // 导出菜单存在（词云卡）
    expect(screen.getByRole("button", { name: /导出/ })).toBeInTheDocument();
  });

  it("词云不可用(jsdom) → 降级提示 + 关键词频次表兜底", async () => {
    ensureWordCloudSpy.mockResolvedValue(false);
    documentsSpy.mockReturnValue({
      data: {
        keywords: [{ term: "区块链", freq: 20 }],
        topCited,
      },
      isLoading: false,
      isError: false,
    });
    render(<DocumentsPanel projectId="1" corpusId={CID} />);
    expect(await screen.findByText(/词云在当前环境不可用/)).toBeInTheDocument();
    // 频次表兜底：含关键词
    expect(screen.getByText("区块链")).toBeInTheDocument();
    // 词云不可用时不渲染导出（getDataURL 无意义）
    expect(screen.queryByRole("button", { name: /导出/ })).not.toBeInTheDocument();
  });

  it("keywords 空 → InsufficientData(缺字段) 空态，高被引表仍在", () => {
    documentsSpy.mockReturnValue({
      data: { keywords: [], topCited },
      isLoading: false,
      isError: false,
    });
    render(<DocumentsPanel projectId="1" corpusId={CID} />);
    expect(screen.getByText(/缺少字段「关键词\(DE\)」/)).toBeInTheDocument();
    expect(screen.getByText(/OpenAlex\/WoS/)).toBeInTheDocument();
    // 高被引表仍渲染
    expect(screen.getByText("论文甲")).toBeInTheDocument();
  });

  it("topCited 空 → 高被引表友好空态，不崩", () => {
    documentsSpy.mockReturnValue({
      data: { keywords: [], topCited: [] },
      isLoading: false,
      isError: false,
    });
    render(<DocumentsPanel projectId="1" corpusId={CID} />);
    expect(screen.getByText("当前语料无高被引文献数据")).toBeInTheDocument();
  });

  it("文献标题/作者与关键词为 null → 显示未标注，不崩", async () => {
    ensureWordCloudSpy.mockResolvedValue(false);
    documentsSpy.mockReturnValue({
      data: {
        keywords: [{ term: null, freq: null }],
        topCited: [{ title: null, author: null, year: null, cited: null }],
      },
      isLoading: false,
      isError: false,
    });
    render(<DocumentsPanel projectId="1" corpusId={CID} />);
    await screen.findByText(/词云在当前环境不可用/);
    expect(screen.getAllByText("未标注").length).toBeGreaterThanOrEqual(3);
  });

  it("被引列默认降序排序（initialSort）", () => {
    documentsSpy.mockReturnValue({
      data: { keywords: [], topCited },
      isLoading: false,
      isError: false,
    });
    render(<DocumentsPanel projectId="1" corpusId={CID} />);
    // 第一数据行的被引应为 30（降序）
    const rows = screen.getAllByRole("row").slice(1);
    expect(within(rows[0]).getByText("30")).toBeInTheDocument();
  });
});

// ============================================================
// ConceptualPanel / IntellectualPanel — 单网络 + 滑块切片
// ============================================================
describe("ConceptualPanel 主题地图", () => {
  it("12 节点 → 默认 Top-N=min(50,12)=12，渲染全部 + 显示滑块", () => {
    conceptualSpy.mockReturnValue({
      data: { graph: makeGraph(12) },
      isLoading: false,
      isError: false,
    });
    render(<ConceptualPanel projectId="1" corpusId={CID} />);
    // 节点>10 → 显示滑块
    expect(screen.getByRole("slider")).toBeInTheDocument();
    // 切片后传给 NetworkGraph 的节点数 = 12（默认全取）
    const last = netCalls[netCalls.length - 1];
    expect(last.nodes).toBe(12);
  });

  it("滑块改为 5 → 切片到 5 个最强节点，边收敛", () => {
    conceptualSpy.mockReturnValue({
      data: { graph: makeGraph(20) },
      isLoading: false,
      isError: false,
    });
    render(<ConceptualPanel projectId="1" corpusId={CID} />);
    netCalls.length = 0; // 清掉初次渲染
    // 滑块 min=10，故拖到 10 验证切片（5 低于 min，用 10）
    fireEvent.change(screen.getByRole("slider"), { target: { value: "10" } });
    const last = netCalls[netCalls.length - 1];
    expect(last.nodes).toBe(10);
    // 链式 20 节点取前 10（n0..n9，value 最大的 10 个），边为 n0-n1..n8-n9 = 9 条
    expect(last.edges).toBe(9);
  });

  it("节点≤10 → 不显示滑块", () => {
    conceptualSpy.mockReturnValue({
      data: { graph: makeGraph(6) },
      isLoading: false,
      isError: false,
    });
    render(<ConceptualPanel projectId="1" corpusId={CID} />);
    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
    expect(netCalls[netCalls.length - 1].nodes).toBe(6);
  });

  it("空 graph → InsufficientData 空态，不渲染网络", () => {
    conceptualSpy.mockReturnValue({
      data: { graph: { nodes: [], edges: [] } },
      isLoading: false,
      isError: false,
    });
    render(<ConceptualPanel projectId="1" corpusId={CID} />);
    expect(screen.getByText("数据样本不足")).toBeInTheDocument();
    expect(screen.queryByTestId("net-stub")).not.toBeInTheDocument();
  });

  it("节点<3 → 数据不足空态", () => {
    conceptualSpy.mockReturnValue({
      data: { graph: makeGraph(2) },
      isLoading: false,
      isError: false,
    });
    render(<ConceptualPanel projectId="1" corpusId={CID} />);
    expect(screen.getByText("数据样本不足")).toBeInTheDocument();
  });

  it("导出菜单：network 目标提供 PNG/CSV/JSON，getCanvas 命中桩 canvas", () => {
    conceptualSpy.mockReturnValue({
      data: { graph: makeGraph(12) },
      isLoading: false,
      isError: false,
    });
    render(<ConceptualPanel projectId="1" corpusId={CID} />);
    fireEvent.click(screen.getByRole("button", { name: /导出/ }));
    expect(screen.getByRole("menuitem", { name: "PNG 图片" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "CSV 数据" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "JSON 数据" })).toBeInTheDocument();
    // 无 SVG（network 目标）
    expect(screen.queryByRole("menuitem", { name: "SVG 矢量图" })).not.toBeInTheDocument();
    // 桩容器内有 canvas（getCanvas 可命中）
    expect(screen.getByTestId("net-canvas")).toBeInTheDocument();
  });
});

describe("IntellectualPanel 知识脉络", () => {
  it("有数据出网络；空态友好降级", () => {
    intellectualSpy.mockReturnValue({
      data: { graph: makeGraph(8) },
      isLoading: false,
      isError: false,
    });
    const { rerender } = render(<IntellectualPanel projectId="1" corpusId={CID} />);
    expect(screen.getByTestId("net-stub")).toBeInTheDocument();

    intellectualSpy.mockReturnValue({
      data: { graph: { nodes: [], edges: [] } },
      isLoading: false,
      isError: false,
    });
    rerender(<IntellectualPanel projectId="1" corpusId={CID} />);
    expect(screen.getByText("数据样本不足")).toBeInTheDocument();
  });
});

// ============================================================
// SocialPanel — 双网络独立状态
// ============================================================
describe("SocialPanel 合作网络", () => {
  it("作者+国家双网络各自渲染（两套独立切片）", () => {
    socialSpy.mockReturnValue({
      data: { authorCollab: makeGraph(15), countryCollab: makeGraph(7) },
      isLoading: false,
      isError: false,
    });
    render(<SocialPanel projectId="1" corpusId={CID} />);
    expect(screen.getByText("作者合作网络")).toBeInTheDocument();
    expect(screen.getByText("国家合作网络")).toBeInTheDocument();
    // 两个网络桩
    expect(screen.getAllByTestId("net-stub")).toHaveLength(2);
    // 作者网 15 节点 → 有滑块；国家网 7 节点 → 无滑块 → 共 1 个滑块
    expect(screen.getAllByRole("slider")).toHaveLength(1);
  });

  it("作者网有数据、国家网空 → 各自独立处理（一图一空态）", () => {
    socialSpy.mockReturnValue({
      data: { authorCollab: makeGraph(8), countryCollab: { nodes: [], edges: [] } },
      isLoading: false,
      isError: false,
    });
    render(<SocialPanel projectId="1" corpusId={CID} />);
    // 一个网络桩（作者），一个空态（国家）
    expect(screen.getAllByTestId("net-stub")).toHaveLength(1);
    expect(screen.getByText("数据样本不足")).toBeInTheDocument();
  });

  it("两网络独立导出菜单（各 1 个导出按钮）", () => {
    socialSpy.mockReturnValue({
      data: { authorCollab: makeGraph(8), countryCollab: makeGraph(6) },
      isLoading: false,
      isError: false,
    });
    render(<SocialPanel projectId="1" corpusId={CID} />);
    expect(screen.getAllByRole("button", { name: /导出/ })).toHaveLength(2);
  });
});

// ============================================================
// AnalysisSidebar — per-view requiresCorpus 契约修正
// ============================================================
describe("AnalysisSidebar per-view 置灰契约", () => {
  function renderSidebar(corpusReady: boolean) {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const activeCorpus = corpusReady
      ? {
          corpusId: asDbCorpusId(1),
          rCorpusId: asRCorpusId("r1"),
          status: "ready" as const,
          stale: false,
          documentCount: 10,
          contentHash: "h",
        }
      : null;
    return render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <AnalysisSidebar
            activeView={"overview" as AnalysisViewId}
            onSelect={vi.fn()}
            activeCorpus={activeCorpus}
            collapsed={false}
            onToggleCollapse={vi.fn()}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  /** 找到 label 文本对应的 sidebar 按钮 */
  function viewButton(label: string): HTMLButtonElement {
    return screen.getByText(label).closest("button") as HTMLButtonElement;
  }

  it("无语料：screen 置灰、prisma 可用（文献库洞察组不整体置灰）", () => {
    renderSidebar(false);
    expect(viewButton("相关性筛选")).toBeDisabled(); // screen requiresCorpus:true
    expect(viewButton("PRISMA")).not.toBeDisabled(); // prisma requiresCorpus:false
    // 文献库洞察/AI 工具台均有无语料可用视图；仅 2 个全需语料的组有徽标。
    expect(screen.getAllByText("(未就绪)")).toHaveLength(2);
  });

  it("有语料：screen 与 prisma 均可用", () => {
    renderSidebar(true);
    expect(viewButton("相关性筛选")).not.toBeDisabled();
    expect(viewButton("PRISMA")).not.toBeDisabled();
    // 全部就绪 → 无徽标
    expect(screen.queryByText("(未就绪)")).not.toBeInTheDocument();
  });

  it("无语料：知识结构组(全需语料)整体置灰", () => {
    renderSidebar(false);
    expect(viewButton("关键词热点")).toBeDisabled();
    expect(viewButton("主题地图")).toBeDisabled();
    expect(viewButton("合作网络")).toBeDisabled();
  });
});
