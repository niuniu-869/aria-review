import { defineConfig, devices } from "@playwright/test";

/**
 * playwright.config.ts — Track B 端到端验收（杀手锏溯源流）。
 *
 * - testDir "e2e"：与 vitest（src/**.test.ts）零重叠，互不干扰。
 * - webServer：自动起 vite dev（5173），reuseExistingServer 便于本地反复跑。
 * - 全程用 page.route 注入契约 fixture，不依赖后端在线（联调只在 F6）。
 */
export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:5173",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        // 容器内以 root 运行需 --no-sandbox，否则 chromium 拒绝启动
        launchOptions: { args: ["--no-sandbox", "--disable-dev-shm-usage"] },
      },
    },
  ],
  webServer: {
    command: "pnpm exec vite --port 5173 --strictPort",
    url: "http://localhost:5173",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
