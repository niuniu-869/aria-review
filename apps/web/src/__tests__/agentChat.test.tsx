/**
 * agentChat.test.tsx — 验证 AgentChat + RunTimeline SSE 消费 (P1-10)
 *
 * 策略:
 *   - 用 vi.hoisted() 声明 mock 函数，保证在 vi.mock hoisting 后可被访问;
 *   - streamAgentRun mock 立即调用所有 handlers, 模拟完整事件序列;
 *   - 断言 RunTimeline 渲染了工具调用卡片和最终输出。
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { ReactElement } from "react";
import type { AgentRunHandlers } from "../api/client";

// ---- hoisted mock 函数声明（在 vi.mock 提升前就存在）----
const { mockCreateRun, mockStreamAgentRun, mockSciverseSettings } = vi.hoisted(() => ({
  mockCreateRun: vi.fn(),
  mockStreamAgentRun: vi.fn(),
  mockSciverseSettings: {
    apiToken: "",
    baseUrl: "https://api.sciverse.space",
  },
}));

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    createRun: mockCreateRun,
    streamAgentRun: mockStreamAgentRun,
  };
});

vi.mock("../api/useSciverseSettings", () => ({
  useSciverseSettings: () => ({
    settings: mockSciverseSettings,
    save: vi.fn(),
    clear: vi.fn(),
  }),
}));

// ---- mock markdown (避免 DOMPurify 在 jsdom 中的警告) ----
vi.mock("../lib/markdown", () => ({
  renderMarkdown: (md: string) => `<p>${md}</p>`,
}));

import { AgentChat } from "../components/AgentChat";
import type {
  AgentRunStartEvent,
  AgentToolsStartEvent,
  AgentRoundCompleteEvent,
  AgentRunCompleteEvent,
} from "../api/client";

// 模拟事件序列
const RUN_START: AgentRunStartEvent = {
  type: "run_start", max_rounds: 5, model: "claude-3-5-sonnet", seq: 0,
};
const TOOLS_START: AgentToolsStartEvent = {
  type: "tools_start", round: 1,
  thinking: "正在分析文献库…",
  tool_calls: [
    { id: "tc1", name: "search_papers", args_preview: '{"query":"SLR"}' },
  ],
  seq: 2,
};
const ROUND_COMPLETE: AgentRoundCompleteEvent = {
  type: "round_complete", round: 1, thinking: "", tool_calls: [],
  tool_results: [
    { tool_id: "tc1", action: "search_papers", success: true, summary: "找到 42 篇文献" },
  ],
  is_final: true, seq: 3,
};
const RUN_COMPLETE: AgentRunCompleteEvent = {
  type: "run_complete", status: "done",
  final_output: "## 综述结论\n本研究共纳入 42 篇文献。",
  seq: 4,
};

function renderWithQueryClient(ui: ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

function renderWithRouter(ui: ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AgentChat + RunTimeline", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSciverseSettings.apiToken = "";
    mockSciverseSettings.baseUrl = "https://api.sciverse.space";

    mockCreateRun.mockResolvedValue({
      runId: "run-123",
      projectId: 1,
      status: "running",
    });

    // streamAgentRun: 同步调用 handlers，然后 resolve
    mockStreamAgentRun.mockImplementation(
      async (_pid: number, _rid: string, _opts: unknown, handlers: AgentRunHandlers) => {
        handlers.onRunStart?.(RUN_START);
        handlers.onToolsStart?.(TOOLS_START);
        handlers.onRoundComplete?.(ROUND_COMPLETE);
        handlers.onRunComplete?.(RUN_COMPLETE);
      },
    );
  });

  it("提交后渲染运行开始卡片", async () => {
    renderWithQueryClient(<AgentChat projectId={1} />);

    const textarea = screen.getByLabelText("Agent 指令输入");
    fireEvent.change(textarea, { target: { value: "分析 SLR 文献" } });

    const sendBtn = screen.getByRole("button", { name: /发送/ });
    fireEvent.click(sendBtn);

    await waitFor(() => {
      expect(screen.getByText(/运行开始/)).toBeInTheDocument();
    });
  });

  it("渲染工具调用卡片 — 包含工具名 search_papers", async () => {
    renderWithQueryClient(<AgentChat projectId={1} />);

    fireEvent.change(screen.getByLabelText("Agent 指令输入"), {
      target: { value: "分析文献" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发送/ }));

    await waitFor(() => {
      // search_papers 出现在工具调用 (.tl-tool-name) 和结果 (.tl-result-action) 两处
      const matches = screen.getAllByText("search_papers");
      expect(matches.length).toBeGreaterThanOrEqual(1);
      // 其中一个来自 tools_start 卡片的 .tl-tool-name
      expect(document.querySelector(".tl-tool-name")).not.toBeNull();
    });
  });

  it("渲染工具结果摘要 — '找到 42 篇文献'", async () => {
    renderWithQueryClient(<AgentChat projectId={1} />);

    fireEvent.change(screen.getByLabelText("Agent 指令输入"), {
      target: { value: "分析文献" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发送/ }));

    await waitFor(() => {
      expect(screen.getByText("找到 42 篇文献")).toBeInTheDocument();
    });
  });

  it("渲染最终输出 run_complete 卡片", async () => {
    renderWithQueryClient(<AgentChat projectId={1} />);

    fireEvent.change(screen.getByLabelText("Agent 指令输入"), {
      target: { value: "分析文献" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发送/ }));

    await waitFor(() => {
      // tl-label 显示"运行完成 · done"
      expect(screen.getByText(/运行完成/)).toBeInTheDocument();
    });
  });

  it("最终输出渲染了 markdown 内容", async () => {
    renderWithQueryClient(<AgentChat projectId={1} />);

    fireEvent.change(screen.getByLabelText("Agent 指令输入"), {
      target: { value: "分析文献" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发送/ }));

    await waitFor(() => {
      // renderMarkdown mock 把内容包在 <p> 里，innerHTML 中包含原始文本
      const finalCard = document.querySelector(".tl-final-output .md");
      expect(finalCard).not.toBeNull();
      expect(finalCard?.innerHTML).toContain("综述结论");
    });
  });

  it("运行完成后发送按钮重新启用", async () => {
    renderWithQueryClient(<AgentChat projectId={1} />);

    const textarea = screen.getByLabelText("Agent 指令输入");
    fireEvent.change(textarea, { target: { value: "分析文献" } });

    const sendBtn = screen.getByRole("button", { name: /发送/ });
    fireEvent.click(sendBtn);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /发送/ })).not.toBeDisabled();
    });
  });

  it("调用了 createRun 和 streamAgentRun 并传入正确参数", async () => {
    renderWithQueryClient(<AgentChat projectId={42} />);

    fireEvent.change(screen.getByLabelText("Agent 指令输入"), {
      target: { value: "测试指令" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发送/ }));

    await waitFor(() => {
      expect(mockCreateRun).toHaveBeenCalledWith(
        42,
        { prompt: "测试指令", autoConfirm: true, entry: "search" },
        expect.objectContaining({
          baseUrl: "https://api.deepseek.com/v1",
          model: "deepseek-chat",
        }),
        expect.objectContaining({
          baseUrl: "https://api.sciverse.space",
        }),
      );
      expect(mockStreamAgentRun).toHaveBeenCalledWith(
        42, "run-123", expect.anything(), expect.anything(),
      );
    });
  });

  it("P0 三入口：切到「综述撰写」后 createRun 传 entry=review", async () => {
    renderWithQueryClient(<AgentChat projectId={42} />);

    // 点综述入口 tab（默认是检索建库）
    fireEvent.click(screen.getByRole("tab", { name: /综述撰写/ }));
    fireEvent.change(screen.getByLabelText("Agent 指令输入"), {
      target: { value: "写综述" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发送/ }));

    await waitFor(() => {
      expect(mockCreateRun).toHaveBeenCalledWith(
        42,
        { prompt: "写综述", autoConfirm: true, entry: "review" },
        expect.anything(),
        expect.anything(),
      );
    });
  });

  it("gap 入口空库时显示引导卡并在发送前拦截", () => {
    renderWithRouter(
      <AgentChat
        projectId={42}
        readiness={{
          stage: "no_papers",
          label: "项目还没有文献",
          actionText: "去检索或导入文献",
          actionHref: "/projects/42/library",
        }}
      />,
    );

    fireEvent.click(screen.getByRole("tab", { name: /研究空白/ }));
    fireEvent.change(screen.getByLabelText("Agent 指令输入"), { target: { value: "找研究空白" } });
    fireEvent.keyDown(screen.getByLabelText("Agent 指令输入"), { key: "Enter", ctrlKey: true });

    expect(screen.getByRole("alert")).toHaveTextContent("项目还没有文献");
    expect(screen.getByRole("button", { name: /发送/ })).toBeDisabled();
    expect(mockCreateRun).not.toHaveBeenCalled();
  });

  it("search 入口空库时不拦截建库请求", async () => {
    renderWithRouter(
      <AgentChat
        projectId={42}
        readiness={{
          stage: "no_papers",
          label: "项目还没有文献",
          actionText: "去检索或导入文献",
          actionHref: "/projects/42/library",
        }}
      />,
    );

    fireEvent.change(screen.getByLabelText("Agent 指令输入"), { target: { value: "检索联邦学习" } });
    fireEvent.click(screen.getByRole("button", { name: /发送/ }));

    expect(screen.queryByText("项目还没有文献")).not.toBeInTheDocument();
    await waitFor(() => expect(mockCreateRun).toHaveBeenCalled());
  });

  it("review 入口无可读全文时拦截发送", () => {
    renderWithRouter(
      <AgentChat
        projectId={42}
        readiness={{
          stage: "no_fulltext",
          label: "已纳入文献缺少可读全文",
          actionText: "去补充全文",
          actionHref: "/projects/42/library",
        }}
      />,
    );

    fireEvent.click(screen.getByRole("tab", { name: /综述撰写/ }));
    fireEvent.change(screen.getByLabelText("Agent 指令输入"), { target: { value: "写综述" } });

    expect(screen.getByRole("alert")).toHaveTextContent("综述依赖已纳入且可读的全文");
    expect(screen.getByRole("button", { name: /发送/ })).toBeDisabled();
  });

  it("gap 入口无可读全文时仅软提示，不拦截发送", async () => {
    renderWithRouter(
      <AgentChat
        projectId={42}
        readiness={{
          stage: "no_fulltext",
          label: "已纳入文献缺少可读全文",
          actionText: "去补充全文",
          actionHref: "/projects/42/library",
        }}
      />,
    );

    fireEvent.click(screen.getByRole("tab", { name: /研究空白/ }));
    fireEvent.change(screen.getByLabelText("Agent 指令输入"), { target: { value: "讨论研究空白" } });
    fireEvent.click(screen.getByRole("button", { name: /发送/ }));

    expect(screen.getByRole("status")).toHaveTextContent("仍可继续讨论研究空白");
    await waitFor(() => expect(mockCreateRun).toHaveBeenCalled());
  });

  it("统计未加载时 review 入口不显示卡片也不拦截", async () => {
    renderWithRouter(<AgentChat projectId={42} />);

    fireEvent.click(screen.getByRole("tab", { name: /综述撰写/ }));
    fireEvent.change(screen.getByLabelText("Agent 指令输入"), { target: { value: "写综述" } });
    fireEvent.click(screen.getByRole("button", { name: /发送/ }));

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await waitFor(() => expect(mockCreateRun).toHaveBeenCalled());
  });

  it("Sciverse 检索选项更新后提交使用最新值", async () => {
    mockSciverseSettings.apiToken = "old-token";
    mockSciverseSettings.baseUrl = "https://old.sciverse.test";
    // rerender 会整体替换根节点，须显式携带同一 provider（AgentChat 依赖 useQueryClient）
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const { rerender } = render(
      <QueryClientProvider client={qc}><AgentChat projectId={42} /></QueryClientProvider>,
    );

    fireEvent.change(screen.getByLabelText("Agent 指令输入"), {
      target: { value: "测试指令" },
    });

    mockSciverseSettings.apiToken = "new-token";
    mockSciverseSettings.baseUrl = "https://new.sciverse.test";
    rerender(
      <QueryClientProvider client={qc}><AgentChat projectId={42} /></QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: /发送/ }));

    await waitFor(() => {
      expect(mockCreateRun).toHaveBeenCalledWith(
        42,
        { prompt: "测试指令", autoConfirm: true, entry: "search" },
        expect.anything(),
        expect.objectContaining({
          apiToken: "new-token",
          baseUrl: "https://new.sciverse.test",
        }),
      );
    });
  });

  it("同一 run 内多轮检索结果会累计去重展示，而不是只保留最后 20 篇", async () => {
    mockStreamAgentRun.mockImplementation(
      async (_pid: number, _rid: string, _opts: unknown, handlers: AgentRunHandlers) => {
        handlers.onRunStart?.(RUN_START);
        handlers.onSearchResults?.({
          type: "search_results",
          query: "query one",
          candidates: [
            { candidate_id: "a", openalexId: "W1", title: "Paper One", source: "openalex" },
            { candidate_id: "b", openalexId: "W2", title: "Paper Two", source: "openalex" },
          ],
          seq: 1,
        });
        handlers.onSearchResults?.({
          type: "search_results",
          query: "query two",
          candidates: [
            { candidate_id: "b2", openalexId: "W2", title: "Paper Two Updated", source: "openalex" },
            { candidate_id: "c", openalexId: "W3", title: "Paper Three", source: "openalex" },
          ],
          seq: 2,
        });
        handlers.onRunComplete?.(RUN_COMPLETE);
      },
    );

    renderWithQueryClient(<AgentChat projectId={1} />);
    fireEvent.change(screen.getByLabelText("Agent 指令输入"), {
      target: { value: "检索慢病管理文献" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发送/ }));

    await waitFor(() => {
      expect(screen.getByText(/累计/)).toBeInTheDocument();
      expect(screen.getByText(/去重后/)).toBeInTheDocument();
    });
    expect(screen.getByText("Paper One")).toBeInTheDocument();
    expect(screen.getByText("Paper Two Updated")).toBeInTheDocument();
    expect(screen.getByText("Paper Three")).toBeInTheDocument();
    expect(document.querySelectorAll(".candidate-item")).toHaveLength(3);
  });

  // 修复3: 流不完整（无终态事件）→ onError 携带 STREAM_INCOMPLETE 消息，UI 显示错误提示
  it("流断开无终态事件时显示错误提示", async () => {
    // streamAgentRun 调用 onError 模拟 STREAM_INCOMPLETE
    mockStreamAgentRun.mockImplementation(
      async (_pid: number, _rid: string, _opts: unknown, handlers: AgentRunHandlers) => {
        handlers.onRunStart?.(RUN_START);
        // 不调用 onRunComplete，模拟断流后 client 侧触发 onError
        handlers.onError?.({ type: "error", error: "连接中断，运行可能未完成", seq: 1 });
      },
    );

    renderWithQueryClient(<AgentChat projectId={1} />);
    fireEvent.change(screen.getByLabelText("Agent 指令输入"), {
      target: { value: "分析文献" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发送/ }));

    await waitFor(() => {
      expect(screen.getByText(/连接中断/)).toBeInTheDocument();
    });
  });

  // 修复4: 新 run 时 ErrorBoundary 重置（runCount key 递增）——验证第二次提交时 timeline 仍能渲染
  it("第二次提交时 ErrorBoundary 重置并能正常渲染", async () => {
    renderWithQueryClient(<AgentChat projectId={1} />);

    const submit = async () => {
      fireEvent.change(screen.getByLabelText("Agent 指令输入"), {
        target: { value: "分析文献" },
      });
      fireEvent.click(screen.getByRole("button", { name: /发送/ }));
      await waitFor(() => expect(screen.getByText(/运行完成/)).toBeInTheDocument());
    };

    await submit();
    await submit();
    // 第二次仍能看到运行完成卡片
    expect(screen.getByText(/运行完成/)).toBeInTheDocument();
  });
});
