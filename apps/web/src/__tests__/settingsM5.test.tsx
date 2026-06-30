/**
 * settingsM5.test.tsx — M5 设置页 + useLlmSettings + OutputView 闸门 + key 注入 测试
 *
 * 覆盖:
 *   1. useLlmSettings — localStorage 读/写/清除
 *   2. SettingsPage — 表单渲染 / 保存 / 清除
 *   3. OutputView 闸门 — 无 ready corpus 显示引导；ready 时显示导出内容
 *   4. Key 注入 — AiToolsPanel 调用 aiSummary 时带上 apiKey
 *
 * 注意: jsdom 测试环境下 localStorage 需要 mock（opaque origin 无 .clear()）
 */

import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";

// ========================================================================
// localStorage mock（jsdom opaque origin 兼容）
// ========================================================================

const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => { store[key] = value; },
    removeItem: (key: string) => { delete store[key]; },
    clear: () => { store = {}; },
    get length() { return Object.keys(store).length; },
    key: (i: number) => Object.keys(store)[i] ?? null,
  };
})();

Object.defineProperty(window, "localStorage", {
  value: localStorageMock,
  writable: true,
});

const STORAGE_KEY = "bibliocn.llm";

// ========================================================================
// 1. useLlmSettings — 纯 hook 测试
// ========================================================================

describe("useLlmSettings", () => {
  beforeEach(() => {
    localStorageMock.clear();
  });

  afterEach(() => {
    localStorageMock.clear();
  });

  it("无存储时返回默认值 (deepseek / '' / deepseek base URL / deepseek-chat)", async () => {
    const { useLlmSettings } = await import("../api/useLlmSettings");
    const { renderHook } = await import("@testing-library/react");
    const { result } = renderHook(() => useLlmSettings());
    expect(result.current.settings.provider).toBe("deepseek");
    expect(result.current.settings.apiKey).toBe("");
    expect(result.current.settings.baseUrl).toBe("https://api.deepseek.com/v1");
    expect(result.current.settings.model).toBe("deepseek-chat");
  });

  it("save() 写入 localStorage 并可读回", async () => {
    const { useLlmSettings } = await import("../api/useLlmSettings");
    const { renderHook } = await import("@testing-library/react");
    const { result } = renderHook(() => useLlmSettings());
    act(() => {
      result.current.save({
        provider: "openai",
        apiKey: "test-api-key-123",
        baseUrl: "https://api.openai.com/v1",
        model: "gpt-4o-mini",
      });
    });
    const stored = JSON.parse(localStorageMock.getItem(STORAGE_KEY) ?? "{}") as Record<string, string>;
    expect(stored.provider).toBe("openai");
    expect(stored.apiKey).toBe("test-api-key-123");
    expect(stored.baseUrl).toBe("https://api.openai.com/v1");
    expect(stored.model).toBe("gpt-4o-mini");
  });

  it("clear() 删除 localStorage 中的 key", async () => {
    localStorageMock.setItem(STORAGE_KEY, JSON.stringify({ provider: "openai", apiKey: "test-api-key-x", model: "gpt-4o-mini" }));
    const { useLlmSettings } = await import("../api/useLlmSettings");
    const { renderHook } = await import("@testing-library/react");
    const { result } = renderHook(() => useLlmSettings());
    act(() => {
      result.current.clear();
    });
    expect(localStorageMock.getItem(STORAGE_KEY)).toBeNull();
  });

  it("save() 后 settings 立即反映新值", async () => {
    const { useLlmSettings } = await import("../api/useLlmSettings");
    const { renderHook } = await import("@testing-library/react");
    const { result } = renderHook(() => useLlmSettings());
    act(() => {
      result.current.save({
        provider: "anthropic",
        apiKey: "anthropic-test-api-key",
        baseUrl: "",
        model: "claude-3-haiku-20240307",
      });
    });
    expect(result.current.settings.apiKey).toBe("anthropic-test-api-key");
    expect(result.current.settings.provider).toBe("anthropic");
  });
});

// ========================================================================
// 2. SettingsPage — 渲染 + 交互
// ========================================================================

describe("SettingsPage", () => {
  beforeEach(() => {
    localStorageMock.clear();
  });

  afterEach(() => {
    localStorageMock.clear();
  });

  async function renderSettings() {
    const { SettingsPage } = await import("../pages/SettingsPage");
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <SettingsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  it("渲染 Provider 选择 / API Key / 模型 输入框", async () => {
    await renderSettings();
    expect(screen.getByLabelText("Provider")).toBeInTheDocument();
    expect(screen.getByLabelText("API Key")).toBeInTheDocument();
    expect(screen.getAllByLabelText("Base URL")).toHaveLength(2);
    expect(screen.getByLabelText("模型")).toBeInTheDocument();
    expect(screen.getByLabelText("API Token")).toBeInTheDocument();
  });

  it("点击「保存」将表单值写入 localStorage", async () => {
    await renderSettings();
    const keyInput = screen.getByLabelText("API Key");
    fireEvent.change(keyInput, { target: { value: "test-api-key-save" } });
    fireEvent.click(screen.getByText("保存"));
    await waitFor(() => {
      const stored = JSON.parse(localStorageMock.getItem(STORAGE_KEY) ?? "{}") as Record<string, string>;
      expect(stored.apiKey).toBe("test-api-key-save");
      expect(stored.baseUrl).toBe("https://api.deepseek.com/v1");
    });
  });

  it("点击「清除」删除 localStorage 存储", async () => {
    localStorageMock.setItem(STORAGE_KEY, JSON.stringify({ provider: "deepseek", apiKey: "old-test-api-key", model: "deepseek-chat" }));
    await renderSettings();
    fireEvent.click(screen.getAllByText("清除")[0]);
    await waitFor(() => {
      expect(localStorageMock.getItem(STORAGE_KEY)).toBeNull();
    });
  });

  it("包含安全说明文字「不上传服务器数据库」", async () => {
    await renderSettings();
    expect(screen.getByText(/不上传服务端数据库/)).toBeInTheDocument();
  });
});

// ========================================================================
// 3. OutputView 闸门测试
// ========================================================================

// mock agentHooks
const mockUseProject = vi.fn();
const mockUseArtifacts = vi.fn();
const mockUsePatchArtifact = vi.fn();
const mockUseMaterializeCorpus = vi.fn();

vi.mock("../api/agentHooks", () => ({
  useProject: (...args: unknown[]) => mockUseProject(...args),
  useArtifacts: (...args: unknown[]) => mockUseArtifacts(...args),
  usePatchArtifact: (...args: unknown[]) => mockUsePatchArtifact(...args),
  useMaterializeCorpus: (...args: unknown[]) => mockUseMaterializeCorpus(...args),
  useCreateArtifact: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteArtifact: () => ({ mutateAsync: vi.fn(), isPending: false }),
  // A6: OutputView 内嵌 PrismaPanel 新增依赖 useProjectPapers（自动填充用），mock 为空语料
  useProjectPapers: () => ({ data: { papers: [] }, isLoading: false, error: null }),
}));

// mock client（避免真实网络）
vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    downloadReport: vi.fn().mockResolvedValue(undefined),
    getCite: vi.fn().mockResolvedValue({ citations: ["Author A. (2024). Title. Journal."] }),
    buildPrisma: vi.fn().mockResolvedValue({ stages: [], warnings: [] }),
    aiSummary: vi.fn().mockResolvedValue({ text: "摘要结果" }),
    aiTranslate: vi.fn().mockResolvedValue({ text: "翻译结果" }),
    aiRewrite: vi.fn().mockResolvedValue({ text: "重写结果" }),
    createAiJob: vi.fn().mockResolvedValue({
      id: 101,
      projectId: 1,
      kind: "summary",
      status: "done",
      resultText: "摘要结果",
      events: [],
      request: { kind: "summary", text: "测试摘要文本" },
    }),
    getAiJob: vi.fn().mockResolvedValue({
      id: 101,
      projectId: 1,
      kind: "summary",
      status: "done",
      resultText: "摘要结果",
      events: [],
      request: { kind: "summary", text: "测试摘要文本" },
    }),
    listAiJobs: vi.fn().mockResolvedValue({ jobs: [] }),
    pingLlm: vi.fn().mockResolvedValue({
      ok: true,
      model: "deepseek-chat",
      baseUrl: "https://api.deepseek.com/v1",
      content: "pong",
    }),
  };
});

// mock markdown
vi.mock("../lib/markdown", () => ({
  renderMarkdown: (md: string) => `<p>${md}</p>`,
}));

beforeEach(() => {
  mockUsePatchArtifact.mockReturnValue({ mutateAsync: vi.fn(), isPending: false });
  mockUseMaterializeCorpus.mockReturnValue({ mutate: vi.fn(), isPending: false, isError: false, error: null });
  mockUseArtifacts.mockReturnValue({ data: { artifacts: [] }, isLoading: false, error: null });
});

import { OutputView } from "../pages/OutputView";

function renderOutputAt(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/projects/:pid/output" element={<OutputView />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("OutputView 数据流闸门", () => {
  it("无 activeCorpus 时显示「需先构建分析语料」引导提示", () => {
    mockUseProject.mockReturnValue({
      data: {
        name: "测试项目",
        paperCount: 5,
        includedCount: 3,
        researchQuestion: null,
        activeCorpus: null,
      },
      isLoading: false,
      error: null,
    });

    renderOutputAt("/projects/1/output");
    expect(screen.getByText("需先构建分析语料")).toBeInTheDocument();
    // ready 才有的内容不显示
    expect(screen.queryByText("导出 Markdown")).toBeNull();
  });

  it("activeCorpus.status=parsing 时也显示引导提示", () => {
    mockUseProject.mockReturnValue({
      data: {
        name: "测试项目",
        paperCount: 5,
        includedCount: 3,
        activeCorpus: {
          corpusId: 1,
          rCorpusId: null,
          status: "parsing",
          stale: false,
          documentCount: 10,
          contentHash: "abc",
        },
      },
      isLoading: false,
      error: null,
    });

    renderOutputAt("/projects/1/output");
    expect(screen.getByText("需先构建分析语料")).toBeInTheDocument();
  });

  it("activeCorpus.status=ready 时渲染导出报告 + PRISMA", () => {
    mockUseProject.mockReturnValue({
      data: {
        name: "测试项目",
        paperCount: 10,
        includedCount: 8,
        activeCorpus: {
          corpusId: 1,
          rCorpusId: "r_corpus_001",
          status: "ready",
          stale: false,
          documentCount: 8,
          contentHash: "xyz",
        },
      },
      isLoading: false,
      error: null,
    });

    renderOutputAt("/projects/1/output");
    // ReportPanel 渲染 (A7: 真实 MD/HTML/DOCX 导出)
    expect(screen.getByText("导出 Markdown")).toBeInTheDocument();
    expect(screen.getByText("导出 HTML")).toBeInTheDocument();
    expect(screen.getByText("导出 DOCX")).toBeInTheDocument();
    // PRISMA
    expect(screen.getByText("PRISMA 流程图")).toBeInTheDocument();
    // 仍未支持的占位按钮 (PDF; A7 已移除 DOCX 占位, 改真实导出)
    expect(screen.getByText("导出 PDF（即将支持）")).toBeInTheDocument();
  });

  it("ready 时「导出 PDF/DOI校验」占位按钮处于禁用态", () => {
    mockUseProject.mockReturnValue({
      data: {
        name: "测试项目",
        paperCount: 10,
        includedCount: 8,
        activeCorpus: {
          corpusId: 1,
          rCorpusId: "r_corpus_001",
          status: "ready",
          stale: false,
          documentCount: 8,
          contentHash: "xyz",
        },
      },
      isLoading: false,
      error: null,
    });

    renderOutputAt("/projects/1/output");
    const pdfBtn = screen.getByText("导出 PDF（即将支持）").closest("button");
    const doiBtn = screen.getByText("DOI 校验（即将支持）").closest("button");
    expect(pdfBtn).toBeDisabled();
    expect(doiBtn).toBeDisabled();
    // A7: DOCX 已是真实导出按钮, 不再禁用
    const docxBtn = screen.getByText("导出 DOCX").closest("button");
    expect(docxBtn).not.toBeDisabled();
  });
});

// ========================================================================
// 4. Key 注入 — AiToolsPanel 调用时带 apiKey
// ========================================================================

import { AiToolsPanel } from "../components/AiToolsPanel";

// A7: 三 AI 面板统一为 apiKey prop 注入 (父组件 AnalysisView 从 useLlmSettings 读取后注入)。
describe("AiToolsPanel key 注入 (A7: prop)", () => {
  it("传入 apiKey 时，createAiJob 调用带 apiKey 参数", async () => {
    const { createAiJob } = await import("../api/client");
    vi.mocked(createAiJob).mockClear();

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <AiToolsPanel projectId="1" apiKey="test-api-key-inject" />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const textarea = screen.getByPlaceholderText("粘贴文本…");
    fireEvent.change(textarea, { target: { value: "测试摘要文本" } });
    fireEvent.click(screen.getByText("运行"));

    await waitFor(() => {
      expect(createAiJob).toHaveBeenCalledWith(
        "1",
        { kind: "summary", text: "测试摘要文本", direction: "en2zh", action: "compress" },
        "test-api-key-inject",
      );
    });
  });

  it("未传 apiKey 时，createAiJob 调用 llm 为 undefined + 显示未配置提示", async () => {
    const { createAiJob } = await import("../api/client");
    vi.mocked(createAiJob).mockClear();

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <AiToolsPanel projectId="1" />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    // 未配置 key 的温和提示 (统一 AiKeyNotice)
    expect(screen.getByText(/未配置 LLM key/)).toBeInTheDocument();

    const textarea = screen.getByPlaceholderText("粘贴文本…");
    fireEvent.change(textarea, { target: { value: "无 key 测试" } });
    fireEvent.click(screen.getByText("运行"));

    await waitFor(() => {
      expect(createAiJob).toHaveBeenCalledWith(
        "1",
        { kind: "summary", text: "无 key 测试", direction: "en2zh", action: "compress" },
        undefined,
      );
    });
  });
});
