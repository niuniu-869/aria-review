/**
 * ConceptualPanel — 主题地图（A3 知识结构组 + A5 高级图②）
 *
 * 1) 关键词共现网络：NetworkCard（vis-network + Top-N 滑块切片 + 导出 PNG/CSV/JSON）。
 * 2) A5 主题战略图：Callon 中心度×密度 四象限散点（信封消费）。
 * 3) A5 主题演进图：多周期主题流 sankey（信封消费）。
 *
 * 纯渲染既有数据；PDF 语料常缺 DE/PY → 信封 missing_field/not_enough_data 诚实空态。
 */
import { useMemo, useRef } from "react";
import type { EChartsOption } from "echarts";
import { useConceptual, useEvolution, useThematic } from "../api/hooks";
import { ChartCard, EChart, ExportMenu, NetworkCard, envelopeChartProps } from "./viz";
import type { EChartHandle, Envelope } from "./viz";
import {
  buildEvolutionSankeyOption,
  buildThematicScatterOption,
} from "./viz/advancedCharts";
import type { EvolutionData, ThematicData } from "./viz/advancedCharts";

export function ConceptualPanel({ projectId, corpusId }: { projectId: string; corpusId: string }) {
  const { data, isLoading, isError, error } = useConceptual(projectId, corpusId);
  const graph = data?.graph ?? { nodes: [], edges: [] };

  // A5: 主题战略图（信封）
  const thematicRef = useRef<EChartHandle>(null);
  const thematicQ = useThematic(projectId, corpusId);
  const thematic = thematicQ.data as Envelope<ThematicData> | undefined;
  const thematicProps = envelopeChartProps<ThematicData>({
    isLoading: thematicQ.isLoading,
    isError: thematicQ.isError,
    error: thematicQ.error,
    data: thematic,
  });
  const thematicData = thematic && thematic.available ? thematic.data : undefined;
  const thematicOption = useMemo<EChartsOption>(
    () => (thematicData ? buildThematicScatterOption(thematicData) : {}),
    [thematicData],
  );

  // A5: 主题演进图（信封）
  const evoRef = useRef<EChartHandle>(null);
  const evoQ = useEvolution(projectId, corpusId);
  const evo = evoQ.data as Envelope<EvolutionData> | undefined;
  const evoProps = envelopeChartProps<EvolutionData>({
    isLoading: evoQ.isLoading,
    isError: evoQ.isError,
    error: evoQ.error,
    data: evo,
  });
  const evoData = evo && evo.available ? evo.data : undefined;
  const evoOption = useMemo<EChartsOption>(
    () => (evoData ? buildEvolutionSankeyOption(evoData) : {}),
    [evoData],
  );

  return (
    <section>
      <h2>研究主题地图 (关键词共现)</h2>
      <NetworkCard
        title="关键词共现网络"
        subtitle="共词聚类构成的概念图谱（Thematic Map）"
        graph={graph}
        loading={isLoading}
        error={isError ? error : undefined}
        filename="关键词共现网络"
      />

      {/* A5: 主题战略图（Callon 四象限散点） */}
      <ChartCard
        title="主题战略图"
        subtitle="中心度×密度 四象限（气泡大小=频次）"
        loading={thematicProps.loading}
        error={thematicProps.error}
        empty={thematicProps.empty}
        hint={
          thematicData
            ? "右上=驱动主题，右下=基础主题，左上=小众主题，左下=新兴或衰退主题"
            : undefined
        }
        actions={
          thematicData ? (
            <ExportMenu
              filename="主题战略图"
              target={{
                kind: "echart",
                getHandle: () => thematicRef.current,
                csv: {
                  columns: [
                    { key: "label", label: "主题" },
                    { key: "centrality", label: "中心度" },
                    { key: "density", label: "密度" },
                    { key: "freq", label: "频次" },
                  ],
                  rows: thematicData.clusters,
                },
              }}
            />
          ) : undefined
        }
      >
        <EChart ref={thematicRef} option={thematicOption} height={420} ariaLabel="主题战略图 Callon 四象限散点" />
      </ChartCard>

      {/* A5: 主题演进图（多周期主题流 sankey） */}
      <ChartCard
        title="主题演进图"
        subtitle="跨时间周期的主题流转（Sankey）"
        loading={evoProps.loading}
        error={evoProps.error}
        empty={evoProps.empty}
        hint={evoData ? "每列为一个时间周期，连线宽度表示主题间的流转强度" : undefined}
        actions={
          evoData ? (
            <ExportMenu
              filename="主题演进图"
              target={{
                kind: "echart",
                getHandle: () => evoRef.current,
                csv: {
                  columns: [
                    { key: "name", label: "主题" },
                    { key: "period", label: "周期" },
                  ],
                  rows: evoData.nodes,
                },
              }}
            />
          ) : undefined
        }
      >
        <EChart ref={evoRef} option={evoOption} height={420} ariaLabel="主题演进图 Sankey" />
      </ChartCard>
    </section>
  );
}
