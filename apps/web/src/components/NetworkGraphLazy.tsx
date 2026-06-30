import { lazy, Suspense } from "react";
import type { RefObject } from "react";
import type { Graph } from "../api/client";

// 代码分割: vis-network 较大, 仅在打开网络页时按需加载
const Inner = lazy(() => import("./NetworkGraph").then((m) => ({ default: m.NetworkGraph })));

export function NetworkGraphLazy(props: {
  graph: Graph;
  height?: number;
  /** A3：透传外层容器 ref，供 ExportMenu 取 vis-network canvas 导出 PNG */
  containerRef?: RefObject<HTMLDivElement>;
}) {
  return (
    <Suspense fallback={<p aria-live="polite">加载图渲染...</p>}>
      <Inner {...props} />
    </Suspense>
  );
}
