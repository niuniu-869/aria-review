/**
 * feasibilityHooks.test.tsx — 可行性核验 hooks 的真实 fetch 编排。
 * 覆盖 202 受理后轮询至裁决、未核验 404、job failed 后停止 verdict 轮询。
 */
import React, { useState } from "react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  isFeasibilityVerdictPending,
  useAiJob,
  useFeasibilityVerdict,
  useFeasibilityVerify,
} from "./agentHooks";
import { feasibilityAccepted, feasibilityResultG2 } from "./research.fixtures";

function wrapper(client: QueryClient) {
  return function QueryWrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("feasibility hooks", () => {
  it("POST 202 后轮询：先未就绪，再拿到可行性裁决", async () => {
    let verdictCalls = 0;
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/gaps/g2:feasibility") && init?.method === "POST") {
        return jsonResponse(feasibilityAccepted, 202);
      }
      if (url.endsWith("/gaps/g2/feasibility-verdict")) {
        verdictCalls += 1;
        if (verdictCalls === 1) {
          return jsonResponse({ code: "GAP_NOT_FEASIBILITY_CHECKED", message: "尚未核验" }, 404);
        }
        return jsonResponse(feasibilityResultG2);
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    const mutation = renderHook(() => useFeasibilityVerify(5), { wrapper: wrapper(qc) });
    await act(async () => {
      const accepted = await mutation.result.current.mutateAsync({ gapId: "g2" });
      expect(accepted.feasibility_run_id).toBe(feasibilityAccepted.feasibility_run_id);
    });

    const verdict = renderHook(
      () => useFeasibilityVerdict(5, "g2", { poll: true, pollMs: 20 }),
      { wrapper: wrapper(qc) },
    );
    // 首次 404 返回 {pending:true} 哨兵并继续轮询，第二次拿到真实裁决
    await waitFor(() => {
      const data = verdict.result.current.data;
      expect(isFeasibilityVerdictPending(data) ? undefined : data?.verdict.verdict).toBe("buildable");
    });
    expect(verdictCalls).toBe(2);
  });

  it("未核验 404：静默为 {pending:true} 哨兵，不向调用方抛错（F-20）", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => (
      jsonResponse({ code: "GAP_NOT_FEASIBILITY_CHECKED", message: "尚未核验" }, 404)
    )) as unknown as typeof fetch);
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result } = renderHook(
      () => useFeasibilityVerdict(5, "g2", { poll: false }),
      { wrapper: wrapper(qc) },
    );

    await waitFor(() => expect(result.current.data).toEqual({ pending: true }));
    expect(result.current.isError).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("job failed：关闭 verdict poll，并保留后端错误供 UI 提示", async () => {
    let verdictCalls = 0;
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/gaps/g2:feasibility") && init?.method === "POST") {
        return jsonResponse({ feasibility_run_id: "91" }, 202);
      }
      if (url.endsWith("/ai/jobs/91")) {
        return jsonResponse({ id: 91, status: "failed", error: "feasibility-scout 取证失败" });
      }
      if (url.endsWith("/gaps/g2/feasibility-verdict")) {
        verdictCalls += 1;
        return jsonResponse({ code: "GAP_NOT_FEASIBILITY_CHECKED", message: "尚未核验" }, 409);
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });

    const { result } = renderHook(() => {
      const [runId, setRunId] = useState<string | null>(null);
      const mutation = useFeasibilityVerify(5);
      const jobId = runId ? Number(runId) : null;
      const job = useAiJob(5, jobId, { enabled: !!runId, pollMs: 20 });
      const failed = job.data?.status === "failed" || job.data?.status === "cancelled";
      const verdict = useFeasibilityVerdict(5, runId ? "g2" : null, { poll: !!runId && !failed, pollMs: 20 });
      return {
        job,
        verdict,
        start: async () => {
          const accepted = await mutation.mutateAsync({ gapId: "g2" });
          setRunId(accepted.feasibility_run_id);
        },
      };
    }, { wrapper: wrapper(qc) });

    await act(async () => result.current.start());
    await waitFor(() => expect(result.current.job.data?.status).toBe("failed"));
    expect(result.current.job.data?.error).toBe("feasibility-scout 取证失败");
    const callsAtFailure = verdictCalls;
    await new Promise((resolve) => setTimeout(resolve, 80));
    expect(verdictCalls).toBe(callsAtFailure);
  });
});
