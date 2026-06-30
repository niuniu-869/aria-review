/**
 * vizPrimitives.test.tsx — A0 图表地基原语单测（vitest + jsdom）
 *
 * jsdom 下 ECharts 无法真渲染 canvas → mock echartsSetup，断言传给 init/setOption 的 option 结构。
 * 其余原语（DataTable/NodeCountSlider/ExportMenu/InsufficientData/ChartCard）直接渲染交互断言。
 * 像素/插件/真实导出靠 Playwright 截图补（见设计 §7）。
 */
import { render, screen, fireEvent, within } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";

// ---- mock echarts 实例 ----
// vi.hoisted：mock 工厂被提升到文件顶部，引用的 spy 必须在 hoisted 块里定义，否则 ReferenceError。
// 显式签名让 mock.calls 元组带参数元素（否则 TS 推断为长度 0 元组）。
// 仅在模块作用域解构测试用得到的 spy；其余（resize/on/off/getDataURL/...）只在工厂内部用
const { setOptionSpy, disposeSpy, initSpy } = vi.hoisted(() => {
  const setOptionSpy = vi.fn((_opt: Record<string, unknown>, _cfg?: unknown) => {});
  const disposeSpy = vi.fn();
  const initSpy = vi.fn((_el: unknown, _theme?: string, _cfg?: unknown) => ({
    setOption: setOptionSpy,
    dispose: disposeSpy,
    resize: vi.fn(),
    on: vi.fn(),
    off: vi.fn(),
    getDataURL: vi.fn(() => "data:image/png;base64,FAKE"),
    renderToSVGString: vi.fn(() => "<svg>fake</svg>"),
  }));
  return { setOptionSpy, disposeSpy, initSpy };
});

// EChart 从 ./echartsSetup 取 echarts，从 theme 取 registerBiblioTheme → 都 mock
vi.mock("../components/viz/echartsSetup", () => ({
  echarts: { init: initSpy, registerTheme: vi.fn() },
}));
vi.mock("../theme/echartsTheme", () => ({
  registerBiblioTheme: vi.fn(),
}));

// ResizeObserver 在 jsdom 缺失，补 stub
beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  );
  // 默认非 reduced-motion
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((q: string) => ({
      matches: false,
      media: q,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }))
  );
});

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

// 延迟 import：确保 mock 先生效
import { EChart } from "../components/viz/EChart";
import type { EChartHandle } from "../components/viz/EChart";
import { ChartCard } from "../components/viz/ChartCard";
import { DataTable } from "../components/viz/DataTable";
import type { DataTableColumn } from "../components/viz/DataTable";
import { NodeCountSlider } from "../components/viz/NodeCountSlider";
import { ExportMenu, timestamp } from "../components/viz/ExportMenu";
import { InsufficientData } from "../components/viz/InsufficientData";
import { createRef } from "react";

// ============================================================
// EChart
// ============================================================
describe("EChart", () => {
  it("init 用 bibliocn 主题，并把 option 传给 setOption", () => {
    const option = { series: [{ type: "line", data: [1, 2, 3] }] } as never;
    render(<EChart option={option} />);
    // init(el, 'bibliocn', {renderer})
    expect(initSpy).toHaveBeenCalledTimes(1);
    expect(initSpy.mock.calls[0][1]).toBe("bibliocn");
    // setOption 收到原 option（非 reduced-motion 不加 animation:false）
    expect(setOptionSpy).toHaveBeenCalled();
    const passed = setOptionSpy.mock.calls[0][0];
    expect(passed).toMatchObject({ series: [{ type: "line" }] });
    expect(passed.animation).toBeUndefined();
  });

  it("prefers-reduced-motion 时关闭动画", () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockImplementation((q: string) => ({
        matches: true, // reduced
        media: q,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }))
    );
    render(<EChart option={{ series: [] } as never} />);
    const passed = setOptionSpy.mock.calls[0][0];
    expect(passed.animation).toBe(false);
  });

  it("卸载时 dispose 实例", () => {
    const { unmount } = render(<EChart option={{ series: [] } as never} />);
    unmount();
    expect(disposeSpy).toHaveBeenCalled();
  });

  it("ref 暴露 getDataURL / renderToSVGString", () => {
    const ref = createRef<EChartHandle>();
    render(<EChart ref={ref} option={{ series: [] } as never} renderer="svg" />);
    expect(ref.current?.getDataURL()).toBe("data:image/png;base64,FAKE");
    expect(ref.current?.renderToSVGString()).toBe("<svg>fake</svg>");
  });

  it("设置 role=img 与 aria-label", () => {
    render(<EChart option={{ series: [] } as never} ariaLabel="年度产出折线" />);
    expect(screen.getByRole("img", { name: "年度产出折线" })).toBeInTheDocument();
  });
});

// ============================================================
// ChartCard
// ============================================================
describe("ChartCard", () => {
  it("loading 时显示 spinner，不渲染 children", () => {
    render(
      <ChartCard title="标题" loading>
        <div>内容</div>
      </ChartCard>
    );
    expect(screen.queryByText("内容")).not.toBeInTheDocument();
    expect(screen.getByText("加载中…")).toBeInTheDocument();
  });

  it("error 时显示错误信息", () => {
    render(<ChartCard title="标题" error={new Error("网络炸了")} />);
    expect(screen.getByRole("alert")).toHaveTextContent("网络炸了");
  });

  it("empty 优先于 children 渲染", () => {
    render(
      <ChartCard title="标题" empty={<div>空态节点</div>}>
        <div>正常内容</div>
      </ChartCard>
    );
    expect(screen.getByText("空态节点")).toBeInTheDocument();
    expect(screen.queryByText("正常内容")).not.toBeInTheDocument();
  });

  it("正常态渲染 children + actions 槽", () => {
    render(
      <ChartCard title="标题" subtitle="副标题" actions={<button>导出</button>}>
        <div>图表内容</div>
      </ChartCard>
    );
    expect(screen.getByText("图表内容")).toBeInTheDocument();
    expect(screen.getByText("副标题")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "导出" })).toBeInTheDocument();
  });
});

// ============================================================
// DataTable
// ============================================================
interface Row {
  name: string;
  count: number;
  [key: string]: unknown;
}
const cols: DataTableColumn<Row>[] = [
  { key: "name", label: "名称", sortable: true },
  { key: "count", label: "数量", sortable: true, align: "right" },
];

describe("DataTable", () => {
  const rows: Row[] = [
    { name: "乙", count: 30 },
    { name: "甲", count: 10 },
    { name: "丙", count: 20 },
  ];

  it("空数据显示 emptyText", () => {
    render(<DataTable columns={cols} rows={[]} emptyText="木有数据" />);
    expect(screen.getByText("木有数据")).toBeInTheDocument();
  });

  it("点击数字表头升序/降序排序", () => {
    render(<DataTable columns={cols} rows={rows} />);
    const countHeader = screen.getByRole("button", { name: /数量/ });
    // 第一次点击：升序 → 第一行应是 10
    fireEvent.click(countHeader);
    let body = screen.getAllByRole("row").slice(1); // 跳过表头
    expect(within(body[0]).getByText("10")).toBeInTheDocument();
    // 第二次点击：降序 → 第一行应是 30
    fireEvent.click(countHeader);
    body = screen.getAllByRole("row").slice(1);
    expect(within(body[0]).getByText("30")).toBeInTheDocument();
  });

  it("分页：pageSize 控制每页行数 + 翻页", () => {
    const many: Row[] = Array.from({ length: 25 }, (_, i) => ({
      name: `行${i}`,
      count: i,
    }));
    render(<DataTable columns={cols} rows={many} pageSize={10} />);
    // 第 1 页 10 行 + 1 表头 = 11 行
    expect(screen.getAllByRole("row")).toHaveLength(11);
    // 页码信息
    expect(screen.getByText("1 / 3")).toBeInTheDocument();
    // 翻到下一页
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    expect(screen.getByText("2 / 3")).toBeInTheDocument();
  });

  it("initialSort 应用初始排序", () => {
    render(
      <DataTable columns={cols} rows={rows} initialSort={{ key: "count", dir: "desc" }} />
    );
    const body = screen.getAllByRole("row").slice(1);
    expect(within(body[0]).getByText("30")).toBeInTheDocument();
  });

  it("format 自定义单元格渲染", () => {
    render(
      <DataTable
        columns={[
          { key: "name", label: "名称" },
          { key: "count", label: "数量", format: (v) => `共${v}篇` },
        ]}
        rows={[{ name: "甲", count: 5 }]}
      />
    );
    expect(screen.getByText("共5篇")).toBeInTheDocument();
  });
});

// ============================================================
// NodeCountSlider
// ============================================================
describe("NodeCountSlider", () => {
  it("显示当前值，拖动触发 onChange", () => {
    const onChange = vi.fn();
    render(<NodeCountSlider value={30} onChange={onChange} />);
    expect(screen.getByText("30")).toBeInTheDocument();
    const range = screen.getByRole("slider");
    fireEvent.change(range, { target: { value: "50" } });
    expect(onChange).toHaveBeenCalledWith(50);
  });

  it("默认 min/max/step 与自定义 label", () => {
    render(<NodeCountSlider value={20} onChange={vi.fn()} label="Top-N" />);
    const range = screen.getByRole("slider") as HTMLInputElement;
    expect(range.min).toBe("10");
    expect(range.max).toBe("100");
    expect(range.step).toBe("10");
    expect(screen.getByText("Top-N")).toBeInTheDocument();
  });
});

// ============================================================
// ExportMenu
// ============================================================
describe("ExportMenu", () => {
  let createObjectURLSpy: ReturnType<typeof vi.fn>;
  let revokeObjectURLSpy: ReturnType<typeof vi.fn>;
  let clickSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    createObjectURLSpy = vi.fn(() => "blob:fake");
    revokeObjectURLSpy = vi.fn();
    vi.stubGlobal("URL", {
      createObjectURL: createObjectURLSpy,
      revokeObjectURL: revokeObjectURLSpy,
    });
    // 拦截 a.click 避免 jsdom 真导航
    clickSpy = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(clickSpy);
  });

  it("timestamp 格式为 YYYYMMDD_HHMMSS", () => {
    const ts = timestamp(new Date(2026, 4, 23, 9, 8, 7)); // 月份从 0
    expect(ts).toBe("20260523_090807");
  });

  it("ECharts 目标(svgCapable)提供 PNG/SVG/CSV，点 SVG 触发下载", () => {
    const handle: EChartHandle = {
      getDataURL: () => "data:image/png;base64,X",
      renderToSVGString: () => "<svg/>",
      getInstance: () => null,
    };
    render(
      <ExportMenu
        filename="折线"
        target={{
          kind: "echart",
          getHandle: () => handle,
          svgCapable: true, // 显式声明用 svg renderer，SVG 导出才可用
          csv: { columns: [{ key: "y", label: "年" }], rows: [{ y: 2024 }] },
        }}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: /导出/ }));
    // 三项齐全
    expect(screen.getByRole("menuitem", { name: "PNG 图片" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "SVG 矢量图" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "CSV 数据" })).toBeInTheDocument();
    // 点 CSV → 创建 blob + a.click
    fireEvent.click(screen.getByRole("menuitem", { name: "CSV 数据" }));
    expect(createObjectURLSpy).toHaveBeenCalled();
    expect(clickSpy).toHaveBeenCalled();
  });

  it("ECharts 目标默认(canvas renderer, 无 svgCapable)不显示 SVG 选项", () => {
    const handle: EChartHandle = {
      getDataURL: () => "data:image/png;base64,X",
      renderToSVGString: () => undefined, // canvas renderer 无 SVG
      getInstance: () => null,
    };
    render(<ExportMenu target={{ kind: "echart", getHandle: () => handle }} />);
    fireEvent.click(screen.getByRole("button", { name: /导出/ }));
    expect(screen.getByRole("menuitem", { name: "PNG 图片" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "SVG 矢量图" })).not.toBeInTheDocument();
  });

  it("vis-network 目标无 SVG，提供 PNG/CSV/JSON", () => {
    const canvas = document.createElement("canvas");
    vi.spyOn(canvas, "toDataURL").mockReturnValue("data:image/png;base64,NET");
    render(
      <ExportMenu
        target={{
          kind: "network",
          getCanvas: () => canvas,
          csv: { columns: [{ key: "id", label: "节点" }], rows: [{ id: "a" }] },
          json: () => ({ nodes: [], edges: [] }),
        }}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: /导出/ }));
    expect(screen.getByRole("menuitem", { name: "PNG 图片" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "SVG 矢量图" })).not.toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "CSV 数据" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "JSON 数据" })).toBeInTheDocument();
  });

  it("table 目标仅 CSV：无 PNG/SVG/JSON，点 CSV 触发下载", () => {
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    render(
      <ExportMenu
        filename="高被引参考文献"
        target={{
          kind: "table",
          csv: {
            columns: [
              { key: "ref", label: "参考文献" },
              { key: "count", label: "被引次数" },
            ],
            rows: [{ ref: "LOUGHRAN 2011", count: 34 }],
          },
        }}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: /导出/ }));
    // 纯表格：无图像/JSON 项，仅 CSV
    expect(screen.queryByRole("menuitem", { name: "PNG 图片" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "SVG 矢量图" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "JSON 数据" })).not.toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "CSV 数据" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: "CSV 数据" }));
    expect(clickSpy).toHaveBeenCalled();
  });

  it("PNG 文件名带前缀与时间戳", () => {
    const handle: EChartHandle = {
      getDataURL: () => "data:image/png;base64,X",
      renderToSVGString: () => undefined,
      getInstance: () => null,
    };
    let downloadName = "";
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement
    ) {
      downloadName = this.download;
    });
    render(<ExportMenu filename="趋势" target={{ kind: "echart", getHandle: () => handle }} />);
    fireEvent.click(screen.getByRole("button", { name: /导出/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: "PNG 图片" }));
    expect(downloadName).toMatch(/^趋势_\d{8}_\d{6}\.png$/);
  });
});

// ============================================================
// InsufficientData
// ============================================================
describe("InsufficientData", () => {
  it("missing_field + 字段名 → 标题含字段", () => {
    render(
      <InsufficientData
        reason="missing_field"
        missingField="CR"
        message="当前 PDF 导入语料缺被引参考文献字段"
        howto="可从 WoS/OpenAlex 导入含 CR 的题录"
      />
    );
    expect(screen.getByText(/缺少字段「CR」/)).toBeInTheDocument();
    expect(screen.getByText(/缺被引参考文献字段/)).toBeInTheDocument();
    expect(screen.getByText(/WoS\/OpenAlex/)).toBeInTheDocument();
  });

  it("not_enough_data 默认标题", () => {
    render(<InsufficientData reason="not_enough_data" />);
    expect(screen.getByText("数据样本不足")).toBeInTheDocument();
  });

  it("computed_empty 默认标题", () => {
    render(<InsufficientData reason="computed_empty" />);
    expect(screen.getByText("计算结果为空")).toBeInTheDocument();
  });

  it("analysis_error 默认标题", () => {
    render(<InsufficientData reason="analysis_error" />);
    expect(screen.getByText("分析计算出错")).toBeInTheDocument();
  });

  it("missing_field 无字段名时用默认标题", () => {
    render(<InsufficientData reason="missing_field" />);
    expect(screen.getByText("缺少所需字段")).toBeInTheDocument();
  });
});
