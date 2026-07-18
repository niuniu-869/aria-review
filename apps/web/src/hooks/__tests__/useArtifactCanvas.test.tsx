import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ArtifactItem } from "../../api/agentHooks";

const { useCreateArtifactSpy } = vi.hoisted(() => ({
  useCreateArtifactSpy: vi.fn(),
}));

vi.mock("../../api/agentHooks", async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return {
    ...actual,
    useCreateArtifact: (...a: unknown[]) => useCreateArtifactSpy(...a),
  };
});

import { useArtifactCanvas } from "../useArtifactCanvas";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useArtifactCanvas", () => {
  const mutateAsync = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    useCreateArtifactSpy.mockReturnValue({ mutateAsync });
  });

  it("run_complete 创建工件；持久化失败时保留本地工件并支持重试", async () => {
    const persisted: ArtifactItem = {
      id: 9,
      projectId: 5,
      runId: 10,
      type: "review",
      title: "新综述",
      sourceEventSeq: 7,
      contentRef: "run:10",
      pinned: false,
      order: 0,
    };
    mutateAsync
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValueOnce(persisted);

    const { result } = renderHook(() => useArtifactCanvas(5), { wrapper });

    await act(async () => {
      await result.current.handleRunComplete({ runId: "10", finalOutput: "# 新综述\n正文", eventSeq: 7, status: "done" });
    });

    await waitFor(() => {
      expect(result.current.localArtifacts).toHaveLength(1);
    });
    const local = result.current.localArtifacts[0];
    expect(local.id).toBeLessThan(0);
    expect(local.title).toBe("新综述");
    expect(local.contentRef).toBe("run:10");

    await act(async () => {
      await result.current.handleRetryPersist(local);
    });

    expect(result.current.localArtifacts[0]).toEqual(persisted);
    expect(mutateAsync).toHaveBeenLastCalledWith({
      type: "review",
      title: "新综述",
      runId: 10,
      sourceEventSeq: 7,
      contentRef: "run:10",
      pinned: false,
      userAnnotation: null,
      order: 0,
    });
  });

  it("同一 runId:eventSeq 只处理一次，避免重复造工件", async () => {
    mutateAsync.mockResolvedValue({
      id: 1,
      projectId: 5,
      runId: 10,
      type: "review",
      title: "去重综述",
      pinned: false,
      order: 0,
    });

    const { result } = renderHook(() => useArtifactCanvas(5), { wrapper });

    await act(async () => {
      await result.current.handleRunComplete({ runId: "10", finalOutput: "# 去重综述", eventSeq: 1, status: "done" });
      await result.current.handleRunComplete({ runId: "10", finalOutput: "# 去重综述", eventSeq: 1, status: "done" });
    });

    expect(mutateAsync).toHaveBeenCalledTimes(1);
    expect(result.current.localArtifacts).toHaveLength(1);
  });
});
