/**
 * EChart.tsx — ECharts React 封装（宣纸主题）
 *
 * - 按需注册（见 echartsSetup）+ bibliocn 主题 init
 * - ResizeObserver 自适应容器宽度；卸载时 dispose()
 * - 尊重 prefers-reduced-motion（reduced 时 option.animation=false）
 * - 通过 ref（EChartHandle）暴露 getDataURL / renderToSVGString / getInstance，供 ExportMenu 用
 */
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import type { EChartsType } from "echarts/core";
import type { EChartsOption } from "echarts";
import { echarts } from "./echartsSetup";
import { registerBiblioTheme } from "../../theme/echartsTheme";

/** ECharts 事件回调表：事件名 → handler */
export type EChartEvents = Record<string, (params: unknown) => void>;

/** 通过 ref 暴露的导出能力（ExportMenu 消费） */
export interface EChartHandle {
  /** PNG dataURL（canvas renderer 时有效；svg renderer 用 renderToSVGString） */
  getDataURL: (opts?: {
    type?: "png" | "jpeg";
    pixelRatio?: number;
    backgroundColor?: string;
  }) => string | undefined;
  /** SVG 字符串（仅 svg renderer 时返回，否则 undefined） */
  renderToSVGString: () => string | undefined;
  /** 原始 echarts 实例（高级用法） */
  getInstance: () => EChartsType | null;
}

export interface EChartProps {
  option: EChartsOption;
  /** 高度，默认 320 */
  height?: number;
  /** 渲染器，默认 canvas；需 SVG 导出时传 'svg' */
  renderer?: "canvas" | "svg";
  /** ECharts 事件订阅（如 { click: (p)=>... }） */
  onEvents?: EChartEvents;
  className?: string;
  /** 无障碍：图表语义标签 */
  ariaLabel?: string;
}

/** 是否减少动效（用户系统偏好） */
function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

export const EChart = forwardRef<EChartHandle, EChartProps>(function EChart(
  { option, height = 320, renderer = "canvas", onEvents, className, ariaLabel },
  ref
) {
  const elRef = useRef<HTMLDivElement>(null);
  const instRef = useRef<EChartsType | null>(null);
  // setOption 抛错时置 true（数据异常致 ECharts 内部崩溃），渲染兜底覆盖层而非白屏
  const [renderError, setRenderError] = useState(false);

  // 暴露导出能力
  useImperativeHandle(
    ref,
    (): EChartHandle => ({
      getDataURL: (opts) =>
        instRef.current?.getDataURL({
          type: opts?.type ?? "png",
          pixelRatio: opts?.pixelRatio ?? 2,
          backgroundColor: opts?.backgroundColor ?? "#fffdf8",
        }),
      renderToSVGString: () => {
        const inst = instRef.current;
        // renderToSVGString 仅在 svg renderer 下存在
        const fn = (inst as unknown as { renderToSVGString?: () => string })
          ?.renderToSVGString;
        return typeof fn === "function" ? fn.call(inst) : undefined;
      },
      getInstance: () => instRef.current,
    }),
    []
  );

  // 初始化 / 重建（renderer 变化时需重建实例）
  useEffect(() => {
    const el = elRef.current;
    if (!el) return;
    registerBiblioTheme();
    const inst = echarts.init(el, "bibliocn", { renderer });
    instRef.current = inst;

    // 自适应宽度（ResizeObserver 不可用时退化到 window.resize，保证 cleanup 总能注册）
    let ro: ResizeObserver | undefined;
    const onWinResize = () => inst.resize();
    if (typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(() => inst.resize());
      ro.observe(el);
    } else if (typeof window !== "undefined") {
      window.addEventListener("resize", onWinResize);
    }

    return () => {
      ro?.disconnect();
      if (typeof window !== "undefined") window.removeEventListener("resize", onWinResize);
      inst.dispose();
      instRef.current = null;
    };
  }, [renderer]);

  // option / reduced-motion 变化时更新（renderer 变化重建实例后也需重设 option）
  useEffect(() => {
    const inst = instRef.current;
    if (!inst) return;
    const finalOption: EChartsOption = prefersReducedMotion()
      ? { ...option, animation: false }
      : option;
    // notMerge 保证移除旧 series（如切换数据集）
    // 防御：真实语料常缺字段，异常 option 可能让 ECharts 内部抛错并冒泡到 React 根 → 整页白屏。
    // 此处捕获后只在容器内显示兜底提示，不让异常逃逸。
    // 用函数式 setState 并在值未变化时直接返回原值，避免成功路径上每次 setOption 都触发额外 re-render。
    try {
      inst.setOption(finalOption, { notMerge: true });
      setRenderError((prev) => (prev ? false : prev));
    } catch (e) {
      console.error("EChart setOption 失败:", e);
      setRenderError((prev) => (prev ? prev : true));
    }
  }, [option, renderer]);

  // 事件订阅（onEvents 或 renderer 变化时重绑；renderer 变化会重建实例需重新绑定）
  useEffect(() => {
    const inst = instRef.current;
    if (!inst || !onEvents) return;
    const entries = Object.entries(onEvents);
    for (const [name, handler] of entries) inst.on(name, handler);
    return () => {
      for (const [name, handler] of entries) inst.off(name, handler);
    };
  }, [onEvents, renderer]);

  // 外层 wrapper 保证 elRef div 始终挂载（init/resize/dispose 生命周期依赖它）；
  // renderError 时只叠加一个绝对定位覆盖层显示兜底文案，不卸载图表容器。
  return (
    <div style={{ position: "relative", width: "100%", height }}>
      <div
        ref={elRef}
        className={className}
        style={{ width: "100%", height: "100%" }}
        // 无 ariaLabel 时不暴露无名 role="img"（避免 AT 读到匿名图片）；
        // renderError 时语义交给覆盖层(role=alert)，容器置 aria-hidden 防 AT 重复读旧图 (codex P2)。
        {...(renderError
          ? { "aria-hidden": true }
          : ariaLabel
          ? { role: "img", "aria-label": ariaLabel }
          : { "aria-hidden": true })}
      />
      {renderError && (
        <div
          className="state state-err"
          role="alert"
          style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: "var(--paper, #fffdf8)",
            fontSize: "0.85rem",
          }}
        >
          图表渲染失败（数据异常）
        </div>
      )}
    </div>
  );
});
