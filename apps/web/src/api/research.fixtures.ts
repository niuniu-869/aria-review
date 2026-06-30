/**
 * research.fixtures.ts — 研究副驾「单一真相」契约 fixture（B1）。
 *
 * 为何单源：项目既有教训 = 前端 e2e fixture 若与契约 fixture 手抄两份，会漂移 → playwright
 * 虚绿。此模块为唯一真相，被 **vitest 组件测试** 与 **e2e dev 注入** 同时 import；
 * 每个常量都用 `schema.d.ts` 生成类型显式标注 → 一旦 openapi 契约改而 fixture 没跟，`tsc`
 * 直接红，结构漂移在编译期即被挡（强于运行期手动同步）。
 *
 * 数据域：MD&A 文本分析（基线 demo 锚点）。注意：领域内容只在「数据」里，核心代码零硬编码
 * 商科词（§0.3 泛化硬约束）；换领域只换本文件数据、不动组件/契约形状。
 *
 * 覆盖面（供 B2/B3/B4 + e2e 穷举渲染）：
 *   - GapCandidate × {concept/method/theory} × {draft/verified/accepted}
 *   - ValueVerdict × {valuable / likely_filled / inconclusive}（含可空 score / source_view）
 *   - EvidencePack 反向检索命中（含可空 year / doi）+ 计量结构佐证 + fail-loud skipped
 */
import type {
  EvidencePack,
  GapCandidate,
  GapDiscoverAccepted,
  GapVerdictResult,
  GapVerifyAccepted,
  ScratchpadState,
  ValueThresholds,
  ValueVerdict,
} from "../types/research";

// ---- 运行期标识（e2e page.route glob 与 dev 注入共用） ----
export const FIXTURE_PID = 5;
export const FIXTURE_CID = "rc_mda_001";
export const FIXTURE_RUN_ID = "run_gap_001";
export const FIXTURE_VERIFY_RUN_ID = "run_verify_001";

/** 透明阈值（商科默认口径；工程领域可按 §7 传参微调，此处为 fixture 默认值） */
export const THRESHOLDS: ValueThresholds = { reverse_hit_high: 25, reverse_hit_low: 3 };

// ============================================================
// 价值裁决（确定性 resolver 产物；decided_by 恒为 deterministic）
// ============================================================

/** valuable：低命中(≤low) + 共现断层 → 真空白、有研究价值 */
export const verdictValuableG2: ValueVerdict = {
  gap_id: "g2",
  verdict: "valuable",
  score: 0.86,
  thresholds: THRESHOLDS,
  rationale:
    "反向检索仅 2 篇强相关（≤ 阈值 3），且 conceptual 网络中两核心概念存在共现断层（structural_hole=true）→ 判定为真空白、有研究价值。",
  decided_by: "deterministic",
};

/** valuable（已被人工 accept 的 g4） */
export const verdictValuableG4: ValueVerdict = {
  gap_id: "g4",
  verdict: "valuable",
  score: 0.79,
  thresholds: THRESHOLDS,
  rationale:
    "反向检索 1 篇强相关（≤ 阈值 3），且 conceptual 网络存在共现断层 → 真空白；已经人工 accept 定稿。",
  decided_by: "deterministic",
};

/** likely_filled：高命中(≥high) → 疑为伪空白（检索不全） */
export const verdictLikelyFilledG3: ValueVerdict = {
  gap_id: "g3",
  verdict: "likely_filled",
  score: 0.22,
  thresholds: THRESHOLDS,
  rationale:
    "反向检索命中 41 篇强相关（≥ 阈值 25）→ 该方向已有大量研究，疑为检索不全造成的伪空白，价值存疑。",
  decided_by: "deterministic",
};

/** inconclusive：命中介于阈值之间且无明确断层 → 证据不足（score 可空为 null） */
export const verdictInconclusiveG5: ValueVerdict = {
  gap_id: "g5",
  verdict: "inconclusive",
  score: null,
  thresholds: THRESHOLDS,
  rationale:
    "反向检索命中 11 篇（介于阈值 3–25 之间），且未检出明确共现断层 → 证据不足以判定，建议人工复核。",
  decided_by: "deterministic",
};

// ============================================================
// 证据包（subagent 攒证；工具不裁决）。覆盖可空 year/doi/source_view + fail-loud skipped。
// ============================================================

export const evidenceG2: EvidencePack = {
  gap_id: "g2",
  reverse_search: {
    query: "MD&A 语气 语义嵌入 跨行业 对照",
    provider: "sciverse",
    hit_count: 2,
    top_hits: [
      { title: "Embedding-based tone measurement in annual reports", year: 2023, doi: "10.1016/j.jacc.2023.0142", relevance: 0.41 },
      { title: "语义向量与年报语气的单行业证据", year: null, doi: null, relevance: 0.33 },
    ],
  },
  biblio_structure: {
    metric: "cooccurrence_gap",
    value: 0.12,
    interpretation: "「语义嵌入语气」与「跨行业对照」两概念在共现网络中几乎不相邻（共现强度 0.12，低于断层阈值），存在结构洞。",
    source_view: "conceptual",
  },
  gathered_by: "subagent",
  skipped: [],
};

export const evidenceG3: EvidencePack = {
  gap_id: "g3",
  reverse_search: {
    query: "语气操纵 真实盈余管理 替代关系 理论框架",
    provider: "openalex",
    hit_count: 41,
    top_hits: [
      { title: "Tone management as a substitute for real earnings management", year: 2021, doi: "10.2308/accr-52910", relevance: 0.74 },
      { title: "Narrative manipulation and earnings quality: a review", year: 2022, doi: "10.1111/1475-679X.12410", relevance: 0.69 },
      { title: "语气操纵与盈余管理替代的经验证据", year: 2020, doi: null, relevance: 0.61 },
    ],
  },
  biblio_structure: {
    metric: "low_coupling",
    value: 0.58,
    interpretation: "两概念耦合度中等（0.58），非显著断层；结合高命中，结构未支持「真空白」。",
    source_view: "conceptual",
  },
  gathered_by: "subagent",
  skipped: [],
};

export const evidenceG5: EvidencePack = {
  gap_id: "g5",
  reverse_search: {
    query: "文本语气异常 盈余质量 预警 中小板",
    provider: "openalex",
    hit_count: 11,
    top_hits: [
      { title: "Abnormal tone and earnings quality signals", year: 2022, doi: "10.1016/j.jcorpfin.2022.102233", relevance: 0.52 },
      { title: "中小板公司文本语气与盈余质量", year: null, doi: null, relevance: 0.4 },
    ],
  },
  biblio_structure: {
    metric: "low_coupling",
    value: 0.44,
    // source_view 可空：本佐证未绑定单一视图（演示 nullable，UI 须不报错）
    interpretation: "耦合度 0.44，未检出明确共现断层；结构证据不足以单独支持判定。",
    source_view: null,
  },
  gathered_by: "subagent",
  // fail-loud：跳过项显式表达，绝不静默成空结果
  skipped: [{ reason: "OpenAlex 近 5 年过滤后候选不足，已跳过补充检索" }],
};

/** valuable 证据（g4，已 accept）：低命中 + 共现断层，与 verdictValuableG4 自洽（codex B1-P2 闭包） */
export const evidenceG4: EvidencePack = {
  gap_id: "g4",
  reverse_search: {
    query: "文本语气异常 盈余质量 预警 中小板 有效性",
    provider: "sciverse",
    hit_count: 1,
    top_hits: [
      { title: "Textual tone anomaly as an earnings-quality early warning", year: 2024, doi: "10.1016/j.bar.2024.101355", relevance: 0.38 },
    ],
  },
  biblio_structure: {
    metric: "cooccurrence_gap",
    value: 0.09,
    interpretation: "「文本语气异常」与「中小板盈余质量」两概念在共现网络中近乎不相邻（0.09），存在显著结构洞。",
    source_view: "conceptual",
  },
  gathered_by: "subagent",
  skipped: [],
};

// ---- GET .../verdict 返回体（裁决 + 证据包） ----
export const verdictResultG2: GapVerdictResult = { gap_id: "g2", verdict: verdictValuableG2, evidence: evidenceG2 };
export const verdictResultG3: GapVerdictResult = { gap_id: "g3", verdict: verdictLikelyFilledG3, evidence: evidenceG3 };
export const verdictResultG4: GapVerdictResult = { gap_id: "g4", verdict: verdictValuableG4, evidence: evidenceG4 };
export const verdictResultG5: GapVerdictResult = { gap_id: "g5", verdict: verdictInconclusiveG5, evidence: evidenceG5 };

/** 全部已产出的裁决结果（裁决+证据闭包）。test 据此校验「非空 verdict 必有自洽证据包」。 */
export const ALL_VERDICT_RESULTS: GapVerdictResult[] = [
  verdictResultG2,
  verdictResultG3,
  verdictResultG4,
  verdictResultG5,
];

// ============================================================
// GAP 候选（scratchpad 条目）。覆盖三 lens × 多状态；value_verdict 可空。
// ============================================================

/** concept · draft · 未核验（value_verdict=null） */
export const gapDraftConcept: GapCandidate = {
  gap_id: "g1",
  theme: "MD&A 文本特征与信息含量",
  statement: "MD&A 文本可读性与分析师预测分歧的关系，在高科技行业情境下尚未被系统检验。",
  lens: "concept",
  supporting_papers: [
    { paper_id: 12, anchor_id: "p12_b3__occ1", quote: "可读性较低的 MD&A 与更大的分析师预测分歧相关。" },
  ],
  counter_evidence: [],
  confidence: 0.62,
  status: "draft",
  value_verdict: null,
};

/** method · verified · valuable */
export const gapVerifiedMethod: GapCandidate = {
  gap_id: "g2",
  theme: "MD&A 文本特征与信息含量",
  statement: "基于深度语义嵌入度量 MD&A 语气的方法，尚未与传统词典法做跨行业对照。",
  lens: "method",
  supporting_papers: [
    { paper_id: 7, anchor_id: "p7_b9__occ1", quote: "现有研究多依赖 LM 词典统计语气。" },
    { paper_id: 23, anchor_id: "p23_b2__occ1", quote: "嵌入式语义度量在单行业样本上表现更优。" },
  ],
  counter_evidence: [
    { paper_id: 31, anchor_id: "p31_b5__occ1", note: "个别研究已尝试嵌入法，但样本受限于单一行业。" },
  ],
  confidence: 0.71,
  status: "verified",
  value_verdict: verdictValuableG2,
};

/** theory · verified · likely_filled（疑伪空白） */
export const gapVerifiedTheory: GapCandidate = {
  gap_id: "g3",
  theme: "盈余管理识别与文本语气",
  statement: "管理层语气操纵与真实盈余管理之间的替代关系，缺乏统一的理论框架。",
  lens: "theory",
  supporting_papers: [
    { paper_id: 4, anchor_id: "p4_b1__occ1", quote: "语气操纵可能替代应计盈余管理。" },
  ],
  counter_evidence: [],
  confidence: 0.55,
  status: "verified",
  value_verdict: verdictLikelyFilledG3,
};

/** concept · accepted · valuable（人工已 accept 定稿） */
export const gapAcceptedConcept: GapCandidate = {
  gap_id: "g4",
  theme: "盈余管理识别与文本语气",
  statement: "文本语气异常作为盈余质量预警指标，在中小板样本中的有效性尚未被评估。",
  lens: "concept",
  supporting_papers: [
    { paper_id: 9, anchor_id: "p9_b7__occ1", quote: "语气异常与后续盈余下调存在相关性。" },
  ],
  counter_evidence: [],
  confidence: 0.68,
  status: "accepted",
  value_verdict: verdictValuableG4,
};

/** method · draft · 未核验（验证后将得 inconclusive，供 verify→verdict 流程演示） */
export const gapDraftMethod: GapCandidate = {
  gap_id: "g5",
  theme: "盈余管理识别与文本语气",
  statement: "用文本语气异常构建盈余质量预警模型的方法，在中小板样本上的稳健性未被验证。",
  lens: "method",
  supporting_papers: [
    { paper_id: 9, anchor_id: "p9_b11__occ1", quote: "现有预警模型多基于财务比率，少有纳入文本语气。" },
  ],
  counter_evidence: [],
  confidence: 0.48,
  status: "draft",
  value_verdict: null,
};

/** 全部条目（按发现顺序），混合状态 → 驱动 scratchpad 实时视图与 GapPanel 分组 */
export const ALL_GAPS: GapCandidate[] = [
  gapDraftConcept,
  gapVerifiedMethod,
  gapVerifiedTheory,
  gapAcceptedConcept,
  gapDraftMethod,
];

// ============================================================
// 顶层接口形状
// ============================================================

export const scratchpadState: ScratchpadState = {
  run_id: FIXTURE_RUN_ID,
  run_status: "completed",
  entries: ALL_GAPS,
  updated_at: "2026-06-16T03:14:07Z",
};

export const discoverAccepted: GapDiscoverAccepted = { run_id: FIXTURE_RUN_ID };
export const verifyAccepted: GapVerifyAccepted = { verify_run_id: FIXTURE_VERIFY_RUN_ID };

/**
 * scratchpad 轮询「时序快照」序列 — 模拟 agent 在一次 run 内逐步累积条目并流转状态，
 * 供 B3 实时视图测试与 e2e 演示「agent 在思考」的可见性（draft→verified→accepted）。
 */
export const SCRATCHPAD_TICKS: ScratchpadState[] = [
  { run_id: FIXTURE_RUN_ID, run_status: "running", entries: [gapDraftConcept], updated_at: "2026-06-16T03:14:01Z" },
  {
    run_id: FIXTURE_RUN_ID,
    run_status: "running",
    entries: [gapDraftConcept, gapDraftMethod],
    updated_at: "2026-06-16T03:14:03Z",
  },
  {
    run_id: FIXTURE_RUN_ID,
    run_status: "running",
    entries: [gapDraftConcept, gapVerifiedMethod, gapDraftMethod],
    updated_at: "2026-06-16T03:14:05Z",
  },
  // 终态：run_status=completed → 前端据此停轮询（codex B1-P1）
  { run_id: FIXTURE_RUN_ID, run_status: "completed", entries: ALL_GAPS, updated_at: "2026-06-16T03:14:07Z" },
];
