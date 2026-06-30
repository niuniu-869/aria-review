/**
 * corpusM2.test.tsx — M2 activeCorpus 模型 + materialize API 客户端单测
 *
 * 覆盖：
 * 1. materializeCorpus 函数：发送 POST 并返回 CorpusMaterializeResult
 * 2. materializeCorpus 遇到 422（EMPTY_INCLUDED）正确抛出 ApiError
 * 3. StageBar：有 ready activeCorpus 时「分析」阶段标为 done
 * 4. StageBar：无 activeCorpus 时「分析」阶段为 active（当前进行中）
 * 5. AnalysisView：无 activeCorpus 时渲染「构建分析语料」按钮
 * 6. AnalysisView：stale=true 时渲染「纳入集已变，点此重算」按钮
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { materializeCorpus } from "../api/client";
import { StageBar } from "../components/shell/StageBar";
import { AnalysisView } from "../pages/AnalysisView";
import type { ActiveCorpus } from "../api/agentHooks";

// ---------------------------------------------------------------------------
// mock fetch helper
// ---------------------------------------------------------------------------
function mockFetch(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    statusText: "x",
    json: async () => body,
  } as unknown as Response);
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// mock agentHooks.useProject — AnalysisView 用 useProject(pidNum) 取 data
// ---------------------------------------------------------------------------
const { mockUseProject, mockUseMaterializeCorpus } = vi.hoisted(() => ({
  mockUseProject: vi.fn(),
  mockUseMaterializeCorpus: vi.fn(),
}));

vi.mock("../api/agentHooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/agentHooks")>();
  return {
    ...actual,
    useProject: mockUseProject,
    useMaterializeCorpus: mockUseMaterializeCorpus,
  };
});

// ---------------------------------------------------------------------------
// 工具：带 QueryClient + Router 渲染 AnalysisView（M3: 路由为 /analysis/:view）
// ---------------------------------------------------------------------------
function renderAnalysis(pid = "5") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/projects/${pid}/analysis/overview`]}>
        <Routes>
          <Route path="/projects/:pid/analysis/:view" element={<AnalysisView />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// 1. materializeCorpus 函数：发 POST，返回 CorpusMaterializeResult
// ---------------------------------------------------------------------------
describe("materializeCorpus API client", () => {
  it("发 POST /projects/{pid}/corpus/materialize 并返回结果", async () => {
    const mockBody = {
      corpusId: 42,
      rCorpusId: "abc-123",
      status: "ready",
      documentCount: 5,
      contentHash: "deadbeef",
    };
    const f = mockFetch(200, mockBody);
    vi.stubGlobal("fetch", f);

    const result = await materializeCorpus(7);
    expect(result.corpusId).toBe(42);
    expect(result.rCorpusId).toBe("abc-123");
    expect(result.status).toBe("ready");

    const [url, init] = f.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/projects/7/corpus/materialize");
    expect(init.method).toBe("POST");
  });

  it("422 EMPTY_INCLUDED 时抛出 ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(422, { code: "EMPTY_INCLUDED", message: "没有 included 论文" }),
    );
    await expect(materializeCorpus(3)).rejects.toMatchObject({
      code: "EMPTY_INCLUDED",
      status: 422,
    });
  });
});

// ---------------------------------------------------------------------------
// 2. StageBar：activeCorpus ready → 分析阶段 done
// ---------------------------------------------------------------------------
describe("StageBar stage progression", () => {
  // A8: StageBar 升级为可点击导航（用 useNavigate/useParams），需 Router 上下文。
  function renderStageBar(stats: Parameters<typeof StageBar>[0]["stats"]) {
    return render(
      <MemoryRouter initialEntries={["/projects/5"]}>
        <Routes>
          <Route path="/projects/:pid/*" element={<StageBar stats={stats} />} />
        </Routes>
      </MemoryRouter>,
    );
  }

  it("无 activeCorpus 时 '分析' 为 active（有 included 文献但无 corpus）", () => {
    const stats = { paperCount: 3, includedCount: 2, activeCorpus: null };
    renderStageBar(stats);
    // 「分析」index=2 应为 active（非 done）
    const steps = document.querySelectorAll(".stage-step");
    // 前 2 个为 done，第 3 个（index=2，「分析」）为 active
    expect(steps[2].classList.contains("active")).toBe(true);
    expect(steps[2].classList.contains("done")).toBe(false);
  });

  it("activeCorpus ready 时 '分析' 为 done", () => {
    const ac: ActiveCorpus = {
      corpusId: 1, rCorpusId: "r-id", status: "ready",
      documentCount: 2, contentHash: "abc", stale: false,
    };
    const stats = { paperCount: 3, includedCount: 2, activeCorpus: ac };
    renderStageBar(stats);
    const steps = document.querySelectorAll(".stage-step");
    // index=2（「分析」）应为 done
    expect(steps[2].classList.contains("done")).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// 3. AnalysisView：无 activeCorpus 渲染「构建分析语料」按钮
// ---------------------------------------------------------------------------
describe("AnalysisView corpus status UI", () => {
  const mockMutate = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    // useMaterializeCorpus 返回空状态（未触发）
    mockUseMaterializeCorpus.mockReturnValue({
      mutate: mockMutate,
      isPending: false,
      isError: false,
      error: null,
    });
  });

  it("无 activeCorpus 时渲染「构建分析语料」按钮", () => {
    mockUseProject.mockReturnValue({ data: { activeCorpus: null } });
    renderAnalysis();
    expect(screen.getByRole("button", { name: /构建分析语料/ })).toBeInTheDocument();
  });

  it("点击「构建分析语料」按钮调用 mutate", async () => {
    mockUseProject.mockReturnValue({ data: { activeCorpus: null } });
    renderAnalysis();
    fireEvent.click(screen.getByRole("button", { name: /构建分析语料/ }));
    await waitFor(() => expect(mockMutate).toHaveBeenCalledTimes(1));
  });

  it("stale=true 时渲染 StaleBar 警告条及「立即重算」按钮（M3）", () => {
    const ac: ActiveCorpus = {
      corpusId: 1, rCorpusId: "r-id", status: "ready",
      documentCount: 2, contentHash: "abc", stale: true,
    };
    mockUseProject.mockReturnValue({ data: { activeCorpus: ac } });
    renderAnalysis();
    // M3 stale 警告通过 StaleBar 显示，按钮名称为「立即重算」
    expect(screen.getByRole("button", { name: /立即重算/ })).toBeInTheDocument();
    // 警告条文字包含「纳入集已变更」
    expect(screen.getByText(/纳入集已变更/)).toBeInTheDocument();
  });

  it("stale=false 时不渲染「立即重算」按钮", () => {
    const ac: ActiveCorpus = {
      corpusId: 1, rCorpusId: "r-id", status: "ready",
      documentCount: 2, contentHash: "abc", stale: false,
    };
    mockUseProject.mockReturnValue({ data: { activeCorpus: ac } });
    renderAnalysis();
    expect(screen.queryByRole("button", { name: /立即重算/ })).toBeNull();
  });

  it("ready 且 stale=false 时渲染 AnalysisFrame 标题（领域概览）", () => {
    const ac: ActiveCorpus = {
      corpusId: 1, rCorpusId: "my-corpus-id-99", status: "ready",
      documentCount: 7, contentHash: "abc", stale: false,
    };
    mockUseProject.mockReturnValue({ data: { activeCorpus: ac } });
    renderAnalysis();
    // M3: ready 时渲染 AnalysisFrame，标题行包含「领域概览」
    // 用 getAllByText 避免 sidebar 和 frame 标题重复匹配问题
    const matches = screen.getAllByText("领域概览");
    expect(matches.length).toBeGreaterThanOrEqual(1);
  });
});
