/**
 * SearchCandidateCards.test.tsx — P2-T4 TDD 测试
 *
 * 覆盖：
 *   1. 渲染候选列表（标题/作者/年份/来源徽章/被引数）
 *   2. 默认全选；勾选/取消勾选单条
 *   3. "加入库"按钮触发回调，携带选中候选 + defaultStatus="candidate"
 *   4. "加入并纳入"按钮触发回调，携带选中候选 + defaultStatus="included"
 *   5. 入库成功后显示 imported/skipped 计数反馈
 *   6. 无候选时不渲染列表
 *   7. 按钮无障碍可键盘触发
 *   C. 候选集合变化（第二次检索）时只默认勾选新候选
 *   D. 可点击链接文案为"DOI/来源 ↗"，来源徽章为"OpenAlex"
 */
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";
import type { SearchCandidate } from "../../api/client";

// Mock useAddFromSearch hook
const mockMutateAsync = vi.fn();
const mockBackfillFulltextAsync = vi.fn();
vi.mock("../../api/agentHooks", () => ({
  useAddFromSearch: () => ({
    mutateAsync: mockMutateAsync,
    isPending: false,
  }),
  useBackfillFulltext: () => ({
    mutateAsync: mockBackfillFulltextAsync,
    isPending: false,
  }),
}));
vi.mock("../../api/useSciverseSettings", () => ({
  useSciverseSettings: () => ({
    settings: { apiToken: "tok", baseUrl: "https://api.sciverse.space" },
  }),
}));

import { SearchCandidateCards } from "../SearchCandidateCards";

const MOCK_CANDIDATES: SearchCandidate[] = [
  {
    candidate_id: "W1111",
    openalexId: "W1111",
    title: "Analyst Forecast Accuracy and IPO Underpricing",
    authors: ["Zhang Wei", "Li Ming"],
    year: 2022,
    doi: "10.1016/j.example.2022.01",
    containerTitle: "Journal of Finance",
    url: "https://openalex.org/W1111",
    abstract: "This paper examines analyst forecasts during IPO periods.",
    citedByCount: 47,
    source: "openalex",
    publicationDate: "2022-03-15",
  },
  {
    candidate_id: "W2222",
    openalexId: "W2222",
    title: "Earnings Management and Analyst Forecast Dispersion",
    authors: ["Chen Jing"],
    year: 2021,
    doi: "10.2308/ar-2021-000",
    containerTitle: "Accounting Review",
    url: "https://openalex.org/W2222",
    abstract: "We study how earnings management affects forecast dispersion.",
    citedByCount: 23,
    source: "openalex",
    publicationDate: "2021-08-01",
  },
];

describe("SearchCandidateCards", () => {
  beforeEach(() => {
    mockMutateAsync.mockReset();
    mockBackfillFulltextAsync.mockReset();
    mockMutateAsync.mockResolvedValue({ imported: 2, skipped: 0, failed: [], failedCount: 0, paperIds: [101, 102] });
    mockBackfillFulltextAsync.mockResolvedValue({
      total: 0,
      fetched: 0,
      failed: [],
      skipped: 0,
      remaining: 0,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("渲染候选列表：标题、作者、年份、来源徽章、被引数", () => {
    render(<SearchCandidateCards projectId={5} candidates={MOCK_CANDIDATES} />);

    // 标题
    expect(screen.getByText(/Analyst Forecast Accuracy/)).toBeInTheDocument();
    expect(screen.getByText(/Earnings Management/)).toBeInTheDocument();

    // 作者
    expect(screen.getByText(/Zhang Wei/)).toBeInTheDocument();
    expect(screen.getByText(/Chen Jing/)).toBeInTheDocument();

    // 年份
    expect(screen.getByText(/2022/)).toBeInTheDocument();
    expect(screen.getByText(/2021/)).toBeInTheDocument();

    // 来源徽章（openalex）
    const badges = screen.getAllByText(/OpenAlex/i);
    expect(badges.length).toBeGreaterThanOrEqual(1);

    // 被引数
    expect(screen.getByText(/47/)).toBeInTheDocument();
    expect(screen.getByText(/23/)).toBeInTheDocument();
  });

  it("按 sciverseDocId 显示含全文/仅题录徽章", () => {
    render(
      <SearchCandidateCards
        projectId={5}
        candidates={[
          { ...MOCK_CANDIDATES[0], candidate_id: "S1", sciverseDocId: "doc-1", source: "sciverse", provider: "sciverse" },
          { ...MOCK_CANDIDATES[1], candidate_id: "S2", sciverseDocId: null, source: "sciverse", provider: "sciverse" },
        ]}
      />,
    );

    expect(screen.getByText("含全文")).toBeInTheDocument();
    expect(screen.getByText("仅题录")).toBeInTheDocument();
  });

  it("多源候选来源徽标如实显示各源，不再一律标 OpenAlex", () => {
    render(
      <SearchCandidateCards
        projectId={5}
        candidates={[
          { ...MOCK_CANDIDATES[0], candidate_id: "core:1", source: "core", provider: "core", openalexId: null },
          { ...MOCK_CANDIDATES[1], candidate_id: "epmc:1", source: "europepmc", provider: "europepmc", openalexId: null },
        ]}
      />,
    );
    expect(screen.getByText("CORE")).toBeInTheDocument();
    expect(screen.getByText("EuropePMC")).toBeInTheDocument();
    // 不应把 core/europepmc 误标成 OpenAlex
    expect(screen.queryByText("OpenAlex")).not.toBeInTheDocument();
  });

  it("跨源合并候选显示所有涉及源(mergedSources)", () => {
    render(
      <SearchCandidateCards
        projectId={5}
        candidates={[
          { ...MOCK_CANDIDATES[0], candidate_id: "m:1", source: "core", provider: "core",
            mergedSources: ["core", "openalex"], openalexId: null },
        ]}
      />,
    );
    expect(screen.getByText("CORE+OpenAlex")).toBeInTheDocument();
  });

  it("带 OA 直链的候选显示「开放获取PDF」徽标", () => {
    render(
      <SearchCandidateCards
        projectId={5}
        candidates={[
          { ...MOCK_CANDIDATES[0], candidate_id: "oa:1", source: "core", provider: "core",
            pdfUrl: "https://oa.example.org/x.pdf", sciverseDocId: null, openalexId: null },
        ]}
      />,
    );
    expect(screen.getByText("开放获取PDF")).toBeInTheDocument();
  });

  it("默认全部候选被勾选", () => {
    render(<SearchCandidateCards projectId={5} candidates={MOCK_CANDIDATES} />);

    const checkboxes = screen.getAllByRole("checkbox");
    // 每条候选一个 checkbox + 可能有一个全选 checkbox
    const candidateCheckboxes = checkboxes.filter((cb) =>
      cb.getAttribute("aria-label")?.includes("选择") ||
      cb.closest("[data-candidate-id]") !== null
    );
    candidateCheckboxes.forEach((cb) => expect(cb).toBeChecked());
  });

  it("取消勾选单条候选", () => {
    render(<SearchCandidateCards projectId={5} candidates={MOCK_CANDIDATES} />);

    // 找到第一条候选的 checkbox（用 aria-label 或 data 属性定位）
    const checkboxes = screen.getAllByRole("checkbox");
    const firstCandidateCb = checkboxes.find(
      (cb) => cb.getAttribute("data-candidate-id") === "W1111"
    );
    expect(firstCandidateCb).toBeDefined();
    fireEvent.click(firstCandidateCb!);
    expect(firstCandidateCb!).not.toBeChecked();
  });

  it("「加入库」按钮调 mutateAsync，带选中候选 + defaultStatus='candidate'", async () => {
    render(<SearchCandidateCards projectId={5} candidates={MOCK_CANDIDATES} />);

    const addBtn = screen.getByRole("button", { name: /加入文献库/ });
    fireEvent.click(addBtn);

    await waitFor(() => {
      expect(mockMutateAsync).toHaveBeenCalledWith({
        pid: 5,
        candidates: expect.arrayContaining([
          expect.objectContaining({ title: "Analyst Forecast Accuracy and IPO Underpricing" }),
          expect.objectContaining({ title: "Earnings Management and Analyst Forecast Dispersion" }),
        ]),
        defaultStatus: "candidate",
      });
    });
  });

  it("「加入并纳入」按钮调 mutateAsync，带选中候选 + defaultStatus='included'", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<SearchCandidateCards projectId={5} candidates={MOCK_CANDIDATES} />);

    const includeBtn = screen.getByRole("button", { name: /加入并纳入/ });
    fireEvent.click(includeBtn);

    await waitFor(() => {
      expect(mockMutateAsync).toHaveBeenCalledWith({
        pid: 5,
        candidates: expect.arrayContaining([
          expect.objectContaining({ title: "Earnings Management and Analyst Forecast Dispersion" }),
        ]),
        defaultStatus: "included",
      });
    });
  });

  it("取消勾选后「加入并纳入」只发送剩余选中候选", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<SearchCandidateCards projectId={5} candidates={MOCK_CANDIDATES} />);

    // 取消第一条
    const firstCandidateCb = screen.getAllByRole("checkbox").find(
      (cb) => cb.getAttribute("data-candidate-id") === "W1111"
    );
    fireEvent.click(firstCandidateCb!);

    const includeBtn = screen.getByRole("button", { name: /加入并纳入/ });
    fireEvent.click(includeBtn);

    await waitFor(() => {
      const call = mockMutateAsync.mock.calls[0][0];
      expect(call.candidates).toHaveLength(1);
      expect(call.candidates[0].title).toContain("Earnings Management");
    });
    expect(confirmSpy).not.toHaveBeenCalled();
  });

  it("入库成功后显示 imported/skipped 计数反馈", async () => {
    mockMutateAsync.mockResolvedValue({ imported: 2, skipped: 0, failed: [], failedCount: 0, paperIds: [101, 102] });

    render(<SearchCandidateCards projectId={5} candidates={MOCK_CANDIDATES} />);

    const addBtn = screen.getByRole("button", { name: /加入文献库/ });
    fireEvent.click(addBtn);

    await waitFor(() => {
      // 应显示成功反馈，含 imported 数量
      expect(screen.getByText(/已导入 2 篇/)).toBeInTheDocument();
    });
  });

  it("入库返回 fulltextEligiblePaperIds 时自动补全文并显示结果", async () => {
    mockMutateAsync.mockResolvedValue({
      imported: 2,
      skipped: 0,
      failed: [],
      failedCount: 0,
      paperIds: [101, 102],
      fulltextEligiblePaperIds: [101],
    });
    mockBackfillFulltextAsync.mockResolvedValue({
      total: 1,
      fetched: 1,
      skipped: 0,
      failed: [],
      remaining: 0,
    });

    render(<SearchCandidateCards projectId={5} candidates={MOCK_CANDIDATES} />);

    fireEvent.click(screen.getByRole("button", { name: /加入文献库/ }));

    await waitFor(() => {
      expect(mockBackfillFulltextAsync).toHaveBeenCalledWith({
        paperIds: [101],
        maxPapers: 1,
        sciverse: { apiToken: "tok", baseUrl: "https://api.sciverse.space" },
      });
      expect(screen.getByText(/已为 1 篇拉取全文/)).toBeInTheDocument();
      expect(screen.getByText(/1 篇仅题录/)).toBeInTheDocument();
    });
  });

  it("skipped > 0 时显示跳过数提示", async () => {
    mockMutateAsync.mockResolvedValue({ imported: 1, skipped: 1, failed: [], failedCount: 0, paperIds: [101] });

    render(<SearchCandidateCards projectId={5} candidates={MOCK_CANDIDATES} />);

    const addBtn = screen.getByRole("button", { name: /加入文献库/ });
    fireEvent.click(addBtn);

    await waitFor(() => {
      expect(screen.getByText(/跳过 1 篇/)).toBeInTheDocument();
    });
  });

  it("无候选时不渲染任何卡片", () => {
    render(<SearchCandidateCards projectId={5} candidates={[]} />);

    expect(screen.queryByRole("checkbox")).toBeNull();
    // 空状态提示
    expect(screen.getByText(/暂无候选/)).toBeInTheDocument();
  });

  it("来源链接按钮可键盘聚焦（无障碍）", () => {
    render(<SearchCandidateCards projectId={5} candidates={MOCK_CANDIDATES} />);

    // D: 可点击链接 aria-label 含「打开来源链接」（P2-2: 改为来源链接，非原文）
    const links = screen.getAllByRole("link", { name: /打开来源链接/i });
    links.forEach((link) => {
      expect(link).toHaveAttribute("href");
      expect(link.getAttribute("href")).toContain("openalex.org");
    });
  });

  it("「加入库」按钮在无选中时禁用", () => {
    render(<SearchCandidateCards projectId={5} candidates={MOCK_CANDIDATES} />);

    // 取消所有选中
    const checkboxes = screen.getAllByRole("checkbox").filter(
      (cb) => cb.getAttribute("data-candidate-id")
    );
    checkboxes.forEach((cb) => fireEvent.click(cb));

    const addBtn = screen.getByRole("button", { name: /加入文献库/ });
    const includeBtn = screen.getByRole("button", { name: /加入并纳入/ });
    expect(addBtn).toBeDisabled();
    expect(includeBtn).toBeDisabled();
  });

  it("传入 query 时头部显示检索词和候选数", () => {
    render(
      <SearchCandidateCards
        projectId={5}
        candidates={MOCK_CANDIDATES}
        query="analyst forecast IPO"
      />
    );

    // 头部应包含检索词和候选数
    expect(screen.getByText(/analyst forecast IPO/)).toBeInTheDocument();
    expect(screen.getByText(/找到/)).toBeInTheDocument();
    // 候选数 2
    const strongEls = document.querySelectorAll(".candidate-cards-title strong");
    const countEl = Array.from(strongEls).find((el) => el.textContent === "2");
    expect(countEl).toBeDefined();
  });

  it("多轮累计检索时显示累计轮数、去重候选数和最近一轮数量", () => {
    render(
      <SearchCandidateCards
        projectId={5}
        candidates={MOCK_CANDIDATES}
        query="chronic disease management"
        searchCount={2}
        latestCount={20}
      />
    );

    expect(screen.getByText(/累计/)).toBeInTheDocument();
    expect(screen.getByText(/去重后/)).toBeInTheDocument();
    expect(screen.getByText(/最近：chronic disease management/)).toBeInTheDocument();
    expect(screen.getByText(/20 篇/)).toBeInTheDocument();
  });

  it("未传 query 时头部使用默认文案（不含检索词格式）", () => {
    render(<SearchCandidateCards projectId={5} candidates={MOCK_CANDIDATES} />);

    expect(screen.getByText(/检索到/)).toBeInTheDocument();
    expect(screen.queryByText(/找到/)).toBeNull();
  });

  it("label 上无重复 aria-label（只 input 有 aria-label）", () => {
    render(<SearchCandidateCards projectId={5} candidates={MOCK_CANDIDATES} />);

    // label 元素不应有 aria-label 属性
    const labels = document.querySelectorAll("label.candidate-item-label");
    labels.forEach((label) => {
      expect(label.hasAttribute("aria-label")).toBe(false);
    });

    // checkbox 仍有 aria-label
    const checkboxes = screen.getAllByRole("checkbox");
    const candidateCbs = checkboxes.filter((cb) => cb.getAttribute("data-candidate-id"));
    candidateCbs.forEach((cb) => {
      expect(cb).toHaveAttribute("aria-label");
    });
  });

  // ---- C: 候选集合变化时只默认勾选新候选 ----

  it("C: 第二轮合并候选后，被取消的旧候选保持未选中，新候选默认选中", () => {
    const CANDIDATES_A: SearchCandidate[] = [
      {
        candidate_id: "A1",
        title: "Paper A1",
        source: "openalex",
      },
      {
        candidate_id: "A2",
        title: "Paper A2",
        source: "openalex",
      },
    ];
    const CANDIDATES_MERGED: SearchCandidate[] = [
      ...CANDIDATES_A,
      {
        candidate_id: "B1",
        title: "Paper B1",
        source: "openalex",
      },
    ];

    const { rerender } = render(
      <SearchCandidateCards projectId={5} candidates={CANDIDATES_A} />
    );

    // 初始：A 集全选
    const checkboxesA = screen.getAllByRole("checkbox").filter(
      (cb) => cb.getAttribute("data-candidate-id")
    );
    expect(checkboxesA).toHaveLength(2);
    checkboxesA.forEach((cb) => expect(cb).toBeChecked());

    // 取消 A1 选择
    fireEvent.click(checkboxesA[0]);
    expect(checkboxesA[0]).not.toBeChecked();

    // 第二轮检索合并：A1/A2 已存在，B1 新出现。
    act(() => {
      rerender(<SearchCandidateCards projectId={5} candidates={CANDIDATES_MERGED} />);
    });

    const checkboxesMerged = screen.getAllByRole("checkbox").filter(
      (cb) => cb.getAttribute("data-candidate-id")
    );
    expect(checkboxesMerged).toHaveLength(3);
    expect(screen.getByLabelText("选择：Paper A1")).not.toBeChecked();
    expect(screen.getByLabelText("选择：Paper A2")).toBeChecked();
    expect(screen.getByLabelText("选择：Paper B1")).toBeChecked();
  });

  it("P1-5: 全选状态点「加入并纳入」先确认，确认后才触发回调", async () => {
    const confirmSpy = vi
      .spyOn(window, "confirm")
      .mockReturnValueOnce(false)
      .mockReturnValueOnce(true);

    render(<SearchCandidateCards projectId={5} candidates={MOCK_CANDIDATES} />);

    const includeBtn = screen.getByRole("button", { name: /加入并纳入/ });
    fireEvent.click(includeBtn);

    expect(confirmSpy).toHaveBeenCalledWith("确认将 2 篇全部加入并纳入？");
    expect(mockMutateAsync).not.toHaveBeenCalled();

    fireEvent.click(includeBtn);

    await waitFor(() => {
      expect(mockMutateAsync).toHaveBeenCalledWith({
        pid: 5,
        candidates: expect.arrayContaining([
          expect.objectContaining({ candidate_id: "W1111" }),
          expect.objectContaining({ candidate_id: "W2222" }),
        ]),
        defaultStatus: "included",
      });
    });
  });

  // ---- D: 链接文案和来源徽章 ----

  it("D: 可点击链接文案为「DOI/来源 ↗」，来源徽章为「OpenAlex」", () => {
    render(<SearchCandidateCards projectId={5} candidates={MOCK_CANDIDATES} />);

    // 来源徽章仍为 OpenAlex（span.candidate-source-badge）
    const sourceBadges = document.querySelectorAll(".candidate-source-badge");
    sourceBadges.forEach((badge) => {
      expect(badge.textContent).toBe("OpenAlex");
    });

    // P2-2: 可点击链接（badge 样式）文案为「DOI/来源 ↗」（非"原文"）
    const origLinks = document.querySelectorAll(".candidate-oa-link");
    origLinks.forEach((link) => {
      expect(link.textContent).toBe("DOI/来源 ↗");
    });

    // aria-label 含「打开来源链接」（P2-2 修改）
    const allLinks = screen.getAllByRole("link", { name: /打开来源链接/i });
    expect(allLinks.length).toBeGreaterThan(0);

    // 不应存在 aria-label 含「打开原文链接」（旧文案已去除）
    const oldLinks = screen.queryAllByRole("link", { name: /打开原文链接/i });
    expect(oldLinks).toHaveLength(0);
  });

  // ---- P2-3: failed 计数反馈 ----

  it("P1-3: failed > 0 时显示失败计数和可折叠明细", async () => {
    mockMutateAsync.mockResolvedValue({
      imported: 1,
      skipped: 0,
      failed: [
        { candidateId: "W1111", title: "Analyst Forecast Accuracy and IPO Underpricing", reason: "数据库冲突：UNIQUE constraint failed" },
        { candidateId: "W2222", title: "Earnings Management and Analyst Forecast Dispersion", reason: "字段异常：year out of range" },
      ],
      failedCount: 2,
      paperIds: [101],
    });

    render(<SearchCandidateCards projectId={5} candidates={MOCK_CANDIDATES} />);

    const addBtn = screen.getByRole("button", { name: /加入文献库/ });
    fireEvent.click(addBtn);

    await waitFor(() => {
      expect(screen.getByText(/已导入 1 篇/)).toBeInTheDocument();
      expect(screen.getByText(/失败 2 篇/)).toBeInTheDocument();
      expect(screen.getByText(/失败明细/)).toBeInTheDocument();
      expect(screen.getByText(/数据库冲突/)).toBeInTheDocument();
      expect(screen.getByText(/year out of range/)).toBeInTheDocument();
    });
  });

  it("P1-3: 点击「只重选失败项」后仅勾选失败候选", async () => {
    mockMutateAsync.mockResolvedValue({
      imported: 1,
      skipped: 0,
      failed: [
        { candidateId: "W2222", title: "Earnings Management and Analyst Forecast Dispersion", reason: "模拟 DB 500" },
      ],
      failedCount: 1,
      paperIds: [101],
    });

    render(<SearchCandidateCards projectId={5} candidates={MOCK_CANDIDATES} />);

    const addBtn = screen.getByRole("button", { name: /加入文献库/ });
    fireEvent.click(addBtn);

    const retryFailedBtn = await screen.findByRole("button", { name: /只重选失败项/ });
    fireEvent.click(retryFailedBtn);

    expect(screen.getByLabelText("选择：Analyst Forecast Accuracy and IPO Underpricing")).not.toBeChecked();
    expect(screen.getByLabelText("选择：Earnings Management and Analyst Forecast Dispersion")).toBeChecked();
  });

  it("P2-3: failed = 0 时不显示失败提示", async () => {
    mockMutateAsync.mockResolvedValue({ imported: 2, skipped: 0, failed: [], failedCount: 0, paperIds: [101, 102] });

    render(<SearchCandidateCards projectId={5} candidates={MOCK_CANDIDATES} />);

    const addBtn = screen.getByRole("button", { name: /加入文献库/ });
    fireEvent.click(addBtn);

    await waitFor(() => {
      expect(screen.getByText(/已导入 2 篇/)).toBeInTheDocument();
      expect(screen.queryByText(/失败/)).toBeNull();
    });
  });
});
