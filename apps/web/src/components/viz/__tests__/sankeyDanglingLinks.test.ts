/**
 * sankeyDanglingLinks.test.ts — sankey 悬空边过滤护栏单测
 *
 * 背景：ECharts sankey 对引用了不存在节点的 link（悬空边）会抛异常 → 冒泡到 React 根 → 整页白屏。
 * 真实语料常缺字段，故 build*SankeyOption 必须在构建 series 前剔除悬空边。
 *
 * 纯函数测试，无需 mock echarts。
 */
import { describe, it, expect } from "vitest";
import {
  buildThreeFieldSankeyOption,
  buildEvolutionSankeyOption,
} from "../advancedCharts";

describe("buildThreeFieldSankeyOption 悬空边过滤", () => {
  it("source/target 不在节点 name 集合内的 link 被剔除，且不抛异常", () => {
    const opt = buildThreeFieldSankeyOption({
      nodes: [
        { name: "A:X", layer: 0 },
        { name: "K:Y", layer: 1 },
        { name: "S:Z", layer: 2 },
      ],
      links: [
        // 合法边
        { source: "A:X", target: "K:Y", value: 2 },
        { source: "K:Y", target: "S:Z", value: 1 },
        // 悬空边：target 不存在
        { source: "A:X", target: "K:GHOST", value: 5 },
        // 悬空边：source 不存在
        { source: "S:MISSING", target: "S:Z", value: 4 },
      ],
    });
    const series = opt.series as Array<{ links: Array<{ source: string; target: string; value: number }> }>;
    // 仅保留 2 条合法边
    expect(series[0].links).toHaveLength(2);
    const pairs = series[0].links.map((l) => `${l.source}->${l.target}`);
    expect(pairs).toContain("A:X->K:Y");
    expect(pairs).toContain("K:Y->S:Z");
    expect(pairs).not.toContain("A:X->K:GHOST");
    expect(pairs).not.toContain("S:MISSING->S:Z");
  });

  it("全部为悬空边时 links 为空，不抛异常", () => {
    expect(() =>
      buildThreeFieldSankeyOption({
        nodes: [{ name: "A:X", layer: 0 }],
        links: [{ source: "GHOST1", target: "GHOST2", value: 1 }],
      })
    ).not.toThrow();
    const opt = buildThreeFieldSankeyOption({
      nodes: [{ name: "A:X", layer: 0 }],
      links: [{ source: "GHOST1", target: "GHOST2", value: 1 }],
    });
    const series = opt.series as Array<{ links: unknown[] }>;
    expect(series[0].links).toHaveLength(0);
  });
});

describe("buildEvolutionSankeyOption 悬空边过滤", () => {
  it("link 引用不存在的 id → 剔除，不抛异常", () => {
    expect(() =>
      buildEvolutionSankeyOption({
        nodes: [{ name: "AI", period: "P1", id: 0 }],
        links: [{ source: 0, target: 99, value: 1 }],
      })
    ).not.toThrow();
    const opt = buildEvolutionSankeyOption({
      nodes: [
        { name: "AI", period: "P1", id: 0 },
        { name: "ML", period: "P2", id: 1 },
      ],
      links: [
        { source: 0, target: 1, value: 0.8 }, // 合法
        { source: 0, target: 99, value: 0.5 }, // 悬空：target id 不存在
        { source: 88, target: 1, value: 0.3 }, // 悬空：source id 不存在
      ],
    });
    const series = opt.series as Array<{ links: Array<{ source: string; target: string }> }>;
    // 仅保留 1 条合法边，且 source/target 均落在节点 name 上
    expect(series[0].links).toHaveLength(1);
    expect(series[0].links[0].source).toContain("P1");
    expect(series[0].links[0].target).toContain("P2");
  });
});
