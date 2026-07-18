/**
 * NetworkCard.tsx — 网络图卡（A3 知识结构组复用单元，DRY）
 *
 * 把「ChartCard + NodeCountSlider(Top-N 切片) + ExportMenu(PNG/CSV/JSON) + NetworkGraph」
 * 封装为一个自包含卡片。主题地图/知识脉络/作者合作/国家合作 四处共用。
 *
 * 切片算法（客户端）：
 *   1. 按 node.value(强度) 降序排序节点；
 *   2. 取前 N 个节点，收集其 id 集合；
 *   3. 过滤 edges：仅保留 source 与 target 都在 id 集合内的边；
 *   4. 把切片后的 {nodes, edges} 传给 NetworkGraph，并作为 ExportMenu 的 CSV/JSON 源。
 *
 * 滑块范围 10–min(100, 节点总数)，默认 min(50, 节点总数)；节点数≤10 时不显示滑块。
 * 节点<3 或为空 → 用 InsufficientData 诚实空态（不渲染图与滑块/导出）。
 *
 * 导出 PNG：把 containerRef 透传给 NetworkGraph 外层 div，
 * getCanvas 从该 div querySelector('canvas')（vis-network 渲染的 canvas）。
 */
import { useMemo, useRef, useState } from "react";
import type { Graph } from "../../api/client";
import { ChartCard } from "./ChartCard";
import { ExportMenu } from "./ExportMenu";
import { NodeCountSlider } from "./NodeCountSlider";
import { InsufficientData } from "./InsufficientData";
import { NetworkGraphLazy } from "../NetworkGraphLazy";

/** 切片下界：少于此节点数视为「数据不足」 */
const MIN_NODES = 3;
/** 滑块下界 / 步长 / 默认值上限 */
const SLIDER_MIN = 10;
const SLIDER_DEFAULT_CAP = 50;
const SLIDER_HARD_MAX = 100;

/** 按强度降序取前 N，并把边过滤到这些节点（切片算法核心） */
type ValidGraph = {
  nodes: { id: string; label: string; value: number }[];
  edges: { source: string; target: string; weight: number }[];
};

function sliceGraph(graph: Graph, topN: number): ValidGraph {
  const validNodes = graph.nodes.filter(
    (node): node is { id: string; label: string; value: number } =>
      typeof node.id === "string" && node.id.length > 0 &&
      typeof node.label === "string" && typeof node.value === "number",
  );
  // 先按强度降序，再按 id 去重（保留最高强度的那个），避免重复 id 传给 vis-network DataSet 报错
  const sorted = [...validNodes].sort((a, b) => b.value - a.value);
  const seen = new Set<string>();
  const deduped = sorted.filter((n) => {
    if (seen.has(n.id)) return false;
    seen.add(n.id);
    return true;
  });
  const kept = deduped.slice(0, topN);
  const keepIds = new Set(kept.map((n) => n.id));
  const edges = graph.edges.filter(
    (edge): edge is { source: string; target: string; weight: number } =>
      typeof edge.source === "string" && typeof edge.target === "string" &&
      typeof edge.weight === "number" && keepIds.has(edge.source) && keepIds.has(edge.target),
  );
  return { nodes: kept, edges };
}

// ExportMenu CSV 行类型（节点：id/label/value）
type NodeRow = { id: string; label: string; value: number; [k: string]: unknown };

export interface NetworkCardProps {
  title: string;
  subtitle?: string;
  graph: Graph;
  loading?: boolean;
  error?: unknown;
  height?: number;
  /** 导出文件名前缀 */
  filename?: string;
}

export function NetworkCard({
  title,
  subtitle,
  graph,
  loading,
  error,
  height = 460,
  filename = "network",
}: NetworkCardProps) {
  const total = graph.nodes.length;
  // 用户是否拖过滑块：未拖时默认值随当前数据动态算 min(50,total)，
  // 避免「loading 阶段空图把默认固化为 10、数据到后仍只显示 Top10」(codex P1)。
  const [userTopN, setUserTopN] = useState<number | null>(null);

  const sliderMax = Math.min(SLIDER_HARD_MAX, total);
  const showSlider = total > SLIDER_MIN; // 节点数≤10 不显示滑块
  const clampN = (n: number) => Math.min(Math.max(n, SLIDER_MIN), Math.max(sliderMax, SLIDER_MIN));
  // 默认 N = clamp(min(50, 当前节点数))；用户拖动后用其值（仍 clamp 到当前数据范围）
  const defaultN = clampN(Math.min(SLIDER_DEFAULT_CAP, total));
  const effectiveN = userTopN === null ? defaultN : clampN(userTopN);

  const sliced = useMemo(() => sliceGraph(graph, effectiveN), [graph, effectiveN]);

  // 容器 ref：导出 PNG 时从中取 vis-network 渲染的 canvas
  const containerRef = useRef<HTMLDivElement>(null);

  const nodeRows: NodeRow[] = sliced.nodes.map((n) => ({ id: n.id, label: n.label, value: n.value }));

  const isLoadingOrError = loading || !!error;
  const insufficient = !isLoadingOrError && total < MIN_NODES;

  return (
    <ChartCard
      title={title}
      subtitle={subtitle}
      loading={loading}
      error={error}
      empty={
        insufficient ? (
          <InsufficientData
            reason="not_enough_data"
            message="该网络节点过少，无法形成有意义的关系图。"
            howto="可纳入更多文献，或导入含相应字段（合著/共被引/关键词）的题录以丰富网络。"
          />
        ) : undefined
      }
      actions={
        !insufficient && !isLoadingOrError ? (
          <>
            {showSlider && (
              <NodeCountSlider
                value={effectiveN}
                min={SLIDER_MIN}
                max={sliderMax}
                step={5}
                onChange={setUserTopN}
                label="节点数"
              />
            )}
            <ExportMenu
              filename={filename}
              target={{
                kind: "network",
                getCanvas: () => containerRef.current?.querySelector("canvas") ?? null,
                csv: {
                  columns: [
                    { key: "id", label: "节点ID" },
                    { key: "label", label: "标签" },
                    { key: "value", label: "强度" },
                  ],
                  rows: nodeRows,
                },
                // 导出当前切片后的 {nodes, edges}
                json: () => sliced,
              }}
            />
          </>
        ) : undefined
      }
      hint={
        !insufficient && !isLoadingOrError && showSlider
          ? `按节点强度取前 ${effectiveN} 个（共 ${total} 个），边已收敛至所选节点之间。`
          : undefined
      }
    >
      <NetworkGraphLazy graph={sliced} height={height} containerRef={containerRef} />
    </ChartCard>
  );
}
