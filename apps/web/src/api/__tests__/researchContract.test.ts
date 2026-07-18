/**
 * researchContract.test.ts — 研究副驾契约形状 + 单源 fixture 不变量（B1）。
 *
 * 作用：把研究副驾契约的关键铁律固化为可执行断言，作为前端侧 fixture
 * 真相源（后端 contract-shape 测试独立断言同一契约）。fixture 已用生成类型标注 →
 * 形状由 tsc 守；本测试再守「值层不变量」（decided_by/gathered_by/可空性/枚举/源坐标）。
 *
 * 同时覆盖 client 的 AIP 风格自定义方法 URL（:discover / :verify）构造正确。
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { asRCorpusId } from "../corpusIds";
import {
  ALL_GAPS,
  ALL_VERDICT_RESULTS,
  SCRATCHPAD_TICKS,
  evidenceG2,
  gapDraftConcept,
  gapVerifiedMethod,
  scratchpadState,
  verdictInconclusiveG5,
  verdictResultG2,
  discoverAccepted,
  verifyAccepted,
  feasibilityResultG2,
} from "../research.fixtures";
import { RUN_STATUS_DONE, RUN_STATUS_FAILED, RUN_STATUS_RUNNING } from "../runStatus";
import type { EvidencePack, GapCandidate, ValueVerdict } from "../../types/research";

// 从裁决结果注册表派生（含 G4），杜绝漏项（codex B1-P2）：
// 任一裁决/证据加进 ALL_VERDICT_RESULTS 即自动纳入全部铁律断言。
const ALL_VERDICTS: ValueVerdict[] = ALL_VERDICT_RESULTS.map((r) => r.verdict);
const ALL_PACKS: EvidencePack[] = ALL_VERDICT_RESULTS.map((r) => r.evidence);

const LENSES = new Set(["concept", "method", "theory"]);
const STATUSES = new Set(["draft", "verified", "accepted", "rejected"]);
const VERDICT_KINDS = new Set(["valuable", "likely_filled", "inconclusive"]);
const PROVIDERS = new Set(["sciverse", "openalex"]);
const METRICS = new Set(["cooccurrence_gap", "low_coupling"]);

describe("研究契约 · 分层铁律不变量", () => {
  it("裁决恒由确定性 resolver 出：decided_by === 'deterministic'", () => {
    expect(ALL_VERDICTS.length).toBeGreaterThan(0);
    for (const v of ALL_VERDICTS) expect(v.decided_by).toBe("deterministic");
  });

  it("证据包由 subagent 攒：gathered_by === 'subagent'（工具不裁决）", () => {
    for (const p of ALL_PACKS) expect(p.gathered_by).toBe("subagent");
  });

  it("透明阈值齐备且 high > low", () => {
    for (const v of ALL_VERDICTS) {
      expect(typeof v.thresholds.reverse_hit_high).toBe("number");
      expect(typeof v.thresholds.reverse_hit_low).toBe("number");
      expect(v.thresholds.reverse_hit_high).toBeGreaterThan(v.thresholds.reverse_hit_low);
    }
  });

  it("每个裁决结果 verdict↔hit_count 规则自洽（valuable=低/likely_filled=高/inconclusive=中）", () => {
    // 泛化到全部裁决结果（含 G4），而非硬编码若干（codex B1-P2）
    for (const r of ALL_VERDICT_RESULTS) {
      const hit = r.evidence.reverse_search.hit_count;
      const t = r.verdict.thresholds;
      if (r.verdict.verdict === "valuable") {
        expect(hit, `${r.gap_id} valuable 应低命中`).toBeLessThanOrEqual(t.reverse_hit_low);
      } else if (r.verdict.verdict === "likely_filled") {
        expect(hit, `${r.gap_id} likely_filled 应高命中`).toBeGreaterThanOrEqual(t.reverse_hit_high);
      } else {
        expect(hit, `${r.gap_id} inconclusive 应介于阈值之间`).toBeGreaterThan(t.reverse_hit_low);
        expect(hit).toBeLessThan(t.reverse_hit_high);
      }
    }
  });

  it("闭包自洽：每个非空 value_verdict 都有匹配的裁决结果（裁决+证据，codex B1-P2）", () => {
    const byGap = new Map(ALL_VERDICT_RESULTS.map((r) => [r.gap_id, r]));
    for (const g of ALL_GAPS) {
      if (g.value_verdict === null) continue;
      const r = byGap.get(g.gap_id);
      expect(r, `gap ${g.gap_id} 的非空 verdict 必须有对应裁决结果`).toBeDefined();
      expect(r!.verdict.verdict).toBe(g.value_verdict.verdict);
      expect(r!.verdict.gap_id).toBe(g.gap_id);
      expect(r!.evidence.gap_id).toBe(g.gap_id);
    }
  });
});

describe("研究契约 · 枚举与可空性", () => {
  it("枚举值合法（lens/status/verdict/provider/metric）", () => {
    for (const g of ALL_GAPS) {
      expect(LENSES.has(g.lens)).toBe(true);
      expect(STATUSES.has(g.status)).toBe(true);
    }
    for (const v of ALL_VERDICTS) expect(VERDICT_KINDS.has(v.verdict)).toBe(true);
    for (const p of ALL_PACKS) {
      expect(PROVIDERS.has(p.reverse_search.provider)).toBe(true);
      expect(METRICS.has(p.biblio_structure.metric)).toBe(true);
    }
  });

  it("value_verdict 可空：draft 为 null，verified/accepted 非空且 gap_id 自洽", () => {
    expect(gapDraftConcept.value_verdict).toBeNull();
    expect(gapVerifiedMethod.value_verdict).not.toBeNull();
    expect(gapVerifiedMethod.value_verdict?.gap_id).toBe(gapVerifiedMethod.gap_id);
  });

  it("契约确实承载 nullable：存在 score=null / year=null / doi=null / source_view=null 的样本", () => {
    // score 可空（inconclusive）
    expect(verdictInconclusiveG5.score).toBeNull();
    // year / doi 可空（反向检索命中里至少一条）
    expect(evidenceG2.reverse_search.top_hits.some((h) => h.year === null)).toBe(true);
    expect(evidenceG2.reverse_search.top_hits.some((h) => h.doi === null)).toBe(true);
    // source_view 可空（计量结构未绑定单一视图）
    expect(ALL_PACKS.some((p) => p.biblio_structure.source_view === null)).toBe(true);
  });
});

describe("研究契约 · 逐字保留 + 源坐标（铁律 3）", () => {
  it("每条 supporting_paper 带 anchor_id + 非空 quote（可回定位原文块）", () => {
    for (const g of ALL_GAPS) {
      expect(g.supporting_papers.length).toBeGreaterThan(0);
      for (const sp of g.supporting_papers) {
        expect(sp.anchor_id).toMatch(/\S/);
        expect(sp.quote).toMatch(/\S/);
        expect(Number.isInteger(sp.paper_id)).toBe(true);
      }
    }
  });

  it("fail-loud：跳过项以显式 reason 表达（绝不静默成空结果）", () => {
    const withSkips = ALL_PACKS.filter((p) => p.skipped.length > 0);
    expect(withSkips.length).toBeGreaterThan(0);
    for (const p of withSkips) for (const s of p.skipped) expect(s.reason).toMatch(/\S/);
  });

  it("scratchpad 顶层形状自洽（run_id / run_status / entries / updated_at）", () => {
    expect(scratchpadState.run_id).toMatch(/\S/);
    expect(Array.isArray(scratchpadState.entries)).toBe(true);
    expect(scratchpadState.entries.length).toBe(ALL_GAPS.length);
    expect(scratchpadState.updated_at).toMatch(/\d{4}-\d{2}-\d{2}T/);
  });

  it("run_status 是合法停轮询信号；ticks 末态为终态、含 running（codex B1-P1）", () => {
    const RUN_STATES = new Set([RUN_STATUS_RUNNING, RUN_STATUS_DONE, RUN_STATUS_FAILED]);
    expect(RUN_STATES.has(scratchpadState.run_status)).toBe(true);
    for (const t of SCRATCHPAD_TICKS) expect(RUN_STATES.has(t.run_status)).toBe(true);
    // 轮询序列：最后一拍必须终态（停轮询），中间至少一拍 running（持续轮询）
    expect([RUN_STATUS_DONE, RUN_STATUS_FAILED]).toContain(SCRATCHPAD_TICKS[SCRATCHPAD_TICKS.length - 1].run_status);
    expect(SCRATCHPAD_TICKS.some((t) => t.run_status === RUN_STATUS_RUNNING)).toBe(true);
  });

  it("GapVerdictResult 自洽：gap_id 三处一致（结果/裁决/证据）", () => {
    expect(verdictResultG2.gap_id).toBe(verdictResultG2.verdict.gap_id);
    expect(verdictResultG2.gap_id).toBe(verdictResultG2.evidence.gap_id);
  });

  it("GapFeasibilityVerdictResult 自洽：gap_id 三处一致且裁决来自确定性状态机", () => {
    expect(feasibilityResultG2.gap_id).toBe(feasibilityResultG2.verdict.gap_id);
    expect(feasibilityResultG2.gap_id).toBe(feasibilityResultG2.pack?.gap_id);
    expect(feasibilityResultG2.verdict.decided_by).toBe("deterministic");
  });
});

describe("研究 client · AIP 自定义方法 URL 构造", () => {
  afterEach(() => vi.unstubAllGlobals());

  function stubFetch(json: unknown) {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return new Response(JSON.stringify(json), { status: 200, headers: { "content-type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
    return calls;
  }

  it("discoverGaps → POST .../corpus/{cid}/gaps:discover", async () => {
    const { discoverGaps } = await import("../client");
    const calls = stubFetch(discoverAccepted);
    const res = await discoverGaps(5, asRCorpusId("rc_mda_001"));
    expect(res.run_id).toBe(discoverAccepted.run_id);
    expect(calls[0].url).toMatch(/\/projects\/5\/corpus\/rc_mda_001\/gaps:discover$/);
    expect(calls[0].init?.method).toBe("POST");
  });

  it("verifyGap → POST .../gaps/{gap_id}:verify", async () => {
    const { verifyGap } = await import("../client");
    const calls = stubFetch(verifyAccepted);
    const res = await verifyGap(5, "g2");
    expect(res.verify_run_id).toBe(verifyAccepted.verify_run_id);
    expect(calls[0].url).toMatch(/\/projects\/5\/gaps\/g2:verify$/);
    expect(calls[0].init?.method).toBe("POST");
  });

  it("verifyGapFeasibility → POST .../gaps/{gap_id}:feasibility", async () => {
    const { verifyGapFeasibility } = await import("../client");
    const calls = stubFetch({ feasibility_run_id: "run_feasibility_001" });
    const res = await verifyGapFeasibility(5, "g2");
    expect(res.feasibility_run_id).toBe("run_feasibility_001");
    expect(calls[0].url).toMatch(/\/projects\/5\/gaps\/g2:feasibility$/);
    expect(calls[0].init?.method).toBe("POST");
  });

  it("patchGap → PATCH .../gaps/{gap_id} 带 action body", async () => {
    const { patchGap } = await import("../client");
    const accepted: GapCandidate = { ...gapVerifiedMethod, status: "accepted" };
    const calls = stubFetch(accepted);
    const res = await patchGap(5, "g2", { action: "accept" });
    expect(res.status).toBe("accepted");
    expect(calls[0].url).toMatch(/\/projects\/5\/gaps\/g2$/);
    expect(calls[0].init?.method).toBe("PATCH");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ action: "accept" });
  });
});
