/**
 * DocumentsPanel — 关键词热点（A3 知识结构组）
 *
 * 1) 真词云：keywords[{term,freq}] → echarts-wordcloud。词大小/颜色按 freq 映射
 *    （宣纸色系：低频墨色 → 中频金 → 高频朱砂）。渲染前 await ensureWordCloud()
 *    惰性注册（jsdom/不支持 canvas 时返回 false → 优雅降级为「词云不可用 + 频次表」）。
 * 2) 高被引文献：topCited → DataTable（标题|作者|年份|被引，被引 sortable 降序默认）。
 *
 * 真实数据现实：PDF 语料常缺关键词(DE) → keywords 空 → InsufficientData(missing_field)。
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { EChartsOption } from "echarts";
import { useCitedRefs, useDocuments, useKeywordTrend } from "../api/hooks";
import type { RCorpusId } from "../api/corpusIds";
import { ensureWordCloud } from "./viz/echartsSetup";
import { ChartCard, DataTable, EChart, ExportMenu, InsufficientData, envelopeChartProps } from "./viz";
import type { DataTableColumn, EChartHandle, Envelope } from "./viz";
import { buildKeywordRiverOption, getKeywordTrendInsufficientData } from "./viz/advancedCharts";
import type { KeywordTrendData } from "./viz/advancedCharts";

type CitedRefItem = { ref: string; count: number };

// ---------------------------------------------------------------------------
// 词云
// ---------------------------------------------------------------------------

type Keyword = { term: string; freq: number };

/** 宣纸三色梯度：低频墨色 → 中频金 → 高频朱砂（t∈[0,1] 为归一化 freq 名次） */
function paperColor(t: number): string {
  if (t >= 0.66) return "#c0432b"; // 朱砂（高频）
  if (t >= 0.33) return "#b08423"; // 金（中频）
  return "#4a443b"; // 墨色（低频）
}

/**
 * 词云 option。echarts-wordcloud 的 series.type='wordCloud'，需先 ensureWordCloud()。
 * sizeRange 控制字号区间；每词颜色按 freq 在区间内的相对名次取宣纸三色。
 */
function buildWordCloudOption(keywords: Keyword[]): EChartsOption {
  const max = Math.max(...keywords.map((k) => k.freq), 1);
  const min = Math.min(...keywords.map((k) => k.freq), 0);
  const span = Math.max(max - min, 1);
  const data = keywords.map((k) => ({
    name: k.term,
    value: k.freq,
    textStyle: { color: paperColor((k.freq - min) / span) },
  }));
  return {
    tooltip: {
      show: true,
      formatter: (p: unknown) => {
        const d = p as { name: string; value: number };
        return `${d.name}：${d.value} 次`;
      },
    },
    series: [
      {
        // echarts-wordcloud 注册后此 type 才有效
        type: "wordCloud" as unknown as "custom",
        shape: "circle",
        sizeRange: [14, 56],
        rotationRange: [-30, 30],
        gridSize: 8,
        drawOutOfBound: false,
        layoutAnimation: true,
        textStyle: { fontFamily: '"Songti SC", "STSong", "SimSun", serif' },
        data,
      },
    ] as unknown as EChartsOption["series"],
  };
}

// ---------------------------------------------------------------------------
// 高被引文献表
// ---------------------------------------------------------------------------

type CitedRow = {
  title?: string | null;
  author?: string | null;
  year?: number | null;
  cited?: number | null;
  [k: string]: unknown;
};

/** 文本 null/空显示兜底。 */
function dash(v: unknown): string {
  return v == null || v === "" ? "未标注" : String(v);
}

/** 缺失数值显示「—」，避免把未知误作 0。 */
function numDash(v: unknown): string {
  return v == null ? "—" : String(v);
}

const citedCols: DataTableColumn<CitedRow>[] = [
  { key: "title", label: "标题", format: (v) => dash(v) },
  { key: "author", label: "作者", format: (v) => dash(v) },
  { key: "year", label: "年份", align: "right", sortable: true, format: (v) => dash(v) },
  { key: "cited", label: "被引", align: "right", sortable: true, format: (v) => numDash(v) },
];

// 关键词频次兜底表（词云不可用时用）
type KeywordRow = { term: string; freq: number; [k: string]: unknown };
const keywordCols: DataTableColumn<KeywordRow>[] = [
  { key: "term", label: "关键词", sortable: true },
  { key: "freq", label: "频次", align: "right", sortable: true },
];

// A4: 高被引参考文献表（参考文献 | 次数）
type CitedRefRow = { ref?: string | null; count?: number | null; [k: string]: unknown };
const citedRefCols: DataTableColumn<CitedRefRow>[] = [
  { key: "ref", label: "参考文献", sortable: true, format: (v) => dash(v) },
  { key: "count", label: "被引次数", align: "right", sortable: true, format: (v) => numDash(v) },
];

// ---------------------------------------------------------------------------
// 面板
// ---------------------------------------------------------------------------

export function DocumentsPanel({ projectId, corpusId }: { projectId: string; corpusId: RCorpusId }) {
  const { data, isLoading, isError, error } = useDocuments(projectId, corpusId);
  const err = isError ? error : undefined;
  const chartRef = useRef<EChartHandle>(null);

  const keywords = useMemo<Keyword[]>(
    () => (data?.keywords ?? []).map((item) => ({
      term: item.term?.trim() || "未标注",
      freq: item.freq ?? 0,
    })),
    [data],
  );
  const hasKeywords = keywords.length > 0;

  // 词云惰性注册状态：null=探测中 / true=可用 / false=不可用（jsdom 等）
  const [wcReady, setWcReady] = useState<boolean | null>(null);
  useEffect(() => {
    if (!hasKeywords) return;
    let alive = true;
    ensureWordCloud()
      .then((ok) => {
        if (alive) setWcReady(ok);
      })
      .catch(() => {
        // 动态导入失败也走降级（频次表），避免卡在「加载中」+ 未处理 rejection
        if (alive) setWcReady(false);
      });
    return () => {
      alive = false;
    };
  }, [hasKeywords]);

  const wcOption = useMemo<EChartsOption>(
    () => (hasKeywords ? buildWordCloudOption(keywords) : {}),
    [hasKeywords, keywords],
  );

  const topCited = (data?.topCited ?? []) as CitedRow[];
  const keywordRows = keywords as KeywordRow[];

  // A4: 关键词历时演变 themeRiver（信封）
  const riverRef = useRef<EChartHandle>(null);
  const trendQ = useKeywordTrend(projectId, corpusId);
  const trend = trendQ.data as Envelope<KeywordTrendData> | undefined;
  const riverProps = envelopeChartProps<KeywordTrendData>({
    isLoading: trendQ.isLoading,
    isError: trendQ.isError,
    error: trendQ.error,
    data: trend,
  });
  const riverData = trend && trend.available ? trend.data : undefined;
  const riverInsufficient = useMemo(
    () => (riverData ? getKeywordTrendInsufficientData(riverData) : null),
    [riverData],
  );
  const riverOption = useMemo<EChartsOption>(
    () => (riverData && !riverInsufficient ? buildKeywordRiverOption(riverData) : {}),
    [riverData, riverInsufficient],
  );

  // A4: 高被引参考文献（信封）
  const refsQ = useCitedRefs(projectId, corpusId);
  const refsEnv = refsQ.data as Envelope<CitedRefItem[]> | undefined;
  const refsProps = envelopeChartProps<CitedRefItem[]>({
    isLoading: refsQ.isLoading,
    isError: refsQ.isError,
    error: refsQ.error,
    data: refsEnv,
  });
  const citedRefRows = (refsEnv && refsEnv.available ? refsEnv.data : []) as CitedRefRow[];

  // ---- 词云卡的内容分支 ----
  let wordCloudBody: React.ReactNode;
  if (!hasKeywords) {
    wordCloudBody = (
      <InsufficientData
        reason="missing_field"
        missingField="关键词(DE)"
        message="当前语料未提供关键词字段。"
        howto="PDF 导入语料常缺关键词，可从 Sciverse/OpenAlex/WoS 导入含关键词的题录。"
      />
    );
  } else if (wcReady === false) {
    // jsdom/不支持 canvas：词云不可用 → 文字提示 + 频次表兜底
    wordCloudBody = (
      <div>
        <p className="muted" style={{ marginTop: 0 }}>词云在当前环境不可用，已改用下方频次表。</p>
        <DataTable
          columns={keywordCols}
          rows={keywordRows}
          initialSort={{ key: "freq", dir: "desc" }}
          emptyText="无关键词数据"
        />
      </div>
    );
  } else if (wcReady === true) {
    wordCloudBody = <EChart ref={chartRef} option={wcOption} height={360} ariaLabel="关键词词云" />;
  } else {
    // 探测中
    wordCloudBody = <p className="muted">词云加载中…</p>;
  }

  return (
    <section>
      <h2>关键词与热点</h2>

      <ChartCard
        title="关键词词云"
        subtitle="词大小/颜色按词频映射（墨→金→朱砂）"
        loading={isLoading}
        error={err}
        actions={
          hasKeywords && wcReady === true && !isLoading && !err ? (
            <ExportMenu
              filename="关键词词云"
              target={{
                kind: "echart",
                getHandle: () => chartRef.current,
                csv: {
                  columns: [
                    { key: "term", label: "关键词" },
                    { key: "freq", label: "频次" },
                  ],
                  rows: keywordRows,
                },
              }}
            />
          ) : undefined
        }
      >
        {wordCloudBody}
      </ChartCard>

      <ChartCard title="高被引文献" subtitle="按被引频次排名" loading={isLoading} error={err}>
        <DataTable
          columns={citedCols}
          rows={topCited}
          initialSort={{ key: "cited", dir: "desc" }}
          emptyText="当前语料无高被引文献数据"
        />
      </ChartCard>

      {/* A4: 关键词历时演变 themeRiver（信封） */}
      <ChartCard
        title="关键词历时演变"
        subtitle="主题热度逐年流变（themeRiver）"
        loading={riverProps.loading}
        error={riverProps.error}
        empty={
          riverInsufficient ? (
            <InsufficientData
              reason={riverInsufficient.reason}
              message={riverInsufficient.message}
              howto={riverInsufficient.howto}
            />
          ) : (
            riverProps.empty
          )
        }
        hint={riverData && !riverInsufficient ? "每条色带为一个高频关键词，宽度表示该年频次" : undefined}
        actions={
          riverData && !riverInsufficient ? (
            <ExportMenu
              filename="关键词历时演变"
              target={{
                kind: "echart",
                getHandle: () => riverRef.current,
                csv: {
                  columns: [
                    { key: "year", label: "年份" },
                    { key: "term", label: "关键词" },
                    { key: "freq", label: "频次" },
                  ],
                  rows: riverData.cells,
                },
              }}
            />
          ) : undefined
        }
      >
        <EChart ref={riverRef} option={riverOption} height={380} ariaLabel="关键词历时演变流图" />
      </ChartCard>

      {/* A4: 高被引参考文献（信封）— 表格形态：CSV-only 导出（无图像）*/}
      <ChartCard
        title="高被引参考文献"
        subtitle="语料内被引最多的参考文献"
        loading={refsProps.loading}
        error={refsProps.error}
        empty={refsProps.empty}
        actions={
          citedRefRows.length > 0 ? (
            <ExportMenu
              filename="高被引参考文献"
              target={{
                kind: "table",
                csv: {
                  columns: [
                    { key: "ref", label: "参考文献" },
                    { key: "count", label: "被引次数" },
                  ],
                  rows: citedRefRows,
                },
              }}
            />
          ) : undefined
        }
      >
        <DataTable
          columns={citedRefCols}
          rows={citedRefRows}
          initialSort={{ key: "count", dir: "desc" }}
          emptyText="当前语料无高被引参考文献数据"
        />
      </ChartCard>
    </section>
  );
}
