/**
 * advancedCharts.ts — A4/A5 高级图 ECharts option 构造 (纯函数, 供面板 + 单测复用)
 *
 * A4:
 * - buildAuthorHeatmapOption: 作者 × 年份 热力图 (visualMap 宣纸色 墨→金→朱砂)
 * - buildKeywordRiverOption:  关键词历时演变 themeRiver (堆叠流图)
 * A5:
 * - buildThematicScatterOption: 主题战略图 Callon 四象限散点 (markLine 参考线 + 象限标注)
 * - buildEvolutionSankeyOption: 主题演进 sankey (节点按 period 分层)
 * - buildHistciteGraphOption:   历史引文 graph (y=年份时序分层)
 * - buildThreeFieldSankeyOption: 三字段 sankey (作者→关键词→来源 三层)
 *
 * 宣纸色: 墨 #4a443b → 金 #b08423 → 朱砂 #c0432b。
 */
import type { EChartsOption } from "echarts";

// ---- 类型 (镜像契约 data 形状) ----
export type AuthorProductionCell = {
  author?: string | null;
  year?: number | null;
  articles?: number | null;
};
export type AuthorProductionData = {
  authors: (string | null)[];
  years: (number | null)[];
  cells: AuthorProductionCell[];
};

export type KeywordTrendCell = { year?: number | null; term?: string | null; freq?: number | null };
export type KeywordTrendData = {
  years: (number | null)[];
  terms: (string | null)[];
  cells: KeywordTrendCell[];
};
export type KeywordTrendInsufficientData = {
  reason: "computed_empty";
  message: string;
  howto: string;
};

// A5 类型 (镜像契约 data 形状)
export type ThematicCluster = {
  label?: string | null;
  centrality?: number | null;
  density?: number | null;
  freq?: number | null;
};
export type ThematicData = { clusters: ThematicCluster[] };

export type EvolutionNode = { name?: string | null; period?: string | null; id?: number | null };
export type EvolutionLink = { source?: number | null; target?: number | null; value?: number | null };
export type EvolutionData = { nodes: EvolutionNode[]; links: EvolutionLink[] };

export type HistciteNode = {
  id?: string | null;
  year?: number | null;
  label?: string | null;
  localCites?: number | null;
};
export type HistciteEdge = { from?: string | null; to?: string | null };
export type HistciteData = { nodes: HistciteNode[]; edges: HistciteEdge[] };

export type ThreeFieldNode = { name?: string | null; layer?: number | null };
export type ThreeFieldLink = { source?: string | null; target?: string | null; value?: number | null };
export type ThreeFieldData = { nodes: ThreeFieldNode[]; links: ThreeFieldLink[] };

/** 宣纸色梯度: 墨 → 金 → 朱砂 (visualMap inRange.color) */
export const PAPER_HEAT_COLORS = ["#efe9dc", "#4a443b", "#b08423", "#c0432b"];

/**
 * 作者年度产出热力图 option。
 * x 轴=年份 (升序)，y 轴=作者 (按数据顺序，已是 top-k 总产出降序)。
 * 每个 cell = [yearIndex, authorIndex, articles]。visualMap 用宣纸色梯度。
 */
export function buildAuthorHeatmapOption(d: AuthorProductionData): EChartsOption {
  const rawYears = d.years.filter((year): year is number => typeof year === "number");
  const years = rawYears.map((y) => String(y));
  const authors = d.authors.map((author) => author?.trim() || "未标注");
  const yearIdx = new Map(rawYears.map((y, i) => [y, i]));
  const authorIdx = new Map(authors.map((a, i) => [a, i]));

  const data: [number, number, number][] = [];
  let maxV = 1;
  for (const c of d.cells) {
    if (typeof c.year !== "number" || typeof c.articles !== "number") continue;
    const author = c.author?.trim() || "未标注";
    const xi = yearIdx.get(c.year);
    const yi = authorIdx.get(author);
    if (xi === undefined || yi === undefined) continue;
    data.push([xi, yi, c.articles]);
    if (c.articles > maxV) maxV = c.articles;
  }

  return {
    grid: { left: 8, right: 16, top: 16, bottom: 56, containLabel: true },
    tooltip: {
      position: "top",
      formatter: (params: unknown) => {
        const p = params as { value: [number, number, number] };
        const [xi, yi, v] = p.value;
        return `${authors[yi]} · ${years[xi]}：${v} 篇`;
      },
    },
    xAxis: {
      type: "category",
      data: years,
      splitArea: { show: true },
      axisLabel: { rotate: years.length > 8 ? 45 : 0 },
    },
    yAxis: { type: "category", data: authors, splitArea: { show: true } },
    visualMap: {
      min: 0,
      max: maxV,
      calculable: true,
      orient: "horizontal",
      left: "center",
      bottom: 0,
      inRange: { color: PAPER_HEAT_COLORS },
      text: ["多", "少"],
    },
    series: [
      {
        name: "年度产出",
        type: "heatmap",
        data,
        label: { show: maxV <= 30 && data.length <= 120 },
        emphasis: { itemStyle: { shadowBlur: 8, shadowColor: "rgba(0,0,0,0.3)" } },
      },
    ],
  };
}

/** 宣纸主色循环 (themeRiver 各 term 配色) */
const RIVER_PALETTE = [
  "#c0432b", "#2f4858", "#b08423", "#4a443b", "#d98b6f",
  "#5b7a8c", "#caa45a", "#7a6f5d", "#9c5640", "#3d5a66",
  "#bf9b54", "#6b6155", "#a8523b", "#48606b", "#c2a062",
];

/** 关键词演变图的可渲染性检查；返回值直接映射到 InsufficientData。 */
export function getKeywordTrendInsufficientData(d: KeywordTrendData): KeywordTrendInsufficientData | null {
  if (d.years.some((year) => typeof year === "number")) return null;
  return {
    reason: "computed_empty",
    message: "关键词历时演变缺少年份数据，无法计算时间轴。",
    howto: "请导入含年份(PY)和关键词(DE)的题录，或纳入更多文献后重试。",
  };
}

/**
 * 关键词历时演变 themeRiver option。
 * data 项形如 [time, value, name]; time 用年份字符串。
 * terms 已按全局总频次降序; 取调色板循环。
 */
export function buildKeywordRiverOption(d: KeywordTrendData): EChartsOption {
  if (getKeywordTrendInsufficientData(d)) {
    return { series: [] };
  }
  const years = d.years.filter((year): year is number => typeof year === "number");
  const terms = d.terms.map((term) => term?.trim() || "未标注");
  const data: [string, number, string][] = d.cells
    .filter((cell): cell is { year: number; term?: string | null; freq: number } =>
      typeof cell.year === "number" && typeof cell.freq === "number",
    )
    .map((cell) => [String(cell.year), cell.freq, cell.term?.trim() || "未标注"]);
  const color = terms.map((_, i) => RIVER_PALETTE[i % RIVER_PALETTE.length]);

  return {
    color,
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line", lineStyle: { color: "rgba(0,0,0,0.2)", width: 1 } },
    },
    legend: { data: terms, top: 0, type: "scroll" },
    singleAxis: {
      top: 56,
      bottom: 24,
      axisTick: {},
      axisLabel: {},
      type: "time",
      min: `${Math.min(...years)}-01-01`,
      max: `${Math.max(...years)}-12-31`,
      axisPointer: { animation: true, label: { show: true } },
    },
    series: [
      {
        type: "themeRiver",
        emphasis: { itemStyle: { shadowBlur: 12, shadowColor: "rgba(0,0,0,0.4)" } },
        data,
        label: { show: false },
      },
    ],
  };
}

// ===========================================================================
// A5 主题战略图 (Callon 中心度×密度 四象限散点)
// ===========================================================================

/** 气泡大小: 按 freq 在 [min,max] 线性映射到 [18,60]px (单点时取中值) */
function bubbleSize(freq: number, min: number, max: number): number {
  if (max <= min) return 36;
  return 18 + ((freq - min) / (max - min)) * 42;
}

/**
 * 主题战略图 option: scatter 四象限。
 * x=中心度 centrality (与其它主题关联强度), y=密度 density (主题内部凝聚度),
 * 气泡大小=freq; 以中心度/密度中位线划四象限 + 象限标注:
 *   右上 驱动主题 / 右下 基础主题 / 左上 小众主题 / 左下 新兴或衰退主题。
 */
export function buildThematicScatterOption(d: ThematicData): EChartsOption {
  const cl = d.clusters.map((cluster) => ({
    label: cluster.label?.trim() || "未标注",
    centrality: cluster.centrality ?? 0,
    density: cluster.density ?? 0,
    freq: cluster.freq ?? 0,
  }));
  const xs = cl.map((c) => c.centrality);
  const ys = cl.map((c) => c.density);
  // 参考线取中位数 (Callon 习惯); 单点时退化为该点值
  const median = (arr: number[]): number => {
    if (!arr.length) return 0;
    const s = [...arr].sort((a, b) => a - b);
    const mid = Math.floor(s.length / 2);
    return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
  };
  const xMid = median(xs);
  const yMid = median(ys);
  const fMin = Math.min(...cl.map((c) => c.freq), 0);
  const fMax = Math.max(...cl.map((c) => c.freq), 1);

  const data = cl.map((c) => ({
    name: c.label,
    value: [c.centrality, c.density, c.freq] as [number, number, number],
    symbolSize: bubbleSize(c.freq, fMin, fMax),
  }));

  const xMax = Math.max(...xs, xMid) || 1;
  const yMax = Math.max(...ys, yMid) || 1;

  return {
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    tooltip: {
      trigger: "item",
      formatter: (params: unknown) => {
        const p = params as { name: string; value: [number, number, number] };
        const [x, y, f] = p.value;
        return `${p.name}<br/>中心度 ${x}<br/>密度 ${y}<br/>频次 ${f}`;
      },
    },
    xAxis: { type: "value", name: "中心度", nameLocation: "middle", nameGap: 26, min: 0 },
    yAxis: { type: "value", name: "密度", nameLocation: "middle", nameGap: 32, min: 0 },
    series: [
      {
        type: "scatter",
        data,
        itemStyle: { color: "#c0432b", opacity: 0.7, borderColor: "#9c3622" },
        label: { show: true, position: "top", fontSize: 11, color: "#4a443b" },
        // 四象限参考线 (中位线)
        markLine: {
          silent: true,
          symbol: "none",
          lineStyle: { color: "#b0a48b", type: "dashed", width: 1 },
          label: { show: false },
          data: [{ xAxis: xMid }, { yAxis: yMid }],
        },
        // 四象限标注 (置于各象限中点)
        markPoint: {
          symbol: "rect",
          symbolSize: 0,
          label: {
            show: true,
            color: "#9a8f78",
            fontSize: 12,
            fontWeight: "bold",
          },
          data: [
            { name: "驱动主题", coord: [(xMid + xMax) / 2, (yMid + yMax) / 2], value: "驱动主题" },
            { name: "基础主题", coord: [(xMid + xMax) / 2, yMid / 2], value: "基础主题" },
            { name: "小众主题", coord: [xMid / 2, (yMid + yMax) / 2], value: "小众主题" },
            { name: "新兴或衰退主题", coord: [xMid / 2, yMid / 2], value: "新兴或衰退主题" },
          ],
        },
      },
    ],
  };
}

// ===========================================================================
// A5 主题演进 (多周期主题流 / Sankey, 节点按 period 分层)
// ===========================================================================

/** 宣纸主色循环 (sankey 节点配色) */
const SANKEY_PALETTE = [
  "#c0432b", "#2f4858", "#b08423", "#4a443b", "#d98b6f",
  "#5b7a8c", "#caa45a", "#7a6f5d", "#9c5640", "#3d5a66",
];

/**
 * 主题演进 sankey option。节点 name 用 "period|theme" 唯一键 (跨周期同名主题分属不同节点),
 * label 显主题词; links 用节点唯一键连。ECharts sankey 自动按层布局 (period 决定深度)。
 */
export function buildEvolutionSankeyOption(d: EvolutionData): EChartsOption {
  const validNodes = d.nodes.filter(
    (node): node is { name?: string | null; period?: string | null; id: number } =>
      typeof node.id === "number",
  );
  // id → 节点 (links 用 id 引用)
  const byId = new Map(validNodes.map((n) => [n.id, n]));
  // 唯一 key: period + name + id (防同周期同名主题碰撞)
  const keyOf = (n: { name?: string | null; period?: string | null; id: number }) =>
    `${n.period?.trim() || "未标注"}｜${n.name?.trim() || "未标注"}｜${n.id}`;
  const periods = Array.from(new Set(validNodes.map((n) => n.period?.trim() || "未标注"))).sort();
  const periodColor = new Map(periods.map((p, i) => [p, SANKEY_PALETTE[i % SANKEY_PALETTE.length]]));

  const nodes = validNodes.map((n) => ({
    name: keyOf(n),
    // sankey label 显主题词 (不显内部 key)
    label: { formatter: () => n.name?.trim() || "未标注" },
    itemStyle: { color: periodColor.get(n.period?.trim() || "未标注") },
  }));
  // 防御：节点 name 集合，过滤悬空边（ECharts sankey 对引用不存在节点的边会抛异常）。
  // 这里 link 已先经 byId 解析丢弃无效 id，再用 name Set 二次确认 source/target 落在节点上。
  const nodeNames = new Set(nodes.map((n) => n.name));
  const links = d.links
    .map((l) => {
      if (typeof l.source !== "number" || typeof l.target !== "number") return null;
      const s = byId.get(l.source);
      const t = byId.get(l.target);
      if (!s || !t) return null;
      return typeof l.value === "number"
        ? { source: keyOf(s), target: keyOf(t), value: l.value }
        : null;
    })
    .filter((x): x is { source: string; target: string; value: number } =>
      x !== null &&
      nodeNames.has(x.source) &&
      nodeNames.has(x.target) &&
      x.source !== x.target && // 自环：sankey 不支持，会抛错
      Number.isFinite(x.value) &&
      x.value > 0 // 非有限/非正权重会让 sankey 布局异常 (codex P2)
    );

  return {
    tooltip: {
      trigger: "item",
      triggerOn: "mousemove",
      formatter: (params: unknown) => {
        const p = params as { dataType: string; name?: string; data?: { source?: string; target?: string; value?: number } };
        if (p.dataType === "edge" && p.data) {
          const sName = (p.data.source ?? "").split("｜")[1] ?? p.data.source;
          const tName = (p.data.target ?? "").split("｜")[1] ?? p.data.target;
          return `${sName} → ${tName}：${p.data.value}`;
        }
        return (p.name ?? "").split("｜")[1] ?? p.name ?? "";
      },
    },
    series: [
      {
        type: "sankey",
        data: nodes,
        links,
        emphasis: { focus: "adjacency" },
        nodeAlign: "left",
        lineStyle: { color: "gradient", opacity: 0.45, curveness: 0.5 },
        label: { color: "#4a443b", fontSize: 11 },
      },
    ],
  };
}

// ===========================================================================
// A5 历史引文 (graph, y=年份时序分层; 边为引用关系)
// ===========================================================================

/**
 * 历史引文 graph option。节点按 year 纵向分层 (y=年份, 早→晚 自上而下),
 * 同年节点横向铺开; 节点大小按 localCites; 边为引用关系 (引用方→被引方)。
 * 用固定坐标布局 (layout='none' + 计算 x/y), 保证年份时序可读。
 */
export function buildHistciteGraphOption(d: HistciteData): EChartsOption {
  const nodes = d.nodes.map((node, index) => ({
    id: node.id?.trim() || `unknown-${index}`,
    year: node.year,
    label: node.label?.trim() || "未标注",
    localCites: node.localCites ?? 0,
  }));
  // 年份分组 → 计算每个节点的 (x,y); 无年份的归入最大年份+1 "未知"层
  const yrs = nodes.map((n) => (n.year == null ? Number.NaN : n.year));
  const validYrs = yrs.filter((y) => !Number.isNaN(y));
  const minYr = validYrs.length ? Math.min(...validYrs) : 0;
  const maxYr = validYrs.length ? Math.max(...validYrs) : 0;
  const span = Math.max(maxYr - minYr, 1);

  // 同年节点的横向序号 (用于 x 偏移)
  const yearCount = new Map<number, number>();
  const lcsMax = Math.max(...nodes.map((n) => n.localCites), 1);

  const gnodes = nodes.map((n) => {
    const yr = n.year == null ? maxYr + 1 : n.year;
    const seq = yearCount.get(yr) ?? 0;
    yearCount.set(yr, seq + 1);
    // y: 早年在上 (y 小); x: 同年依次右移
    const y = ((yr - minYr) / span) * 100;
    const x = (seq % 8) * 14 + (seq >= 8 ? 7 : 0);
    return {
      id: n.id,
      name: n.label,
      x,
      y,
      symbolSize: 12 + (n.localCites / lcsMax) * 28,
      value: n.localCites,
      itemStyle: { color: "#c0432b" },
      label: { show: true, position: "right" as const, fontSize: 10, color: "#4a443b" },
    };
  });

  const nodeIds = new Set(nodes.map((node) => node.id));
  const gedges = d.edges
    .filter(
      (edge): edge is { from: string; to: string } =>
        typeof edge.from === "string" && typeof edge.to === "string" &&
        nodeIds.has(edge.from) && nodeIds.has(edge.to),
    )
    .map((edge) => ({ source: edge.from, target: edge.to }));

  return {
    tooltip: {
      trigger: "item",
      formatter: (params: unknown) => {
        const p = params as { dataType?: string; name?: string; value?: number };
        if (p.dataType === "node") return `${p.name}<br/>本地被引 ${p.value}`;
        return "";
      },
    },
    series: [
      {
        type: "graph",
        layout: "none",
        roam: true,
        data: gnodes,
        edges: gedges,
        edgeSymbol: ["none", "arrow"],
        edgeSymbolSize: 7,
        lineStyle: { color: "#9a8f78", opacity: 0.5, curveness: 0.1 },
        emphasis: { focus: "adjacency", lineStyle: { width: 2 } },
      },
    ],
  };
}

// ===========================================================================
// A5 三字段 Sankey (作者→关键词→来源 三层)
// ===========================================================================

/** 三层配色: 作者朱砂 / 关键词靛蓝 / 来源金 */
const TF_LAYER_COLOR = ["#c0432b", "#2f4858", "#b08423"];

/** 节点 name 带前缀 (A:/K:/S:) 消歧; label 去前缀显原名 */
function tfStripPrefix(name: string): string {
  return name.length > 2 && name[1] === ":" ? name.slice(2) : name;
}

/**
 * 三字段 sankey option。nodes 含 layer (0=作者/1=关键词/2=来源) → 配色;
 * links 用节点全局 name (已含前缀消歧) 连。
 */
export function buildThreeFieldSankeyOption(d: ThreeFieldData): EChartsOption {
  const normalizedNodes = d.nodes.map((node, index) => ({
    name: node.name?.trim() || `未标注-${index + 1}`,
    layer: node.layer ?? -1,
  }));
  const nodes = normalizedNodes.map((n) => ({
    name: n.name,
    label: { formatter: () => tfStripPrefix(n.name) },
    itemStyle: { color: TF_LAYER_COLOR[n.layer] ?? "#7a6f5d" },
  }));
  // 防御：ECharts sankey 对引用了不存在节点的悬空边会抛异常（真实语料常缺字段）。
  // 用节点 name 建 Set，过滤掉 source/target 不在其中的 link。
  const nodeNames = new Set(normalizedNodes.map((n) => n.name));
  const links = d.links
    .filter(
      (l): l is { source: string; target: string; value: number } =>
        typeof l.source === "string" &&
        typeof l.target === "string" &&
        typeof l.value === "number" &&
        nodeNames.has(l.source) &&
        nodeNames.has(l.target) &&
        l.source !== l.target && // 自环：sankey 不支持，会抛错
        Number.isFinite(l.value) &&
        l.value > 0, // 非有限/非正权重会让 sankey 布局异常 (codex P2)
    )
    .map((l) => ({ source: l.source, target: l.target, value: l.value }));

  return {
    tooltip: {
      trigger: "item",
      triggerOn: "mousemove",
      formatter: (params: unknown) => {
        const p = params as { dataType: string; name?: string; data?: { source?: string; target?: string; value?: number } };
        if (p.dataType === "edge" && p.data) {
          return `${tfStripPrefix(p.data.source ?? "")} → ${tfStripPrefix(p.data.target ?? "")}：${p.data.value}`;
        }
        return tfStripPrefix(p.name ?? "");
      },
    },
    series: [
      {
        type: "sankey",
        data: nodes,
        links,
        emphasis: { focus: "adjacency" },
        nodeAlign: "left",
        lineStyle: { color: "gradient", opacity: 0.45, curveness: 0.5 },
        label: { color: "#4a443b", fontSize: 11 },
      },
    ],
  };
}
