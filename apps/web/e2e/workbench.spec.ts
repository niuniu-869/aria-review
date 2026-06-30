import { test, expect } from "@playwright/test";

/**
 * F1 — 首页 IA 重定位为"语料工作台"（不再是综述生成器）。
 * 断言：工作台叙事标题 + 语料生产线四段关键字 + 综述/分析仍可达（降为 tab/link，不删）。
 */
test("首页是语料工作台,不是综述生成器", async ({ page }) => {
  await page.goto("/");

  // 工作台叙事标题
  await expect(
    page.getByRole("heading", { name: /语料工作台|Corpus Workbench/ }),
  ).toBeVisible();

  // 语料生产线四段（导入 → 加工 → 结构化语料 → 下游应用）
  await expect(page.getByText(/导入/).first()).toBeVisible();
  await expect(page.getByText(/结构化语料/).first()).toBeVisible();

  // ① 导入段内嵌的现有"我的项目"心智不丢
  await expect(page.getByText(/我的项目/).first()).toBeVisible();

  // 综述仍可达（④ 下游应用保留专门入口，不删）— 精确命名避免与生产线 rail 文案歧义
  await expect(page.getByRole("link", { name: "AI 综述" })).toBeVisible();
  // 分析入口同样保留
  await expect(page.getByRole("link", { name: "文献计量分析" })).toBeVisible();
});
