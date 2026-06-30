/**
 * echartsTheme.ts — 「学术·宣纸」ECharts 主题
 *
 * 注册名为 `bibliocn` 的 ECharts 主题，色板/排版映射 styles.css 的 CSS 变量计算值。
 * - 主色序列：朱砂 → 靛蓝 → 金 → 墨色 → 柔朱砂（循环）
 * - 文本 --ink/--ink-2/--ink-3；标题宋体 --serif；轴/图例 --sans；背景透明（透出纸纹）；网格线 --line
 * - 暗色 token 切换时，重新调用 registerBiblioTheme() 即可同步（读的是当前计算值）
 *
 * 注意：只在浏览器（有 document）时读 CSS 变量；jsdom/SSR 无变量时走硬编码兜底色板（与亮色 token 同值）。
 */
import * as echarts from "echarts/core";

/** 兜底色板（与 styles.css :root 亮色 token 同值），在无 CSS 变量环境（jsdom/SSR）下使用 */
const FALLBACK = {
  cinnabar: "#c0432b",
  cinnabarSoft: "#f3ddd5",
  indigo: "#2f4858",
  gold: "#b08423",
  ink: "#1f1c17",
  ink2: "#4a443b",
  ink3: "#8a8276",
  line: "#e4ddcd",
  serif:
    '"Songti SC", "STSong", "SimSun", "Source Han Serif SC", "Noto Serif CJK SC", Georgia, "Times New Roman", serif',
  sans:
    '"PingFang SC", "Microsoft YaHei", "Hiragino Sans GB", "Heiti SC", -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif',
};

/** 读单个 CSS 变量计算值；无 document 或值为空时返回兜底 */
function cssVar(name: string, fallback: string): string {
  if (typeof document === "undefined" || !document.documentElement) return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/** 主题是否已注册（避免重复注册的无谓开销；切暗色时可传 force 重注册） */
let registered = false;

/**
 * 注册 `bibliocn` 主题。幂等：默认只注册一次。
 * @param force 暗色 token 切换后传 true，以当前计算值重新注册。
 */
export function registerBiblioTheme(force = false): void {
  if (registered && !force) return;

  const cinnabar = cssVar("--cinnabar", FALLBACK.cinnabar);
  const indigo = cssVar("--indigo", FALLBACK.indigo);
  const gold = cssVar("--gold", FALLBACK.gold);
  const ink = cssVar("--ink", FALLBACK.ink);
  const ink2 = cssVar("--ink-2", FALLBACK.ink2);
  const ink3 = cssVar("--ink-3", FALLBACK.ink3);
  const line = cssVar("--line", FALLBACK.line);
  const cinnabarSoft = cssVar("--cinnabar-soft", FALLBACK.cinnabarSoft);
  const serif = cssVar("--serif", FALLBACK.serif);
  const sans = cssVar("--sans", FALLBACK.sans);

  // 主色序列：朱砂 → 靛蓝 → 金 → 墨色(--ink-2) → 柔朱砂（循环）
  const palette = [cinnabar, indigo, gold, ink2, cinnabarSoft];

  echarts.registerTheme("bibliocn", {
    color: palette,
    // 背景透明，透出宣纸纹理
    backgroundColor: "transparent",
    textStyle: { fontFamily: sans, color: ink2 },
    title: {
      textStyle: { fontFamily: serif, color: ink, fontWeight: 700 },
      subtextStyle: { fontFamily: sans, color: ink3 },
    },
    legend: {
      textStyle: { fontFamily: sans, color: ink2 },
    },
    tooltip: {
      backgroundColor: cssVar("--card", "#fffdf8"),
      borderColor: line,
      borderWidth: 1,
      textStyle: { fontFamily: sans, color: ink },
      extraCssText: "box-shadow: 0 4px 14px rgba(31,28,23,0.12);",
    },
    // 直角坐标系
    grid: { borderColor: line, containLabel: true },
    categoryAxis: axisStyle(line, ink3, sans),
    valueAxis: axisStyle(line, ink3, sans),
    logAxis: axisStyle(line, ink3, sans),
    timeAxis: axisStyle(line, ink3, sans),
    // 折线
    line: {
      itemStyle: { borderWidth: 2 },
      lineStyle: { width: 2 },
      symbolSize: 6,
      symbol: "circle",
      smooth: false,
    },
    // 柱状
    bar: { itemStyle: { barBorderWidth: 0 } },
    // 散点
    scatter: { itemStyle: { opacity: 0.8 } },
    // 关系/力导向（若用 ECharts graph）
    graph: {
      color: palette,
      lineStyle: { color: line, width: 1, opacity: 0.5 },
      label: { color: ink2, fontFamily: sans },
    },
    // 视觉映射默认色
    visualMap: { textStyle: { color: ink2, fontFamily: sans } },
  });

  // 仅当读到真实 CSS 变量时才锁定（避免首注册时变量未就绪 → 永久锁死兜底色板；
  // 未就绪则保持 registered=false，下次 mount 重注册以取到真实宣纸色）
  registered =
    typeof document !== "undefined" &&
    !!document.documentElement &&
    !!getComputedStyle(document.documentElement).getPropertyValue("--cinnabar").trim();
}

/** 轴样式工厂（DRY：category/value/log/time 共用） */
function axisStyle(line: string, ink3: string, sans: string) {
  return {
    axisLine: { show: true, lineStyle: { color: line } },
    axisTick: { show: true, lineStyle: { color: line } },
    axisLabel: { color: ink3, fontFamily: sans },
    splitLine: { show: true, lineStyle: { color: line, type: "dashed" as const } },
    splitArea: { show: false },
  };
}
