/**
 * confirmCard.test.tsx — 验证 ConfirmCard 写确认卡片 + AgentChat 确认流接线 (P2-3)
 *
 * 策略:
 *   - 用 vi.hoisted() 声明 mock 函数, 保证在 vi.mock hoisting 后可被访问;
 *   - streamAgentRun mock 触发 onToolConfirmRequired, 模拟后端发确认信号(流仍打开);
 *   - 断言渲染出 ConfirmCard, 点"批准"调用 confirmRun(1,"7",{toolCallId,decision})。
 *   - 再补 ConfirmCard 隔离单元测试(onApprove/onReject 回调触发)。
 */
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";
import type { AgentRunHandlers, AgentToolConfirmRequiredEvent } from "../api/client";

// ---- hoisted mock 函数声明(在 vi.mock 提升前就存在)----
const { mockCreateRun, mockStreamAgentRun, mockConfirmRun } = vi.hoisted(() => ({
  mockCreateRun: vi.fn(),
  mockStreamAgentRun: vi.fn(),
  mockConfirmRun: vi.fn(),
}));

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    createRun: mockCreateRun,
    streamAgentRun: mockStreamAgentRun,
    confirmRun: mockConfirmRun,
  };
});

// ---- mock markdown (避免 DOMPurify 在 jsdom 中的警告) ----
vi.mock("../lib/markdown", () => ({
  renderMarkdown: (md: string) => `<p>${md}</p>`,
}));

import { AgentChat } from "../components/AgentChat";
import { ConfirmCard } from "../components/ConfirmCard";

// AgentChat 内部使用 useQueryClient（F-21 入口切换失效项目查询），渲染需包 provider
function renderWithQueryClient(ui: ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

const CONFIRM_EVT: AgentToolConfirmRequiredEvent = {
  type: "tool_confirm_required",
  toolCallId: "c1",
  toolId: "library",
  action: "add",
  argsPreview: '{"paper_id":1}',
  seq: 3,
};

describe("ConfirmCard 单元", () => {
  it("渲染 toolId/action/argsPreview, 点批准/拒绝触发回调", () => {
    const onApprove = vi.fn();
    const onReject = vi.fn();
    render(
      <ConfirmCard
        toolId="library"
        action="add"
        argsPreview='{"paper_id":1}'
        onApprove={onApprove}
        onReject={onReject}
      />,
    );

    expect(screen.getByText(/library/)).toBeInTheDocument();
    expect(screen.getByText(/add/)).toBeInTheDocument();
    expect(screen.getByText(/paper_id/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /批准/ }));
    expect(onApprove).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: /拒绝/ }));
    expect(onReject).toHaveBeenCalledTimes(1);
  });

  it("pending 时按钮禁用", () => {
    render(
      <ConfirmCard
        toolId="library"
        action="add"
        argsPreview="{}"
        pending
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /批准/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /拒绝/ })).toBeDisabled();
  });
});

describe("AgentChat 确认流", () => {
  beforeEach(() => {
    vi.clearAllMocks();

    mockCreateRun.mockResolvedValue({
      runId: "7",
      projectId: 1,
      status: "running",
    });
    mockConfirmRun.mockResolvedValue({ status: "approved" });

    // streamAgentRun: 触发确认信号(流仍打开, 不发终态)
    mockStreamAgentRun.mockImplementation(
      async (_pid: number, _rid: string, _opts: unknown, handlers: AgentRunHandlers) => {
        handlers.onToolConfirmRequired?.(CONFIRM_EVT);
      },
    );
  });

  // 关闭自动确认开关 → 走确认流
  const turnOffAutoConfirm = () => {
    const cb = screen.getByLabelText(/自动确认写操作/);
    fireEvent.click(cb);
  };

  it("收到确认信号后渲染 ConfirmCard", async () => {
    renderWithQueryClient(<AgentChat projectId={1} />);
    turnOffAutoConfirm();

    fireEvent.change(screen.getByLabelText("Agent 指令输入"), {
      target: { value: "把第1篇加入文献库" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发送/ }));

    await waitFor(() => {
      expect(screen.getByText(/library/)).toBeInTheDocument();
      expect(screen.getByText(/add/)).toBeInTheDocument();
      expect(screen.getByText(/paper_id/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /批准/ })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /拒绝/ })).toBeInTheDocument();
    });
  });

  it("点批准调用 confirmRun(1,'7',{toolCallId:'c1',decision:'approve'})", async () => {
    renderWithQueryClient(<AgentChat projectId={1} />);
    turnOffAutoConfirm();

    fireEvent.change(screen.getByLabelText("Agent 指令输入"), {
      target: { value: "把第1篇加入文献库" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发送/ }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /批准/ })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /批准/ }));

    await waitFor(() => {
      expect(mockConfirmRun).toHaveBeenCalledWith(1, "7", {
        toolCallId: "c1",
        decision: "approve",
      });
    });
  });

  it("点拒绝调用 confirmRun decision:'reject', 之后确认卡消失", async () => {
    renderWithQueryClient(<AgentChat projectId={1} />);
    turnOffAutoConfirm();

    fireEvent.change(screen.getByLabelText("Agent 指令输入"), {
      target: { value: "把第1篇加入文献库" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发送/ }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /拒绝/ })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /拒绝/ }));

    await waitFor(() => {
      expect(mockConfirmRun).toHaveBeenCalledWith(1, "7", {
        toolCallId: "c1",
        decision: "reject",
      });
      expect(screen.queryByRole("button", { name: /批准/ })).not.toBeInTheDocument();
    });
  });

  it("顺序确认竞态: 批准#1 时#2 已到达, resolve 后#2 确认卡仍在(codex P1)", async () => {
    // 捕获 handlers 以便手动触发第二个确认信号
    let captured: AgentRunHandlers | null = null;
    mockStreamAgentRun.mockImplementation(
      async (_pid: number, _rid: string, _opts: unknown, handlers: AgentRunHandlers) => {
        captured = handlers;
        handlers.onToolConfirmRequired?.(CONFIRM_EVT); // #1 (c1)
      },
    );
    // confirmRun 用可控 deferred: 在 resolve 前注入 #2
    let resolveConfirm: (v: { status: string }) => void = () => {};
    mockConfirmRun.mockImplementation(
      () => new Promise<{ status: string }>((res) => { resolveConfirm = res; }),
    );

    renderWithQueryClient(<AgentChat projectId={1} />);
    turnOffAutoConfirm();
    fireEvent.change(screen.getByLabelText("Agent 指令输入"), {
      target: { value: "连续两个写操作" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发送/ }));

    await waitFor(() => expect(screen.getByText(/add/)).toBeInTheDocument()); // #1
    fireEvent.click(screen.getByRole("button", { name: /批准/ }));            // 批准#1(confirmRun pending)

    // #2 在 confirmRun resolve 前到达(同一打开的流)
    const EVT2: AgentToolConfirmRequiredEvent = {
      type: "tool_confirm_required", toolCallId: "c2", toolId: "library",
      action: "tag", argsPreview: '{"tag":"核心"}', seq: 5,
    };
    await act(async () => {
      captured!.onToolConfirmRequired?.(EVT2);   // #2 在 resolve 前到达
      resolveConfirm({ status: "approved" });    // #1 confirm 此刻才 resolve
    });

    // 修复后: 清空只针对 c1, #2(c2) 的确认卡应仍在(否则 run 卡住)。
    // 用 #2 独有的 argsPreview 内容"核心"定位(避免 /tag/ 同时命中 action 与 args)。
    await waitFor(() => {
      expect(screen.getByText(/核心/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /批准/ })).toBeInTheDocument();
    });
  });

  it("默认 autoConfirm:true (不动开关时) createRun 传 autoConfirm:true", async () => {
    // 默认不触发确认信号
    mockStreamAgentRun.mockImplementation(async () => {});
    renderWithQueryClient(<AgentChat projectId={1} />);

    fireEvent.change(screen.getByLabelText("Agent 指令输入"), {
      target: { value: "分析文献" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发送/ }));

    await waitFor(() => {
      expect(mockCreateRun).toHaveBeenCalledWith(
        1,
        {
          prompt: "分析文献",
          autoConfirm: true,
          entry: "search",
        },
        expect.objectContaining({
          baseUrl: "https://api.deepseek.com/v1",
          model: "deepseek-chat",
        }),
        expect.objectContaining({
          baseUrl: "https://api.sciverse.space",
        }),
      );
    });
  });

  it("关闭开关后 createRun 传 autoConfirm:false", async () => {
    renderWithQueryClient(<AgentChat projectId={1} />);
    turnOffAutoConfirm();

    fireEvent.change(screen.getByLabelText("Agent 指令输入"), {
      target: { value: "分析文献" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发送/ }));

    await waitFor(() => {
      expect(mockCreateRun).toHaveBeenCalledWith(
        1,
        {
          prompt: "分析文献",
          autoConfirm: false,
          entry: "search",
        },
        expect.objectContaining({
          baseUrl: "https://api.deepseek.com/v1",
          model: "deepseek-chat",
        }),
        expect.objectContaining({
          baseUrl: "https://api.sciverse.space",
        }),
      );
    });
  });
});
