import { useEffect, useRef } from "react";
import type { RefObject } from "react";
import { Network } from "vis-network/peer";
import "vis-network/styles/vis-network.css";
import type { Graph } from "../api/client";

/**
 * 通用网络图渲染 (对应 v0.6 visNetwork)。nodes/edges DTO → vis-network。
 *
 * A3：宣纸主题色（朱砂节点 / 墨色字 / 浅色边）；可选 containerRef 暴露外层容器，
 * 供 ExportMenu 通过 containerRef.current?.querySelector('canvas') 取 vis-network 画布导出 PNG。
 * containerRef 为可选，不传时行为与历史一致（向后兼容）。
 */
export function NetworkGraph({
  graph,
  height = 460,
  containerRef,
}: {
  graph: Graph;
  height?: number;
  /** 可选：外层容器 ref（A3 导出 PNG 用，从中 querySelector('canvas')） */
  containerRef?: RefObject<HTMLDivElement>;
}) {
  const internalRef = useRef<HTMLDivElement>(null);
  // 外部传入 ref 时优先用它（同一 DOM 节点既渲染图、又供导出取 canvas）
  const ref = containerRef ?? internalRef;

  useEffect(() => {
    if (!ref.current || graph.nodes.length === 0) return;
    const data = {
      nodes: graph.nodes.map((n) => ({ id: n.id, label: n.label, value: n.value })),
      edges: graph.edges.map((e) => ({ from: e.source, to: e.target, value: e.weight })),
    };
    const net = new Network(ref.current, data, {
      nodes: {
        shape: "dot",
        scaling: { min: 6, max: 36 },
        font: { size: 12, color: "#1f1c17", face: "PingFang SC, Microsoft YaHei, sans-serif" },
        color: {
          background: "#c0432b",          // 朱砂
          border: "#a8351f",
          highlight: { background: "#d9694f", border: "#a8351f" },
        },
      },
      edges: { color: { color: "#cdbfa6", opacity: 0.45 }, smooth: false },
      physics: { stabilization: { iterations: 120 } },
      interaction: { hover: true, tooltipDelay: 120 },
    });
    return () => net.destroy();
  }, [graph, ref]);

  if (graph.nodes.length === 0)
    return <p style={{ color: "#666" }}>该网络数据不足 (语料较小或缺少相应字段)。</p>;
  return <div ref={ref} style={{ height, border: "1px solid var(--line, #e4ddcd)", borderRadius: 8 }} />;
}
