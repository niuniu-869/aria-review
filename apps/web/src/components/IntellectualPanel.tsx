/**
 * IntellectualPanel — 知识脉络（A3 知识结构组 + A5 高级图②）
 *
 * 1) 参考文献共被引网络：NetworkCard（vis-network + Top-N 滑块切片 + 导出 PNG/CSV/JSON）。
 * 2) A5 历史引文图：时序分层引用脉络 graph（信封消费；y=年份分层，边为引用关系）。
 *
 * 纯渲染既有数据；PDF 语料常缺 CR → 信封 missing_field/not_enough_data 诚实空态。
 */
import { useMemo, useRef } from "react";
import type { EChartsOption } from "echarts";
import { useHistcite, useIntellectual } from "../api/hooks";
import { ChartCard, EChart, ExportMenu, NetworkCard, envelopeChartProps } from "./viz";
import type { EChartHandle, Envelope } from "./viz";
import { buildHistciteGraphOption } from "./viz/advancedCharts";
import type { HistciteData } from "./viz/advancedCharts";

export function IntellectualPanel({ projectId, corpusId }: { projectId: string; corpusId: string }) {
  const { data, isLoading, isError, error } = useIntellectual(projectId, corpusId);
  const graph = data?.graph ?? { nodes: [], edges: [] };

  // A5: 历史引文图（信封）
  const histRef = useRef<EChartHandle>(null);
  const histQ = useHistcite(projectId, corpusId);
  const hist = histQ.data as Envelope<HistciteData> | undefined;
  const histProps = envelopeChartProps<HistciteData>({
    isLoading: histQ.isLoading,
    isError: histQ.isError,
    error: histQ.error,
    data: hist,
  });
  const histData = hist && hist.available ? hist.data : undefined;
  const histOption = useMemo<EChartsOption>(
    () => (histData ? buildHistciteGraphOption(histData) : {}),
    [histData],
  );

  return (
    <section>
      <h2>学科知识脉络 (参考文献共被引)</h2>
      <NetworkCard
        title="共被引网络"
        subtitle="引文耦合揭示的学科知识结构"
        graph={graph}
        loading={isLoading}
        error={isError ? error : undefined}
        filename="共被引网络"
      />

      {/* A5: 历史引文图（时序分层引用脉络） */}
      <ChartCard
        title="历史引文图"
        subtitle="时序分层的引用脉络（节点=作者+年，越大本地被引越多）"
        loading={histProps.loading}
        error={histProps.error}
        empty={histProps.empty}
        hint={histData ? "纵轴为年份（早→晚 自上而下），箭头由引用方指向被引方" : undefined}
        actions={
          histData ? (
            <ExportMenu
              filename="历史引文图"
              target={{
                kind: "echart",
                getHandle: () => histRef.current,
                csv: {
                  columns: [
                    { key: "id", label: "节点ID" },
                    { key: "label", label: "文献" },
                    { key: "year", label: "年份" },
                    { key: "localCites", label: "本地被引" },
                  ],
                  rows: histData.nodes,
                },
              }}
            />
          ) : undefined
        }
      >
        <EChart ref={histRef} option={histOption} height={460} ariaLabel="历史引文图 时序引用脉络" />
      </ChartCard>
    </section>
  );
}
