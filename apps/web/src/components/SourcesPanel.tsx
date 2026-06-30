/**
 * SourcesPanel — 核心期刊（A2 统计概览组）
 *
 * 3 张 DataTable（最相关来源 / 来源 H 指数 / Bradford 分区），各包一个 ChartCard。
 * 纯渲染 useSources 既有数据；PDF 语料这三表常为空 → DataTable emptyText 优雅空态。
 * 注：H 指数的 g/m/被引列、Bradford 完整排名/累计% 为 A4 后端补，A2 仅渲染现有字段。
 */
import { useSources } from "../api/hooks";
import { ChartCard, DataTable } from "./viz";
import type { DataTableColumn } from "./viz";

// DataTable 要求 T extends Record<string, unknown>；本地行类型加索引签名以满足约束（结构同 schema）
type SourceRow = { source: string; articles: number; [k: string]: unknown };
// A4: hIndex 行加 g/m/tc 可选列
type HRow = {
  source: string;
  h: number;
  g?: number | null;
  m?: number | null;
  tc?: number | null;
  [k: string]: unknown;
};
// A4: bradford 行加 rank/cumPct 可选列
type BradfordRow = {
  source: string;
  zone: string;
  freq: number;
  rank?: number | null;
  cumPct?: number | null;
  [k: string]: unknown;
};

/** 缺失/null 显示「—」（不显示 0，避免误导） */
function numDash(v: unknown): string {
  return v == null ? "—" : String(v);
}

/** 是否核心区（Zone 1 / Core / 核心 等多写法兼容） */
function isCoreZone(zone: string): boolean {
  const z = zone.trim().toLowerCase();
  return z === "zone 1" || z === "core" || z.includes("核心") || z === "1";
}

const topCols: DataTableColumn<SourceRow>[] = [
  { key: "source", label: "来源", sortable: true },
  { key: "articles", label: "论文数", align: "right", sortable: true },
];

const hCols: DataTableColumn<HRow>[] = [
  { key: "source", label: "来源", sortable: true },
  { key: "h", label: "H 指数", align: "right", sortable: true },
  // A4 g/m/tc：缺失显示「—」
  { key: "g", label: "g 指数", align: "right", sortable: true, format: (v) => numDash(v) },
  { key: "m", label: "m 指数", align: "right", sortable: true, format: (v) => numDash(v) },
  { key: "tc", label: "被引总数", align: "right", sortable: true, format: (v) => numDash(v) },
];

const bradfordCols: DataTableColumn<BradfordRow>[] = [
  // A4 rank：行号
  { key: "rank", label: "排名", align: "right", sortable: true, format: (v) => numDash(v) },
  { key: "source", label: "来源", sortable: true },
  {
    // 核心区分区名加高亮标记
    key: "zone",
    label: "分区",
    format: (v) =>
      isCoreZone(String(v)) ? (
        <span className="bradford-core">{String(v)}</span>
      ) : (
        String(v)
      ),
  },
  { key: "freq", label: "频率", align: "right", sortable: true },
  // A4 cumPct：累计频次百分比
  { key: "cumPct", label: "累计%", align: "right", sortable: true, format: (v) => (v == null ? "—" : `${v}%`) },
];

export function SourcesPanel({ projectId, corpusId }: { projectId: string; corpusId: string }) {
  const { data, isLoading, isError, error } = useSources(projectId, corpusId);
  const err = isError ? error : undefined;

  const topRows = (data?.topSources ?? []) as SourceRow[];
  const hRows = (data?.hIndex ?? []) as HRow[];
  const bradfordRows = (data?.bradford ?? []) as BradfordRow[];

  return (
    <section>
      <h2>核心期刊</h2>

      <ChartCard title="最相关来源" subtitle="按论文数排名" loading={isLoading} error={err}>
        <DataTable
          columns={topCols}
          rows={topRows}
          initialSort={{ key: "articles", dir: "desc" }}
          emptyText="当前语料无期刊/来源字段数据"
        />
      </ChartCard>

      <ChartCard title="来源 H 指数" subtitle="期刊学术影响力" loading={isLoading} error={err}>
        <DataTable
          columns={hCols}
          rows={hRows}
          initialSort={{ key: "h", dir: "desc" }}
          emptyText="当前语料无期刊/来源字段数据"
        />
      </ChartCard>

      <ChartCard
        title="Bradford 分区"
        subtitle="文献离散定律分区（核心区高亮）"
        loading={isLoading}
        error={err}
        hint={bradfordRows.length > 0 ? "排名按频率降序；累计% 为累计频次占比；核心区行高亮" : undefined}
      >
        <DataTable
          columns={bradfordCols}
          rows={bradfordRows}
          initialSort={{ key: "rank", dir: "asc" }}
          rowClassName={(row) => (isCoreZone(String(row.zone)) ? "row-core" : undefined)}
          emptyText="当前语料无期刊/来源字段数据"
        />
      </ChartCard>
    </section>
  );
}
