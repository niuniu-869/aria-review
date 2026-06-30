/**
 * echartsSetup.ts — ECharts 按需注册（tree-shake）
 *
 * 集中一处用 echarts.use([...]) 注册 A0–A5 各面板会用到的 chart/component + renderer，
 * 避免全量 `import * as echarts from 'echarts'`（包体翻倍）。
 * 词云扩展 echarts-wordcloud 以 side-effect import 注册到同一 echarts 实例。
 *
 * 其它模块统一从这里 re-export `echarts`（已注册），不要各自再 import echarts/core。
 */
import * as echarts from "echarts/core";
import {
  LineChart,
  BarChart,
  ScatterChart,
  PieChart,
  HeatmapChart,
  ThemeRiverChart,
  SankeyChart,
  GraphChart,
} from "echarts/charts";
import {
  TitleComponent,
  TooltipComponent,
  GridComponent,
  LegendComponent,
  DataZoomComponent,
  VisualMapComponent,
  MarkLineComponent,
  MarkPointComponent,
  SingleAxisComponent,
} from "echarts/components";
import { CanvasRenderer, SVGRenderer } from "echarts/renderers";

echarts.use([
  LineChart,
  BarChart,
  ScatterChart,
  PieChart,
  HeatmapChart,
  ThemeRiverChart,
  SankeyChart,
  GraphChart,
  TitleComponent,
  TooltipComponent,
  GridComponent,
  LegendComponent,
  DataZoomComponent,
  VisualMapComponent,
  MarkLineComponent,
  MarkPointComponent,
  SingleAxisComponent,
  CanvasRenderer,
  SVGRenderer,
]);

/**
 * 词云扩展按需注册。
 *
 * echarts-wordcloud 在 import 时即做 canvas 特性探测（layout.isSupported），
 * jsdom 无真实 canvas → 直接抛 "Sorry your browser not support wordCloud"。
 * 为避免该 side-effect 在 jsdom 单测里炸掉任何 import 此模块的面板，
 * 这里改为「显式调用 + try/catch 静态 import」的惰性注册：
 * - 真实浏览器：调用一次即注册 wordCloud series（幂等）
 * - jsdom：探测失败被吞掉，词云图回退为空（A3 面板会以诚实空态/降级处理）
 *
 * A3 词云面板在渲染前调用 ensureWordCloud()。
 */
let wordCloudReady = false;
export async function ensureWordCloud(): Promise<boolean> {
  if (wordCloudReady) return true;
  try {
    await import("echarts-wordcloud");
    wordCloudReady = true;
    return true;
  } catch {
    // jsdom / 不支持 canvas 的环境：忽略，词云不可用
    return false;
  }
}

export { echarts };
