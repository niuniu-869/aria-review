/**
 * track.test.ts — 埋点上报是 best-effort：正确 POST /events，且任何失败都不外抛。
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { localDateKey, shouldTrackDailyAppOpen, track } from "./track";
import { API_BASE } from "../api/client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("track", () => {
  it("POST /events，带 credentials 与正确 body", () => {
    const fetchSpy = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("fetch", fetchSpy);

    track("review_precheck_blocked", { reason: "no_included" }, 7);

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${API_BASE}/events`);
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    expect(JSON.parse(init.body as string)).toEqual({
      event: "review_precheck_blocked",
      projectId: 7,
      props: { reason: "no_included" },
    });
  });

  it("fetch reject 时静默吞掉，绝不外抛", () => {
    const fetchSpy = vi.fn().mockRejectedValue(new Error("network"));
    vi.stubGlobal("fetch", fetchSpy);
    expect(() => track("review_job_failed", { jobId: 1 }, 7)).not.toThrow();
  });

  it("可行性点击上报 gapId", () => {
    const fetchSpy = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("fetch", fetchSpy);
    track("gap_feasibility_click", { gapId: "g2" }, 5);
    const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toMatchObject({
      event: "gap_feasibility_click",
      projectId: 5,
      props: { gapId: "g2" },
    });
  });

  it("fetch 不可用时直接返回，不外抛", () => {
    vi.stubGlobal("fetch", undefined);
    expect(() => track("review_view", undefined, 7)).not.toThrow();
  });
});

describe("app_open 每日去重（回访口径核心）", () => {
  it("首次访问（无记录）应上报", () => {
    expect(shouldTrackDailyAppOpen(null, "2026-07-11")).toBe(true);
  });

  it("同一自然日第二次打开不再上报", () => {
    expect(shouldTrackDailyAppOpen("2026-07-11", "2026-07-11")).toBe(false);
  });

  it("跨自然日回访应再次上报", () => {
    expect(shouldTrackDailyAppOpen("2026-07-10", "2026-07-11")).toBe(true);
  });

  it("localDateKey 用本地时区且补零", () => {
    expect(localDateKey(new Date(2026, 0, 5, 23, 59))).toBe("2026-01-05");
    expect(localDateKey(new Date(2026, 11, 31, 0, 0))).toBe("2026-12-31");
  });
});
