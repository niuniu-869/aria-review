import { test, expect, request as pwRequest } from "@playwright/test";

/**
 * F6 — 真实后端联调（非 mock）。
 *
 * 现状（2026-06-14）：后端 /markdown 端点已就绪（main.py:1509），可真实校验契约形状；
 * /structure 与 综述 provenance_map 由 Track A（B6）补，未就绪时端到端杀手锏 test.skip 并标注
 * 原因（杀手锏由 F3 mock playwright 守护）。后端未起也 skip。
 */
const API = process.env.VITE_API_BASE || "http://localhost:8000";

async function firstPaper(ctx: Awaited<ReturnType<typeof pwRequest.newContext>>) {
  const ph = await ctx.get(`${API}/healthz`);
  if (!ph.ok()) return { skip: `后端不可达 (${API}/healthz=${ph.status()})` as string };
  const projects = await (await ctx.get(`${API}/projects`)).json();
  const pid = projects.projects?.[0]?.id;
  if (!pid) return { skip: "后端无项目" };
  const papers = await (await ctx.get(`${API}/projects/${pid}/papers`)).json();
  const paperId = papers.papers?.[0]?.paperId;
  if (!paperId) return { skip: `项目 #${pid} 无文献` };
  return { pid, paperId };
}

test("真实 /markdown 端点返回契约形状(真实后端)", async () => {
  const ctx = await pwRequest.newContext();
  try {
    const f = await firstPaper(ctx);
    test.skip(!!f.skip, f.skip ?? "");
    const r = await ctx.get(`${API}/projects/${f.pid}/papers/${f.paperId}/markdown`);
    expect(r.ok()).toBeTruthy();
    const j = await r.json();
    // 契约 §2.2：{ markdown, length, truncated, sha256 }
    expect(j).toHaveProperty("markdown");
    expect(j).toHaveProperty("sha256");
    expect(typeof j.markdown).toBe("string");
  } finally {
    await ctx.dispose();
  }
});

test("真实端到端杀手锏(/structure 未就绪则 skip)", async ({ page }) => {
  const ctx = await pwRequest.newContext();
  let pid: number | undefined;
  let paperId: number | undefined;
  let structureStatus = -1;
  try {
    const f = await firstPaper(ctx);
    test.skip(!!f.skip, f.skip ?? "");
    pid = f.pid;
    paperId = f.paperId;
    const s = await ctx.get(`${API}/projects/${pid}/papers/${paperId}/structure`);
    structureStatus = s.status();
  } finally {
    await ctx.dispose();
  }

  test.skip(
    structureStatus !== 200,
    `真实 /structure 端点未就绪 (status=${structureStatus})；Track A B6 待补，杀手锏由 F3 mock playwright 守护`,
  );

  // —— /structure 就绪后真实渲染（当前会 skip）：直挂 SourceViewer 用真实端点（不 mock）——
  await page.goto(`/dev/source-viewer?projectId=${pid}&paperId=${paperId}&blockIdx=0`);
  await expect(
    page.locator("[data-block-highlight='true']").or(page.locator(".sv-degrade")),
  ).toBeVisible();
});
