/**
 * AuthorsPanel — 核心作者（A2 统计概览组）
 *
 * 高产作者 / 作者 H 指数 → DataTable；Lotka 定律 → ECharts 散点 + 理论拟合曲线。
 * 纯渲染 useAuthors 既有数据；hIndex 常空 → emptyText；lotka 缺失/空 → ChartCard empty。
 */
import { useMemo, useRef } from "react";
import type { EChartsOption } from "echarts";
import { useAuthorProduction, useAuthors } from "../api/hooks";
import type { RCorpusId } from "../api/corpusIds";
import { ChartCard, DataTable, EChart, ExportMenu, envelopeChartProps } from "./viz";
import type { DataTableColumn, EChartHandle, Envelope } from "./viz";
import { buildAuthorHeatmapOption } from "./viz/advancedCharts";
import type { AuthorProductionData } from "./viz/advancedCharts";

// DataTable 约束：本地行类型加索引签名（结构同 schema）
type AuthorRow = { author?: string | null; articles?: number | null; [k: string]: unknown };
// A4: hIndex 行加 g/m/tc 可选列
type HRow = {
  author?: string | null;
  h?: number | null;
  g?: number | null;
  m?: number | null;
  tc?: number | null;
  [k: string]: unknown;
};

const topCols: DataTableColumn<AuthorRow>[] = [
  { key: "author", label: "作者", sortable: true, format: (v) => textFallback(v) },
  { key: "articles", label: "论文数", align: "right", sortable: true, format: (v) => numDash(v) },
];

/** 缺失/null 显示「—」（不显示 0，避免误导；m=null 同理） */
function numDash(v: unknown): string {
  return v == null ? "—" : String(v);
}

/** R 文本缺失时统一显示兜底，避免空白单元格。 */
function textFallback(v: unknown): string {
  return typeof v === "string" && v.trim() ? v : "未标注";
}

const hCols: DataTableColumn<HRow>[] = [
  { key: "author", label: "作者", sortable: true, format: (v) => textFallback(v) },
  { key: "h", label: "H 指数", align: "right", sortable: true, format: (v) => numDash(v) },
  // A4 g/m/tc：缺失显示「—」
  { key: "g", label: "g 指数", align: "right", sortable: true, format: (v) => numDash(v) },
  { key: "m", label: "m 指数", align: "right", sortable: true, format: (v) => numDash(v) },
  { key: "tc", label: "被引总数", align: "right", sortable: true, format: (v) => numDash(v) },
];

type LotkaPoint = { articles: number; authors: number };

/** distribution 是否存在 articles=1 的有效锚点（理论曲线归一基准）。 */
function hasLotkaAnchor(dist: LotkaPoint[]): boolean {
  const at1 = dist.find((p) => p.articles === 1)?.authors;
  return typeof at1 === "number" && at1 > 0;
}

/**
 * Lotka 定律图 option：观测散点 + 理论拟合曲线 f(x)=C/x^β。
 * C 严格用 articles=1 处的 authors 数归一（Lotka 定律基准）。
 * 无 articles=1 锚点时仅画观测散点、不画理论曲线（避免归一含义不一致）。
 * 调用方须保证 beta>0 且所有 articles>0（见 lotkaReady），故无除零/NaN。
 */
function buildLotkaOption(beta: number, dist: LotkaPoint[]): EChartsOption {
  // 按发文数升序，保证理论曲线连贯
  const sorted = [...dist].sort((a, b) => a.articles - b.articles);
  const at1 = sorted.find((p) => p.articles === 1)?.authors;
  const hasTheory = typeof at1 === "number" && at1 > 0;

  const observed = sorted.map((p) => [p.articles, p.authors]);
  const legendData = ["观测"];
  const series: NonNullable<EChartsOption["series"]> = [
    {
      name: "观测",
      type: "scatter",
      data: observed,
      symbolSize: 9,
      itemStyle: { color: "#c0432b" },
    },
  ];

  if (hasTheory) {
    const C = at1 as number;
    // 理论曲线在每个观测 x 处求值：C / x^β（articles>0 已由 lotkaReady 保证）
    const theory = sorted.map((p) => [p.articles, C / Math.pow(p.articles, beta)]);
    legendData.push(`理论 (β=${beta})`);
    series.push({
      name: `理论 (β=${beta})`,
      type: "line",
      data: theory,
      smooth: true,
      showSymbol: false,
      lineStyle: { color: "#2f4858", width: 2, type: "dashed" },
      itemStyle: { color: "#2f4858" },
    });
  }

  return {
    grid: { left: 8, right: 16, top: 32, bottom: 8, containLabel: true },
    legend: { data: legendData, top: 0 },
    tooltip: {
      trigger: "item",
      formatter: (params: unknown) => {
        const p = params as { seriesName: string; value: [number, number] };
        const [x, y] = p.value;
        return `${p.seriesName}<br/>发文数 ${x}：${Math.round(y)} 位作者`;
      },
    },
    xAxis: { type: "value", name: "发文数", minInterval: 1 },
    yAxis: { type: "value", name: "作者数" },
    series,
  };
}

export function AuthorsPanel({ projectId, corpusId }: { projectId: string; corpusId: RCorpusId }) {
  const { data, isLoading, isError, error } = useAuthors(projectId, corpusId);
  const err = isError ? error : undefined;
  const chartRef = useRef<EChartHandle>(null);
  const heatRef = useRef<EChartHandle>(null);

  const topRows = (data?.topAuthors ?? []) as AuthorRow[];
  const hRows = (data?.hIndex ?? []) as HRow[];

  // A4: 作者年度产出时间线 (信封)
  const prodQ = useAuthorProduction(projectId, corpusId);
  const prod = prodQ.data as Envelope<AuthorProductionData> | undefined;
  const heatProps = envelopeChartProps<AuthorProductionData>({
    isLoading: prodQ.isLoading,
    isError: prodQ.isError,
    error: prodQ.error,
    data: prod,
  });
  const heatData = prod && prod.available ? prod.data : undefined;
  const heatOption = useMemo<EChartsOption>(
    () => (heatData ? buildAuthorHeatmapOption(heatData) : {}),
    [heatData],
  );

  const beta = data?.lotka?.beta;
  const dist = useMemo<LotkaPoint[]>(
    () => (data?.lotka?.distribution ?? []).filter(
      (p): p is LotkaPoint => typeof p.articles === "number" && typeof p.authors === "number",
    ),
    [data],
  );
  // 有效性：beta 为正数 + distribution 非空 + 所有 articles>0（防 C/x^β 出 Infinity/NaN）
  const lotkaReady =
    typeof beta === "number" && beta > 0 && dist.length > 0 && dist.every((p) => p.articles > 0);
  const lotkaHasTheory = lotkaReady && hasLotkaAnchor(dist);
  const lotkaOption = useMemo<EChartsOption>(
    () => (lotkaReady ? buildLotkaOption(beta as number, dist) : {}),
    [lotkaReady, beta, dist],
  );

  return (
    <section>
      <h2>核心作者</h2>

      <ChartCard title="高产作者" subtitle="按发文数排名" loading={isLoading} error={err}>
        <DataTable
          columns={topCols}
          rows={topRows}
          initialSort={{ key: "articles", dir: "desc" }}
          emptyText="当前语料无作者数据"
        />
      </ChartCard>

      <ChartCard title="作者 H 指数" subtitle="作者学术影响力" loading={isLoading} error={err}>
        <DataTable
          columns={hCols}
          rows={hRows}
          initialSort={{ key: "h", dir: "desc" }}
          emptyText="当前语料无作者 H 指数数据"
        />
      </ChartCard>

      <ChartCard
        title="Lotka 定律"
        subtitle="作者发文分布与理论拟合"
        loading={isLoading}
        error={err}
        empty={!isLoading && !err && !lotkaReady ? <p className="muted">暂无 Lotka 分布数据</p> : undefined}
        hint={
          lotkaReady
            ? lotkaHasTheory
              ? `观测点为各发文数对应的作者数；虚线为 Lotka 定律理论曲线 f(x)=C/x^β（β=${beta}）`
              : "观测点为各发文数对应的作者数（无 articles=1 锚点，未绘理论曲线）"
            : undefined
        }
        actions={
          lotkaReady && !isLoading && !err ? (
            <ExportMenu
              filename="Lotka定律"
              target={{
                kind: "echart",
                getHandle: () => chartRef.current,
                csv: {
                  columns: [
                    { key: "articles", label: "发文数" },
                    { key: "authors", label: "作者数" },
                  ],
                  rows: dist,
                },
              }}
            />
          ) : undefined
        }
      >
        <EChart ref={chartRef} option={lotkaOption} height={320} ariaLabel="Lotka 定律散点与理论曲线" />
      </ChartCard>

      {/* A4: 作者年度产出时间线热力图（作者 × 年份），消费可用性信封 */}
      <ChartCard
        title="作者年度产出时间线"
        subtitle="作者 × 年份发文热力图（墨→金→朱砂）"
        loading={heatProps.loading}
        error={heatProps.error}
        empty={heatProps.empty}
        hint={heatData ? "颜色越深/越朱砂表示该作者该年发文越多" : undefined}
        actions={
          heatData ? (
            <ExportMenu
              filename="作者年度产出"
              target={{
                kind: "echart",
                getHandle: () => heatRef.current,
                csv: {
                  columns: [
                    { key: "author", label: "作者" },
                    { key: "year", label: "年份" },
                    { key: "articles", label: "发文数" },
                  ],
                  rows: heatData.cells,
                },
              }}
            />
          ) : undefined
        }
      >
        <EChart ref={heatRef} option={heatOption} height={Math.max(280, (heatData?.authors.length ?? 0) * 32 + 80)} ariaLabel="作者年度产出热力图" />
      </ChartCard>
    </section>
  );
}
