/**
 * research.ts — 研究副驾（GAP 发现 + 价值核验）契约 TS 类型（前端真相）。
 *
 * 单一真相 = packages/contracts/openapi.yaml 的 research schemas。
 * 这里只做 components["schemas"] 的「别名导出」，禁手写字段（手写=漂移源）。
 * 改契约形状须先改 openapi.yaml → `npm run gen:api` → 这里别名随动。
 *
 * 可空性硬约束（B1 重点）：value_verdict|null、year|null、doi|null、score|null、
 *   source_view|null。fixture/UI 全程按可空处理，绝不假定非空。
 *
 * 分层铁律映射：ValueVerdict.decided_by 恒为 "deterministic"（裁决由确定性 resolver
 *   出，非 LLM）；EvidencePack.gathered_by 恒为 "subagent"（工具只攒证不裁决）。
 */
import type { components } from "../api/schema";

type Schemas = components["schemas"];

/** GAP 透镜（概念/方法/理论；领域无关） */
export type GapLens = Schemas["GapLens"];
/** GAP 生命周期（HITL 流转 draft→verified→accepted/rejected） */
export type GapStatus = Schemas["GapStatus"];
/** 支撑论文证据（带源坐标 anchor_id） */
export type GapSupportingPaper = Schemas["GapSupportingPaper"];
/** 反证/张力证据 */
export type GapCounterEvidence = Schemas["GapCounterEvidence"];
/** 结构化 GAP 候选（scratchpad 条目） */
export type GapCandidate = Schemas["GapCandidate"];

/** 反向检索命中条目（year/doi 可空） */
export type ReverseSearchHit = Schemas["ReverseSearchHit"];
/** 反向检索证据 */
export type ReverseSearch = Schemas["ReverseSearch"];
/** 计量结构佐证（source_view 可空） */
export type BiblioStructure = Schemas["BiblioStructure"];
/** 价值核验证据包（gathered_by 恒为 subagent） */
export type EvidencePack = Schemas["EvidencePack"];

/** 透明阈值 */
export type ValueThresholds = Schemas["ValueThresholds"];
/** 研究方向价值裁决（decided_by 恒为 deterministic；score 可空） */
export type ValueVerdict = Schemas["ValueVerdict"];
/** GET .../verdict 返回体：裁决 + 证据包 */
export type GapVerdictResult = Schemas["GapVerdictResult"];
/** 可行性裁决（状态机：buildable | hard | blocked） */
export type FeasibilityVerdict = Schemas["FeasibilityVerdict"];
/** 可行性侦察证据包 */
export type FeasibilityPack = Schemas["FeasibilityPack"];
/** GET .../feasibility-verdict 返回体 */
export type GapFeasibilityVerdictResult = Schemas["GapFeasibilityVerdictResult"];

/** 本 run 实时工作记忆快照 */
export type ScratchpadState = Schemas["ScratchpadState"];

/** :discover 202 受理体 */
export type GapDiscoverAccepted = Schemas["GapDiscoverAccepted"];
/** :verify 202 受理体 */
export type GapVerifyAccepted = Schemas["GapVerifyAccepted"];
/** :feasibility 202 受理体 */
export type GapFeasibilityAccepted = Schemas["GapFeasibilityAccepted"];
/** HITL 决策请求体 */
export type GapPatchRequest = Schemas["GapPatchRequest"];
/** HITL 决策动作：accept | reject | revise */
export type GapPatchAction = GapPatchRequest["action"];

/** verdict 三态字面量并集（valuable | likely_filled | inconclusive），供 UI 穷举渲染 */
export type ValueVerdictKind = ValueVerdict["verdict"];
