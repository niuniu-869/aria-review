/**
 * aiReportA7.test.tsx — A7 AI 工具台统一外观 + 报告增强单测
 *
 * 覆盖：
 *  - ReportPanel: 标题/作者输入透传; 章节多选勾选→透传 sections; DOCX 按钮存在;
 *                 DOCX 503(PANDOC_UNAVAILABLE) 降级隐藏 DOCX 按钮 + 提示; 引用导出保留;
 *                 全部章节取消勾选时禁用导出。
 *  - 三 AI 面板视觉统一: 共用 AiKeyNotice(未配置提示)、AiError(.state-err) 等原语;
 *                 无残留 inline #hex / crimson 颜色。
 *
 * 策略：mock downloadReport/getCite (不触网); jsdom 断言透传参数 / 降级 / 类名。
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach } from "vitest";

const { downloadReportSpy, getCiteSpy } = vi.hoisted(() => ({
  downloadReportSpy: vi.fn(),
  getCiteSpy: vi.fn(),
}));

vi.mock("../api/client", async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return {
    ...actual,
    downloadReport: (...a: unknown[]) => downloadReportSpy(...a),
    getCite: (...a: unknown[]) => getCiteSpy(...a),
  };
});

import { ReportPanel } from "../components/ReportPanel";
import { ApiError } from "../api/client";

beforeEach(() => {
  downloadReportSpy.mockReset();
  getCiteSpy.mockReset();
  downloadReportSpy.mockResolvedValue(undefined);
  getCiteSpy.mockResolvedValue({ citations: ["A. (2024). T. J."] });
});

function renderReport() {
  return render(<ReportPanel projectId="1" corpusId="r1" />);
}

describe("ReportPanel A7", () => {
  it("默认导出 MD 时透传 title/默认 sections", async () => {
    renderReport();
    fireEvent.click(screen.getByText("导出 Markdown"));
    await waitFor(() => {
      expect(downloadReportSpy).toHaveBeenCalledWith(
        "1", "r1", "md",
        expect.objectContaining({
          title: "文献计量分析报告",
          sections: ["overview", "sources", "authors", "documents", "references"],
        }),
      );
    });
  });

  it("修改标题/作者 + 取消勾选某章节 → 透传更新后的 options", async () => {
    renderReport();
    fireEvent.change(screen.getByPlaceholderText("文献计量分析报告"), { target: { value: "我的综述" } });
    fireEvent.change(screen.getByPlaceholderText("例：张三"), { target: { value: "李四" } });
    // 取消「核心作者」
    fireEvent.click(screen.getByLabelText("核心作者"));
    fireEvent.click(screen.getByText("导出 HTML"));
    await waitFor(() => {
      const call = downloadReportSpy.mock.calls[0];
      expect(call[2]).toBe("html");
      expect(call[3].title).toBe("我的综述");
      expect(call[3].author).toBe("李四");
      expect(call[3].sections).not.toContain("authors");
      expect(call[3].sections).toContain("overview");
    });
  });

  it("DOCX 按钮存在并可点击导出", async () => {
    renderReport();
    const docx = screen.getByText("导出 DOCX");
    expect(docx).toBeInTheDocument();
    fireEvent.click(docx);
    await waitFor(() => {
      expect(downloadReportSpy).toHaveBeenCalledWith("1", "r1", "docx", expect.any(Object));
    });
  });

  it("DOCX 返回 503(PANDOC_UNAVAILABLE) → 隐藏 DOCX 按钮 + 降级提示", async () => {
    downloadReportSpy.mockRejectedValueOnce(new ApiError("PANDOC_UNAVAILABLE", 503, "no pandoc"));
    renderReport();
    fireEvent.click(screen.getByText("导出 DOCX"));
    await waitFor(() => {
      expect(screen.queryByText("导出 DOCX")).not.toBeInTheDocument();
      expect(screen.getByText(/DOCX 导出不可用/)).toBeInTheDocument();
    });
    // MD/HTML 仍可用
    expect(screen.getByText("导出 Markdown")).toBeInTheDocument();
    expect(screen.getByText("导出 HTML")).toBeInTheDocument();
  });

  it("取消所有章节 → 导出按钮禁用 + 提示", () => {
    renderReport();
    ["领域概览", "核心期刊", "核心作者", "关键词 / 高被引", "参考文献"].forEach((l) =>
      fireEvent.click(screen.getByLabelText(l)),
    );
    expect(screen.getByText("导出 Markdown").closest("button")).toBeDisabled();
    expect(screen.getByText(/请至少勾选一个章节/)).toBeInTheDocument();
  });

  it("引用导出保留 (调用 getCite)", async () => {
    renderReport();
    fireEvent.click(screen.getByText("导出引用 (.txt)"));
    await waitFor(() => {
      expect(getCiteSpy).toHaveBeenCalledWith("1", "r1", "apa");
    });
  });

  it("无 #hex 残留色 (宣纸设计系统)", () => {
    const { container } = renderReport();
    expect(container.innerHTML).not.toMatch(/#[0-9a-fA-F]{6}/);
  });
});

// ========================================================================
// 三 AI 面板视觉统一 (共用 AiKeyNotice / AiError / AiEmpty / .state-err)
// ========================================================================

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ChatPanel } from "../components/ChatPanel";
import { AiToolsPanel } from "../components/AiToolsPanel";
import { ReviewPanel } from "../components/ReviewPanel";

function withQuery(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>);
}

describe("AI 面板统一外观 A7", () => {
  it("无 apiKey 时三面板均显示统一「未配置 LLM key」提示", () => {
    const c1 = withQuery(<ChatPanel projectId="1" corpusId="r1" />);
    expect(c1.getByText(/未配置 LLM key/)).toBeInTheDocument();
    c1.unmount();

    const c2 = withQuery(<AiToolsPanel projectId="1" />);
    expect(c2.getByText(/未配置 LLM key/)).toBeInTheDocument();
    c2.unmount();

    const c3 = withQuery(<ReviewPanel projectId="1" corpusId="r1" />);
    expect(c3.getByText(/未配置 LLM key/)).toBeInTheDocument();
    c3.unmount();
  });

  it("有 apiKey 时三面板均不显示提示", () => {
    const c1 = withQuery(<ChatPanel projectId="1" corpusId="r1" apiKey="test-api-key" />);
    expect(c1.queryByText(/未配置 LLM key/)).not.toBeInTheDocument();
    c1.unmount();

    const c2 = withQuery(<AiToolsPanel projectId="1" apiKey="test-api-key" />);
    expect(c2.queryByText(/未配置 LLM key/)).not.toBeInTheDocument();
    c2.unmount();

    const c3 = withQuery(<ReviewPanel projectId="1" corpusId="r1" apiKey="test-api-key" />);
    expect(c3.queryByText(/未配置 LLM key/)).not.toBeInTheDocument();
    c3.unmount();
  });

  it("三面板均渲染统一空态引导 + .ai-panel 外壳", () => {
    const c1 = withQuery(<ChatPanel projectId="1" corpusId="r1" />);
    expect(c1.container.querySelector(".ai-panel")).toBeTruthy();
    expect(c1.getByText(/开始提问/)).toBeInTheDocument();
    c1.unmount();

    const c2 = withQuery(<AiToolsPanel projectId="1" />);
    expect(c2.container.querySelector(".ai-panel")).toBeTruthy();
    expect(c2.getByText(/输入文本并点击/)).toBeInTheDocument();
    c2.unmount();

    const c3 = withQuery(<ReviewPanel projectId="1" corpusId="r1" />);
    expect(c3.container.querySelector(".ai-panel")).toBeTruthy();
    expect(c3.getByText(/填写研究主题/)).toBeInTheDocument();
    c3.unmount();
  });

  it("三面板无残留 inline #hex / crimson 色", () => {
    const c1 = withQuery(<ChatPanel projectId="1" corpusId="r1" />);
    expect(c1.container.innerHTML).not.toMatch(/#[0-9a-fA-F]{6}|crimson/);
    c1.unmount();

    const c2 = withQuery(<AiToolsPanel projectId="1" />);
    expect(c2.container.innerHTML).not.toMatch(/#[0-9a-fA-F]{6}|crimson/);
    c2.unmount();

    const c3 = withQuery(<ReviewPanel projectId="1" corpusId="r1" />);
    expect(c3.container.innerHTML).not.toMatch(/#[0-9a-fA-F]{6}|crimson/);
    c3.unmount();
  });
});
