import { test, expect } from "@playwright/test";

/**
 * F5 — 语料质检面板：by_type 彩色计数 pill + issues 列表（可点回链 paper）。
 * page.route 注入 quality-report，不依赖后端在线。
 */
const REPORT = {
  total: 10,
  by_type: { missing_metadata: 1, duplicate: 2, not_parsed: 1 },
  issues: [{ paper_id: 3, type: "duplicate", detail: "title+year+doi 撞" }],
};

test("质检面板展示问题分类与计数", async ({ page }) => {
  await page.route("**/projects/*/quality-report", (r) => r.fulfill({ json: REPORT }));
  await page.goto("/dev/quality?projectId=5");

  // 重复（duplicate）分类 pill 可见，计数为 2
  const dupPill = page.locator(".ql-pill", { hasText: /重复|duplicate/ });
  await expect(dupPill).toBeVisible();
  await expect(dupPill).toContainText("2");

  // 缺元数据分类可见
  await expect(page.locator(".ql-pill", { hasText: /缺元数据|missing/ })).toBeVisible();

  // issues 列表渲染该条目，可点回链 paper #3
  const issueRow = page.locator(".ql-issue", { hasText: "title+year+doi 撞" });
  await expect(issueRow).toBeVisible();
  await expect(issueRow.getByRole("link")).toHaveAttribute("href", /\/projects\/5\/library\/3/);
});
