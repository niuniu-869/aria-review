import { describe, expect, it } from "vitest";
import { getProjectReadiness } from "../useProjectReadiness";

describe("getProjectReadiness", () => {
  it.each([
    [{ paperCount: 0, includedCount: 0, readableFulltextCount: 0 }, "no_papers"],
    [{ paperCount: 3, includedCount: 0, readableFulltextCount: 0 }, "no_included"],
    [{ paperCount: 3, includedCount: 1, readableFulltextCount: 0 }, "no_fulltext"],
    [{ paperCount: 3, includedCount: 1, readableFulltextCount: 0, ocrDoneCount: 0 }, "not_parsed"],
    [{ paperCount: 3, includedCount: 1, readableFulltextCount: 1 }, "ready"],
  ] as const)("统计 %o 映射为 %s", (stats, stage) => {
    const result = getProjectReadiness(stats, 7);

    expect(result).toMatchObject({ stage });
    expect(result?.label).toBeTruthy();
    expect(result?.actionText).toBeTruthy();
    expect(result?.actionHref).toMatch(/^\/projects\/7/);
  });

  it("ocrDoneCount 未知（null）时不细分 not_parsed，维持 no_fulltext", () => {
    const result = getProjectReadiness(
      { paperCount: 3, includedCount: 1, readableFulltextCount: 0, ocrDoneCount: null },
      7,
    );
    expect(result?.stage).toBe("no_fulltext");
  });

  it("统计未加载时不产生就绪度结果", () => {
    expect(getProjectReadiness(undefined, 7)).toBeUndefined();
  });
});
