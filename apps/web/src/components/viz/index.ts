/**
 * viz/index.ts — 共享可视化原语统一出口
 *
 * 面板从 "components/viz" 一处 import 即可拿到全部原语 + 类型。
 */
export { EChart } from "./EChart";
export type { EChartHandle, EChartProps, EChartEvents } from "./EChart";

export { ChartCard } from "./ChartCard";
export type { ChartCardProps } from "./ChartCard";

export { DataTable } from "./DataTable";
export type { DataTableColumn, DataTableProps } from "./DataTable";

export { NodeCountSlider } from "./NodeCountSlider";
export type { NodeCountSliderProps } from "./NodeCountSlider";

export { ExportMenu, timestamp } from "./ExportMenu";
export type { ExportMenuProps, ExportTarget, CsvColumn } from "./ExportMenu";

export { InsufficientData } from "./InsufficientData";
export type {
  InsufficientDataProps,
  AnalysisUnavailableReason,
} from "./InsufficientData";

export { NetworkCard } from "./NetworkCard";
export type { NetworkCardProps } from "./NetworkCard";

export {
  resolveEnvelopeBranch,
  envelopeChartProps,
  EnvelopeBody,
} from "./EnvelopeView";
export type { Envelope, EnvelopeBranch } from "./EnvelopeView";
