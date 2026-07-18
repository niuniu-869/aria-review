import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentRunHandlers } from "../../api/client";

const { cancelRunSpy, confirmRunSpy, createRunSpy, streamAgentRunSpy } = vi.hoisted(() => ({
  cancelRunSpy: vi.fn(),
  confirmRunSpy: vi.fn(),
  createRunSpy: vi.fn(),
  streamAgentRunSpy: vi.fn(),
}));

vi.mock("../../api/client", async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return {
    ...actual,
    cancelRun: (...a: unknown[]) => cancelRunSpy(...a),
    confirmRun: (...a: unknown[]) => confirmRunSpy(...a),
    createRun: (...a: unknown[]) => createRunSpy(...a),
    streamAgentRun: (...a: unknown[]) => streamAgentRunSpy(...a),
  };
});

import { useAgentRunStream } from "../useAgentRunStream";

const LLM = { apiKey: "k", baseUrl: "https://llm.test", model: "m" };
const SCIVERSE = { apiToken: "s", baseUrl: "https://sciverse.test" };

describe("useAgentRunStream", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    createRunSpy.mockResolvedValue({ runId: "run-1", projectId: 1, status: "running" });
    confirmRunSpy.mockResolvedValue({ status: "ok" });
    cancelRunSpy.mockResolvedValue({ status: "cancelled" });
  });

  it("run 事件流驱动 timeline 状态、候选卡和完成回调", async () => {
    const onRunComplete = vi.fn();
    streamAgentRunSpy.mockImplementation(
      async (_pid: number, _rid: string, _opts: unknown, handlers: AgentRunHandlers) => {
        handlers.onRunStart?.({ type: "run_start", max_rounds: 5, model: "m", seq: 0 });
        handlers.onSearchResults?.({
          type: "search_results",
          query: "q1",
          candidates: [{ candidate_id: "a", openalexId: "W1", title: "Paper A" }],
          partial: true,
          partialReason: "timeout",
          seq: 1,
        });
        handlers.onSearchResults?.({
          type: "search_results",
          query: "q2",
          candidates: [
            { candidate_id: "dup", openalexId: "W1", title: "Paper A updated" },
            { candidate_id: "b", openalexId: "W2", title: "Paper B" },
          ],
          seq: 2,
        });
        handlers.onRunComplete?.({ type: "run_complete", status: "done", final_output: "## 完成", seq: 3 });
      },
    );

    const { result } = renderHook(() => useAgentRunStream({
      projectId: 1,
      llmOptions: LLM,
      sciverseOptions: SCIVERSE,
      onRunComplete,
    }));

    act(() => result.current.setPrompt("分析文献"));
    await act(async () => {
      await result.current.submit();
    });

    expect(result.current.running).toBe(false);
    expect(result.current.events.map((e) => e.type)).toEqual(["run_start", "run_complete"]);
    expect(result.current.searchResult?.candidates).toHaveLength(2);
    expect(result.current.searchResult?.partial).toBe(true);
    expect(result.current.showFollowUps).toBe(true);
    expect(onRunComplete).toHaveBeenCalledWith({ runId: "run-1", finalOutput: "## 完成", eventSeq: 3, status: "done" });
    expect(createRunSpy).toHaveBeenCalledWith(
      1,
      { prompt: "分析文献", autoConfirm: true },
      LLM,
      SCIVERSE,
    );
  });

  it("取消会 abort 本地 SSE，并后台调用 cancelRun", async () => {
    const captured: { signal?: AbortSignal } = {};
    streamAgentRunSpy.mockImplementation(
      (_pid: number, _rid: string, opts: { signal?: AbortSignal }) => {
        captured.signal = opts.signal;
        return new Promise<void>((_resolve, reject) => {
          opts.signal?.addEventListener("abort", () => {
            reject(new DOMException("Aborted", "AbortError"));
          });
        });
      },
    );

    const { result } = renderHook(() => useAgentRunStream({
      projectId: 1,
      llmOptions: LLM,
      sciverseOptions: SCIVERSE,
    }));

    act(() => result.current.setPrompt("长任务"));
    let submitPromise: Promise<void> | null = null;
    act(() => {
      submitPromise = result.current.submit();
    });
    await waitFor(() => {
      expect(result.current.rid).toBe("run-1");
    });

    await act(async () => {
      result.current.stop();
      await submitPromise;
    });

    expect(captured.signal?.aborted).toBe(true);
    expect(result.current.running).toBe(false);
    expect(result.current.events.some((e) => e.type === "cancelled")).toBe(true);
    expect(cancelRunSpy).toHaveBeenCalledWith(1, "run-1");
  });

  it("确认动作只清理当前 pendingConfirm", async () => {
    streamAgentRunSpy.mockImplementation(
      async (_pid: number, _rid: string, _opts: unknown, handlers: AgentRunHandlers) => {
        handlers.onToolConfirmRequired?.({
          type: "tool_confirm_required",
          toolCallId: "tc-1",
          toolId: "write_file",
          action: "write",
          argsPreview: "{}",
          seq: 1,
        });
      },
    );

    const { result } = renderHook(() => useAgentRunStream({
      projectId: 1,
      llmOptions: LLM,
      sciverseOptions: SCIVERSE,
    }));

    act(() => result.current.setPrompt("需要写入"));
    await act(async () => {
      await result.current.submit();
    });
    expect(result.current.pendingConfirm?.toolCallId).toBe("tc-1");

    await act(async () => {
      await result.current.decide("approve");
    });

    expect(confirmRunSpy).toHaveBeenCalledWith(1, "run-1", { toolCallId: "tc-1", decision: "approve" });
    expect(result.current.pendingConfirm).toBeNull();
  });
});
