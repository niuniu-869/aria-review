import { afterEach, describe, expect, it, vi } from "vitest";
import {
  addPapersFromSearch,
  ApiError,
  cancelRun,
  createCorpus,
  createRun,
  fetchSciverseContent,
  getHealth,
  getOverview,
  pingLlm,
  pingSciverse,
  sanitizeSearchCandidateForImport,
  searchSciverseMeta,
  streamReview,
  streamAgentRun,
} from "./client";
import type { AgentRunHandlers } from "./client";

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

describe("api client", () => {
  it("getHealth 返回 body", async () => {
    vi.stubGlobal("fetch", mockFetch(200, { status: "ok", service: "agent", rService: "up" }));
    const h = await getHealth();
    expect(h.status).toBe("ok");
    expect(h.rService).toBe("up");
  });

  it("createCorpus 发 multipart POST 并返回 ref", async () => {
    const f = mockFetch(202, { corpusId: "c1", projectId: "p", status: "ready", schemaVersion: 1 });
    vi.stubGlobal("fetch", f);
    const file = new File(["x"], "a.txt");
    const ref = await createCorpus("p", file, "wos");
    expect(ref.corpusId).toBe("c1");
    const [url, init] = f.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/projects/p/corpus");
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
  });

  it("cancelRun 发 POST 到 .../cancel 端点 (Phase 5)", async () => {
    const f = mockFetch(200, { status: "cancelled" });
    vi.stubGlobal("fetch", f);
    await cancelRun(7, "run-abc");
    const [url, init] = f.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/projects/7/agent/runs/run-abc/cancel");
    expect(init.method).toBe("POST");
  });

  it("pingLlm 通过请求头发送自定义 baseUrl/model/key", async () => {
    const f = mockFetch(200, {
      ok: true,
      model: "gpt-5.5",
      baseUrl: "https://sub2api0.zeabur.app",
      content: "pong",
    });
    vi.stubGlobal("fetch", f);
    await pingLlm({
      apiKey: "test-api-key",
      baseUrl: "https://sub2api0.zeabur.app",
      model: "gpt-5.5",
    });
    const [url, init] = f.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/ai/ping");
    expect((init.headers as Record<string, string>)["X-LLM-Key"]).toBe("test-api-key");
    expect((init.headers as Record<string, string>)["X-LLM-Base-URL"]).toBe("https://sub2api0.zeabur.app");
    expect((init.headers as Record<string, string>)["X-LLM-Model"]).toBe("gpt-5.5");
  });

  it("createRun 通过请求头发送自定义 LLM 配置", async () => {
    const f = mockFetch(200, { runId: "1", projectId: 7, status: "running" });
    vi.stubGlobal("fetch", f);
    await createRun(7, { prompt: "hi" }, {
      apiKey: "test-api-key",
      baseUrl: "https://sub2api0.zeabur.app",
      model: "gpt-5.5",
    });
    const [, init] = f.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)["X-LLM-Base-URL"]).toBe("https://sub2api0.zeabur.app");
    expect((init.headers as Record<string, string>)["X-LLM-Model"]).toBe("gpt-5.5");
  });

  it("addPapersFromSearch 入库前清洗候选，避免后端 422", async () => {
    const f = mockFetch(200, { imported: 1, skipped: 0, failed: 0, paperIds: [1] });
    vi.stubGlobal("fetch", f);
    const longAbstract = "a".repeat(20050);
    await addPapersFromSearch(7, [{
      candidate_id: "c1",
      title: "t".repeat(1200),
      year: 2020.8,
      abstract: longAbstract,
      authors: Array.from({ length: 105 }, (_, i) => `Author ${i}`),
      externalIds: Array.from({ length: 25 }, (_, i) => ({ id: i })),
      source: "openalex",
    }], "included");

    const [, init] = f.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string);
    const candidate = body.candidates[0];
    expect(body.defaultStatus).toBe("included");
    expect(candidate.title).toHaveLength(1000);
    expect(candidate.abstract).toHaveLength(20000);
    expect(candidate.year).toBe(2020);
    expect(candidate.authors).toHaveLength(100);
    expect(candidate.externalIds).toHaveLength(20);
  });

  it("sanitizeSearchCandidateForImport 丢弃越界 year 和过大 raw", () => {
    const payload = sanitizeSearchCandidateForImport({
      candidate_id: "c1",
      title: "Valid",
      year: 2200,
      raw: { blob: "x".repeat(100001) },
      source: "openalex",
    });
    expect(payload.year).toBeUndefined();
    expect(payload.raw).toBeUndefined();
  });

  it("Sciverse API 通过请求头发送自定义 baseUrl/token", async () => {
    const f = mockFetch(200, { ok: true, baseUrl: "https://api.sciverse.space", resultCount: 1 });
    vi.stubGlobal("fetch", f);
    await pingSciverse({
      apiToken: "tok-test",
      baseUrl: "https://api.sciverse.space",
    });
    const [url, init] = f.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/sciverse/ping");
    expect((init.headers as Record<string, string>)["X-Sciverse-Token"]).toBe("tok-test");
    expect((init.headers as Record<string, string>)["X-Sciverse-Base-URL"]).toBe("https://api.sciverse.space");
  });

  it("searchSciverseMeta 调用 Sciverse 元数据检索端点", async () => {
    const f = mockFetch(200, { candidates: [], totalCount: 0 });
    vi.stubGlobal("fetch", f);
    await searchSciverseMeta({ query: "bibliometrics", pageSize: 5 }, { apiToken: "tok" });
    const [url, init] = f.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/sciverse/meta-search");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["X-Sciverse-Token"]).toBe("tok");
    expect(JSON.parse(init.body as string)).toMatchObject({ query: "bibliometrics", pageSize: 5 });
  });

  it("fetchSciverseContent 调用项目文献全文保存端点", async () => {
    const f = mockFetch(200, {
      paperId: 3,
      docId: "doc-1",
      attachmentId: 9,
      chars: 120,
      sha256: "abc",
    });
    vi.stubGlobal("fetch", f);
    await fetchSciverseContent(7, 3, { docId: "doc-1" }, { apiToken: "tok" });
    const [url, init] = f.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/projects/7/papers/3/sciverse/content");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["X-Sciverse-Token"]).toBe("tok");
    expect(JSON.parse(init.body as string)).toMatchObject({ docId: "doc-1" });
  });

  it("错误响应抛 ApiError 带 code/status", async () => {
    vi.stubGlobal("fetch", mockFetch(404, { code: "CORPUS_NOT_FOUND", message: "语料不存在" }));
    await expect(getOverview("p", "c")).rejects.toMatchObject({
      code: "CORPUS_NOT_FOUND",
      status: 404,
    });
  });

  it("ApiError 是 Error 实例", () => {
    const e = new ApiError("X", 500, "boom");
    expect(e).toBeInstanceOf(Error);
    expect(e.code).toBe("X");
  });

  it("网络失败归一为 ApiError(NETWORK_ERROR) (Codex step4-P2)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("conn refused")));
    await expect(getHealth()).rejects.toMatchObject({ code: "NETWORK_ERROR", status: 0 });
  });

  it("streamReview 解析 SSE 事件序列", async () => {
    const frames = [
      'event: meta\ndata: {"template":"本科综述","chapters":["a"],"docCount":2}\n\n',
      'event: chapter\ndata: {"index":0,"title":"a"}\n\n',
      'event: token\ndata: {"text":"hello "}\n\n',
      'event: token\ndata: {"text":"world"}\n\n',
      'event: citations\ndata: {"summary":{"green":1,"yellow":0,"red":0},"annotated":"x ✅"}\n\n',
      'event: done\ndata: {"chapters":1}\n\n',
    ];
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(c) {
        for (const f of frames) c.enqueue(encoder.encode(f));
        c.close();
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, status: 200, body: stream, headers: new Headers() } as unknown as Response),
    );
    const tokens: string[] = [];
    let green = -1;
    let done = false;
    await streamReview("p", "c", { type: "undergrad", topic: "t" }, {}, {
      onToken: (t) => tokens.push(t),
      onCitations: (d) => (green = d.summary.green),
      onDone: () => (done = true),
    });
    expect(tokens.join("")).toBe("hello world");
    expect(green).toBe(1);
    expect(done).toBe(true);
  });

  it("streamReview 收到 error 事件后 reject (Codex slice2-P2)", async () => {
    const frames = ['event: error\ndata: {"code":"LLM_ERROR","message":"boom"}\n\n'];
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(c) {
        for (const f of frames) c.enqueue(encoder.encode(f));
        c.close();
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, status: 200, body: stream, headers: new Headers() } as unknown as Response),
    );
    await expect(
      streamReview("p", "c", { type: "undergrad", topic: "t" }, {}, {}),
    ).rejects.toMatchObject({ code: "LLM_ERROR" });
  });

  it("streamReview 非200 抛 ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 400,
        statusText: "x",
        json: async () => ({ code: "VALIDATION_ERROR", message: "bad" }),
      } as unknown as Response),
    );
    await expect(
      streamReview("p", "c", { type: "x", topic: "t" }, {}, {}),
    ).rejects.toMatchObject({ code: "VALIDATION_ERROR" });
  });

  // 修复2: streamAgentRun 404 应走 onError 而非抛出（RUN_NOT_FOUND 是普通 HTTP 错误）
  it("streamAgentRun 404 调用 onError 而非抛出", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
        statusText: "Not Found",
        json: async () => ({ code: "RUN_NOT_FOUND", message: "run not found" }),
      } as unknown as Response),
    );
    const onError = vi.fn();
    const handlers: AgentRunHandlers = { onError };
    // 不应抛出
    await expect(streamAgentRun(1, "rid", {}, handlers)).resolves.toBeUndefined();
    expect(onError).toHaveBeenCalledWith(
      expect.objectContaining({ type: "error", error: "run not found" }),
    );
  });

  // 修复3: streamAgentRun 流结束无终态事件 → onError STREAM_INCOMPLETE
  it("streamAgentRun 流结束无终态事件时调用 onError 提示中断", async () => {
    // 只发送 run_start，无 run_complete / error
    const frame = 'event: run_start\ndata: {"type":"run_start","max_rounds":5,"model":"m","seq":0}\n\n';
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(c) {
        c.enqueue(encoder.encode(frame));
        c.close();
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, status: 200, body: stream } as unknown as Response),
    );
    const onRunStart = vi.fn();
    const onError = vi.fn();
    const handlers: AgentRunHandlers = { onRunStart, onError };
    await streamAgentRun(1, "rid", {}, handlers);
    expect(onRunStart).toHaveBeenCalledTimes(1);
    expect(onError).toHaveBeenCalledWith(
      expect.objectContaining({ type: "error", error: expect.stringContaining("中断") }),
    );
  });

  // 修复2: streamAgentRun 正常流（含 run_complete）不触发 STREAM_INCOMPLETE
  it("streamAgentRun 正常完成不触发 STREAM_INCOMPLETE onError", async () => {
    const frames = [
      'event: run_start\ndata: {"type":"run_start","max_rounds":5,"model":"m","seq":0}\n\n',
      'event: run_complete\ndata: {"type":"run_complete","status":"completed","final_output":"done","seq":1}\n\n',
    ];
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(c) {
        for (const f of frames) c.enqueue(encoder.encode(f));
        c.close();
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, status: 200, body: stream } as unknown as Response),
    );
    const onError = vi.fn();
    const onRunComplete = vi.fn();
    await streamAgentRun(1, "rid", {}, { onError, onRunComplete });
    expect(onRunComplete).toHaveBeenCalledTimes(1);
    expect(onError).not.toHaveBeenCalled();
  });
});
