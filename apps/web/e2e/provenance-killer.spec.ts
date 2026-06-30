import { test, expect } from "@playwright/test";
import {
  sampleFullMarkdown,
  sampleReviewWithProvenance,
  sampleStructure,
} from "./provenanceSamples";

/**
 * F3 — ★杀手锏：点综述里的引用 → 原文献对应块高亮（markdown 级）+ 双向联动。
 * 用内联合成契约样例，page.route 注入 + window.__DEV_REVIEW__ 注入 review，不依赖后端在线。
 */
const MARKDOWN = {
  markdown: sampleFullMarkdown,
  length: sampleFullMarkdown.length,
  truncated: false,
  sha256: sampleStructure.markdown_sha256,
};

test("点综述里的引用 → 原文献对应块高亮", async ({ page }) => {
  await page.route("**/projects/*/papers/*/structure", (r) => r.fulfill({ json: sampleStructure }));
  await page.route("**/projects/*/papers/*/markdown", (r) => r.fulfill({ json: MARKDOWN }));
  // 注入 review（occurrence anchor; 首锚点 → block_idx 12 / 3 Results）
  await page.addInitScript((rv) => {
    (window as unknown as { __DEV_REVIEW__: unknown }).__DEV_REVIEW__ = rv;
  }, sampleReviewWithProvenance);

  await page.goto("/dev/review-provenance");

  // 综述里有可点击锚点（真实 occurrence anchor）
  const anchor = page.locator(".prov-anchor").first();
  await expect(anchor).toBeVisible();
  const anchorId = await anchor.getAttribute("data-anchor-id");
  expect(anchorId).toMatch(/__occ\d+$/); // occurrence 级 id
  await anchor.click();

  // 点击后 SourceViewer 打开且目标块高亮可见（首锚点 → block 12 = 真实行 25, 3 Results）
  const hl = page.locator("[data-block-highlight='true']");
  await expect(hl).toBeVisible();
  await expect(hl).toContainText("Across all three datasets"); // block 12 真实正文(3 Results)

  // 双向：原文高亮块标注了回链锚点 id
  await expect(page.locator(`[data-source-anchor='${anchorId}']`)).toBeVisible();

  // 反向联动：点原文高亮块 → 左侧对应锚点切到选中态
  await page.locator(`[data-source-anchor='${anchorId}']`).click();
  await expect(page.locator(`.prov-anchor.is-active[data-anchor-id='${anchorId}']`)).toBeVisible();
});

test("provenance_map 缺失时优雅降级为纯文本(不报错)", async ({ page }) => {
  await page.goto("/dev/review-provenance?degrade=1");
  // 降级：渲染综述正文但无可点锚点、无 split-pane 右栏
  await expect(page.locator(".prov-review")).toBeVisible();
  await expect(page.locator(".prov-anchor")).toHaveCount(0);
  await expect(page.locator(".sv-pane")).toHaveCount(0);
});
