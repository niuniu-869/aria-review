/**
 * ReviewPanel.restore.test.tsx — restore() 鲁棒性回归
 *
 * 背景：综述持久化在服务端(AI job kind=review)，前端用 localStorage 缓存 jobId。
 *   失败模式：localStorage 残留的旧 jobId 失效(DB 重置/重建项目 → getAiJob 404)时，
 *   旧实现把异常 catch 吞掉且【不回退 listAiJobs】→ 综述留白，即本案 "不显示了"。
 *
 * 期望：getAiJob 失效 → 清除坏 key 并回退 listAiJobs，显示该 (project,corpus) 最新综述，绝不留白。
 *
 * 策略：mock getAiJob/listAiJobs(不触网)；断言 .ai-review-body 出现、空态消失(对 AiMarkdown 流式稳健)。
 */
import { render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi, describe, it, expect, beforeEach } from "vitest";

const { getAiJobSpy, listAiJobsSpy } = vi.hoisted(() => ({
  getAiJobSpy: vi.fn(),
  listAiJobsSpy: vi.fn(),
}));

vi.mock("../../api/client", async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return {
    ...actual,
    getAiJob: (...a: unknown[]) => getAiJobSpy(...a),
    listAiJobs: (...a: unknown[]) => listAiJobsSpy(...a),
  };
});

import { ReviewPanel } from "../ReviewPanel";
import { ApiError } from "../../api/client";

const PID = "40";
const CORPUS = "87f38425-5745-419a-983a-c53239a839e9";
const KEY = `bibliocn.ai.review.${PID}.${CORPUS}`;

function reviewJob(id: number, text: string) {
  return { id, projectId: 40, corpusId: CORPUS, kind: "review", status: "done", resultText: text, events: [], request: {} };
}

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ReviewPanel projectId={PID} corpusId={CORPUS} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  localStorage.clear();
  getAiJobSpy.mockReset();
  listAiJobsSpy.mockReset();
});

describe("ReviewPanel restore 鲁棒性", () => {
  it("localStorage 旧 jobId 失效(getAiJob 404) → 回退 listAiJobs 显示最新综述，不留白", async () => {
    localStorage.setItem(KEY, "999999"); // 失效 id(DB 已重建)
    getAiJobSpy.mockRejectedValue(new ApiError("AI_JOB_NOT_FOUND", 404, "not found"));
    listAiJobsSpy.mockResolvedValue({ jobs: [reviewJob(30, "盈余管理综述正文") ] });

    const { container, queryByText } = renderPanel();

    await waitFor(() => {
      expect(container.querySelector(".ai-review-body")).toBeTruthy();
    });
    // 不应停留在空态提示
    expect(queryByText(/填写研究主题/)).toBeNull();
    // 坏 key 被有效 id 覆盖
    expect(localStorage.getItem(KEY)).toBe("30");
    // 确实尝试过 getAiJob(失效) 后回退 listAiJobs
    expect(getAiJobSpy).toHaveBeenCalled();
    expect(listAiJobsSpy).toHaveBeenCalled();
  });

  it("无 localStorage → 直接 listAiJobs 取最新综述(回归保护)", async () => {
    listAiJobsSpy.mockResolvedValue({ jobs: [reviewJob(30, "盈余管理综述正文") ] });
    const { container } = renderPanel();
    await waitFor(() => {
      expect(container.querySelector(".ai-review-body")).toBeTruthy();
    });
    expect(getAiJobSpy).not.toHaveBeenCalled();
    expect(listAiJobsSpy).toHaveBeenCalled();
  });

  it("listAiJobs 也无结果 → 优雅空态(不报错)", async () => {
    localStorage.setItem(KEY, "999999");
    getAiJobSpy.mockRejectedValue(new ApiError("AI_JOB_NOT_FOUND", 404, "not found"));
    listAiJobsSpy.mockResolvedValue({ jobs: [] });
    const { findByText } = renderPanel();
    expect(await findByText(/填写研究主题/)).toBeInTheDocument();
  });
});
