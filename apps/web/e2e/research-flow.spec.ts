import { test, expect, type Route } from "@playwright/test";
import {
  gapDraftConcept,
  gapVerifiedMethod,
  gapDraftMethod,
  verdictInconclusiveG5,
  verdictResultG2,
  verdictResultG5,
  FIXTURE_RUN_ID,
  FIXTURE_VERIFY_RUN_ID,
} from "../src/api/research.fixtures";
import type { GapCandidate, ScratchpadState } from "../src/types/research";

/**
 * B5 — 研究副驾 HITL 全流程 e2e（discover→scratchpad→verify→verdict→accept）。
 *
 * 非虚绿：fixture 与 vitest 同源 import 自 src/api/research.fixtures（单一真相，禁手抄漂移）。
 * 单 dispatcher route 注入 5 个研究 endpoint；g5 走 draft→verified→accepted 状态机模拟
 * verify 异步产出裁决。/dev/research 用固定 pid/cid override，不依赖后端在线。
 */
test("研究副驾 HITL 全流程：发现→实时累积→核验→裁决→采纳", async ({ page }) => {
  // g5 三态：draft → verified(after :verify) → accepted(after PATCH)
  let verified = false;
  let accepted = false;

  const g5 = (): GapCandidate =>
    accepted
      ? { ...gapDraftMethod, status: "accepted", value_verdict: verdictInconclusiveG5 }
      : verified
        ? { ...gapDraftMethod, status: "verified", value_verdict: verdictInconclusiveG5 }
        : { ...gapDraftMethod };

  const scratchpad = (): ScratchpadState => ({
    run_id: FIXTURE_RUN_ID,
    run_status: "running",
    entries: [gapDraftConcept, gapVerifiedMethod, g5()],
    updated_at: "2026-06-16T03:14:07Z",
  });

  await page.route("**/projects/**", async (route: Route) => {
    const url = route.request().url();
    const method = route.request().method();
    // 按 endpoint 同时断言 method（防 GET/POST/PATCH 写反仍虚绿，codex B5-P2）
    if (url.includes("gaps:discover")) {
      expect(method, "discover 必须 POST").toBe("POST");
      return route.fulfill({ status: 202, json: { run_id: FIXTURE_RUN_ID } });
    }
    if (url.includes("/scratchpad")) {
      expect(method, "scratchpad 必须 GET").toBe("GET");
      return route.fulfill({ json: scratchpad() });
    }
    if (url.includes(":verify")) {
      expect(method, "verify 必须 POST").toBe("POST");
      verified = true;
      return route.fulfill({ status: 202, json: { verify_run_id: FIXTURE_VERIFY_RUN_ID } });
    }
    if (url.includes("/verdict")) {
      expect(method, "verdict 必须 GET").toBe("GET");
      const gid = url.match(/\/gaps\/([^/]+)\/verdict/)?.[1];
      if (gid === "g2") return route.fulfill({ json: verdictResultG2 });
      if (gid === "g5" && verified) return route.fulfill({ json: verdictResultG5 });
      return route.fulfill({ status: 404, json: { code: "NOT_FOUND", message: "裁决尚未产生" } });
    }
    if (method === "PATCH") {
      // 校验 HITL union body 真为 accept 形状（accept 不带 statement，防 union 构造虚绿）
      expect(route.request().postDataJSON()).toEqual({ action: "accept" });
      accepted = true;
      return route.fulfill({ json: g5() });
    }
    return route.fulfill({ status: 404, json: { code: "NOT_FOUND", message: "unhandled" } });
  });

  await page.goto("/dev/research");
  await expect(page.getByTestId("research-view")).toBeVisible();

  // 1) discover：点「发现研究空白」
  await page.getByRole("button", { name: "发现研究空白" }).click();

  // 2) scratchpad 实时累积：GapPanel + ScratchpadLive 都出现条目
  await expect(page.locator('.research-main [data-gap-id="g5"]')).toBeVisible();
  await expect(page.locator(".scratchpad")).toContainText("研究笔记本");
  await expect(page.locator(".sp-feed [data-gap-id]")).not.toHaveCount(0);
  await page.screenshot({ path: "test-results/research-flow-discover.png" });

  // 3) 选中 g5（草稿）→ 详情显示「核验研究价值」
  await page.locator('.research-main [data-gap-id="g5"] .gap-card-head').click();
  const verifyBtn = page.getByRole("button", { name: "核验研究价值" });
  await expect(verifyBtn).toBeVisible();

  // 4) verify → 轮询拿到 g5 verified → ValueVerdictCard 出现（确定性裁决，证据不足）
  await verifyBtn.click();
  await expect(page.locator(".vv-card")).toBeVisible({ timeout: 8000 });
  await expect(page.locator(".vv-card")).toContainText("证据不足");
  await expect(page.locator(".vv-decided")).toContainText("确定性裁决");
  await expect(page.getByRole("img", { name: /命中 11 篇/ })).toBeVisible();
  await page.screenshot({ path: "test-results/research-flow-verdict.png" });

  // 5) HITL accept → PATCH → g5 已采纳（人工定稿）
  await page.getByRole("button", { name: "采纳" }).click();
  await expect(page.locator(".research-detail")).toContainText("已采纳（人工定稿", { timeout: 8000 });
  await page.screenshot({ path: "test-results/research-flow-accepted.png" });
});

test("无 run 时空态友好：未启动 + 引导选择", async ({ page }) => {
  await page.route("**/projects/**", (route) => route.fulfill({ status: 404, json: { code: "X", message: "x" } }));
  await page.goto("/dev/research");
  await expect(page.getByTestId("research-view")).toBeVisible();
  // 未启动 run：ScratchpadLive 未启动徽标 + GapPanel 空态 + 详情引导
  await expect(page.locator(".sp-run-idle")).toBeVisible();
  await expect(page.getByText("尚未发现研究空白")).toBeVisible();
  await expect(page.locator(".research-detail-empty")).toBeVisible();
});
