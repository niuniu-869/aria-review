/**
 * OverviewPanel — 领域概览（A2 统计概览组）
 *
 * KPI 卡（文献/期刊/作者/篇均被引/年份跨度）+ 年度产出折线面积图（ECharts，宣纸主题）。
 * 纯渲染 useOverview 既有数据，无新后端调用；三态由 ChartCard 承接。
 */
import { useMemo, useRef } from "react";
import type { EChartsOption } from "echarts";
import { useOverview, useThreefield } from "../api/hooks";
import { ChartCard, EChart, ExportMenu, envelopeChartProps } from "./viz";
import type { EChartHandle, Envelope } from "./viz";
import { buildThreeFieldSankeyOption } from "./viz/advancedCharts";
import type { ThreeFieldData } from "./viz/advancedCharts";

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value tnum">{value}</div>
    </div>
  );
}

/** 年度产出折线 + 面积图 option（朱砂面积渐变；hover「年份: N 篇」） */
function buildAnnualOption(data: { year: number; articles: number }[]): EChartsOption {
  return {
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    tooltip: {
      trigger: "axis",
      // 单点格式化：年份: N 篇
      formatter: (params: unknown) => {
        const arr = params as Array<{ axisValue: string; data: number }>;
        const p = arr?.[0];
        return p ? `${p.axisValue}: ${p.data} 篇` : "";
      },
    },
    xAxis: {
      type: "category",
      data: data.map((p) => String(p.year)),
      boundaryGap: false,
    },
    yAxis: { type: "value", minInterval: 1, name: "篇数" },
    series: [
      {
        name: "年度产出",
        type: "line",
        smooth: true,
        showSymbol: data.length <= 30,
        data: data.map((p) => p.articles),
        // 朱砂线 + 自上而下的朱砂面积渐变
        lineStyle: { color: "#c0432b", width: 2 },
        itemStyle: { color: "#c0432b" },
        areaStyle: {
          color: {
            type: "linear",
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: "rgba(192,67,43,0.35)" },
              { offset: 1, color: "rgba(192,67,43,0.02)" },
            ],
          },
        },
      },
    ],
  };
}

export function OverviewPanel({
  projectId,
  corpusId,
}: {
  projectId: string;
  corpusId: string;
}) {
  const { data, isLoading, isError, error } = useOverview(projectId, corpusId, true);
  const chartRef = useRef<EChartHandle>(null);

  const annual = data?.annualProduction ?? [];
  const option = useMemo(() => buildAnnualOption(annual), [annual]);

  // A5: 三字段 Sankey（作者→关键词→来源，信封）
  const tfRef = useRef<EChartHandle>(null);
  const tfQ = useThreefield(projectId, corpusId);
  const tf = tfQ.data as Envelope<ThreeFieldData> | undefined;
  const tfProps = envelopeChartProps<ThreeFieldData>({
    isLoading: tfQ.isLoading,
    isError: tfQ.isError,
    error: tfQ.error,
    data: tf,
  });
  const tfData = tf && tf.available ? tf.data : undefined;
  const tfOption = useMemo<EChartsOption>(
    () => (tfData ? buildThreeFieldSankeyOption(tfData) : {}),
    [tfData],
  );

  const s = data?.stats;
  return (
    <section>
      <h2>领域概览</h2>

      {/* KPI 卡：仍裸渲染（非图表，无需 ChartCard 三态）；加载/出错时不渲染数字。
          A4: hIndex / annualGrowthRate 为可选增量，缺失（null/undefined）时隐藏对应卡。 */}
      {s && (
        <div className="stat-grid">
          <Stat label="文献数" value={s.documents} />
          <Stat label="期刊数" value={s.sources} />
          <Stat label="作者数" value={s.authors} />
          <Stat label="篇均被引" value={s.avgCitationsPerDoc} />
          <Stat label="年份跨度" value={`${s.timespanFrom}–${s.timespanTo}`} />
          {s.hIndex != null && <Stat label="H 指数" value={s.hIndex} />}
          {s.annualGrowthRate != null && (
            <Stat label="年均增长率" value={`${s.annualGrowthRate}%`} />
          )}
        </div>
      )}

      <ChartCard
        title="年度产出"
        subtitle="逐年发文量趋势"
        loading={isLoading}
        error={isError ? error : undefined}
        empty={!isLoading && !isError && annual.length === 0 ? <p className="muted">暂无年度产出数据</p> : undefined}
        actions={
          annual.length > 0 && !isLoading && !isError ? (
            <ExportMenu
              filename="年度产出"
              target={{
                kind: "echart",
                getHandle: () => chartRef.current,
                csv: {
                  columns: [
                    { key: "year", label: "年份" },
                    { key: "articles", label: "篇数" },
                  ],
                  rows: annual,
                },
              }}
            />
          ) : undefined
        }
      >
        <EChart ref={chartRef} option={option} height={300} ariaLabel="年度产出折线面积图" />
      </ChartCard>

      {/* A5: 三字段 Sankey（作者→关键词→来源） */}
      <ChartCard
        title="三字段流向图"
        subtitle="作者 → 关键词 → 来源（Three-Field Sankey）"
        loading={tfProps.loading}
        error={tfProps.error}
        empty={tfProps.empty}
        hint={tfData ? "左列作者、中列关键词、右列来源；连线宽度表示共现强度" : undefined}
        actions={
          tfData ? (
            <ExportMenu
              filename="三字段流向图"
              target={{
                kind: "echart",
                getHandle: () => tfRef.current,
                csv: {
                  columns: [
                    { key: "source", label: "源" },
                    { key: "target", label: "目标" },
                    { key: "value", label: "共现" },
                  ],
                  rows: tfData.links,
                },
              }}
            />
          ) : undefined
        }
      >
        <EChart ref={tfRef} option={tfOption} height={460} ariaLabel="三字段流向图 Sankey" />
      </ChartCard>
    </section>
  );
}
