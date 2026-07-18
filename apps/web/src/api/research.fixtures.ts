/**
 * research.fixtures.ts — packages/contracts/fixtures/research_gap.json 的类型化导出层。
 *
 * 数据单一真源在后端脚本生成的共享 JSON；这里仅保留既有命名导出，避免组件测试关心 JSON
 * 内部组织方式。若后端契约变更，重跑 services/agent/scripts/gen_contract_fixtures.py 即可。
 */
import researchFixtures from "../../../../packages/contracts/fixtures/research_gap.json" with { type: "json" };
import type {
  EvidencePack,
  FeasibilityPack,
  FeasibilityVerdict,
  GapCandidate,
  GapDiscoverAccepted,
  GapFeasibilityAccepted,
  GapFeasibilityVerdictResult,
  GapVerdictResult,
  GapVerifyAccepted,
  ScratchpadState,
  ValueThresholds,
  ValueVerdict,
} from "../types/research";

const data = researchFixtures as unknown as {
  FIXTURE_PID: number;
  FIXTURE_CID: string;
  FIXTURE_RUN_ID: string;
  FIXTURE_VERIFY_RUN_ID: string;
  FIXTURE_FEASIBILITY_RUN_ID: string;
  THRESHOLDS: ValueThresholds;
  verdictValuableG2: ValueVerdict;
  verdictValuableG4: ValueVerdict;
  verdictLikelyFilledG3: ValueVerdict;
  verdictInconclusiveG5: ValueVerdict;
  evidenceG2: EvidencePack;
  evidenceG3: EvidencePack;
  evidenceG5: EvidencePack;
  evidenceG4: EvidencePack;
  verdictResultG2: GapVerdictResult;
  verdictResultG3: GapVerdictResult;
  verdictResultG4: GapVerdictResult;
  verdictResultG5: GapVerdictResult;
  ALL_VERDICT_RESULTS: GapVerdictResult[];
  feasibilityVerdictG2: FeasibilityVerdict;
  feasibilityPackG2: FeasibilityPack;
  feasibilityResultG2: GapFeasibilityVerdictResult;
  gapDraftConcept: GapCandidate;
  gapVerifiedMethod: GapCandidate;
  gapVerifiedTheory: GapCandidate;
  gapAcceptedConcept: GapCandidate;
  gapDraftMethod: GapCandidate;
  ALL_GAPS: GapCandidate[];
  scratchpadState: ScratchpadState;
  discoverAccepted: GapDiscoverAccepted;
  verifyAccepted: GapVerifyAccepted;
  feasibilityAccepted: GapFeasibilityAccepted;
  SCRATCHPAD_TICKS: ScratchpadState[];
};

export const FIXTURE_PID = data.FIXTURE_PID;
export const FIXTURE_CID = data.FIXTURE_CID;
export const FIXTURE_RUN_ID = data.FIXTURE_RUN_ID;
export const FIXTURE_VERIFY_RUN_ID = data.FIXTURE_VERIFY_RUN_ID;
export const FIXTURE_FEASIBILITY_RUN_ID = data.FIXTURE_FEASIBILITY_RUN_ID;
export const THRESHOLDS = data.THRESHOLDS;

export const verdictValuableG2 = data.verdictValuableG2;
export const verdictValuableG4 = data.verdictValuableG4;
export const verdictLikelyFilledG3 = data.verdictLikelyFilledG3;
export const verdictInconclusiveG5 = data.verdictInconclusiveG5;

export const evidenceG2 = data.evidenceG2;
export const evidenceG3 = data.evidenceG3;
export const evidenceG5 = data.evidenceG5;
export const evidenceG4 = data.evidenceG4;

export const verdictResultG2 = data.verdictResultG2;
export const verdictResultG3 = data.verdictResultG3;
export const verdictResultG4 = data.verdictResultG4;
export const verdictResultG5 = data.verdictResultG5;
export const ALL_VERDICT_RESULTS = data.ALL_VERDICT_RESULTS;
export const feasibilityVerdictG2 = data.feasibilityVerdictG2;
export const feasibilityPackG2 = data.feasibilityPackG2;
export const feasibilityResultG2 = data.feasibilityResultG2;

export const gapDraftConcept = data.gapDraftConcept;
export const gapVerifiedMethod = data.gapVerifiedMethod;
export const gapVerifiedTheory = data.gapVerifiedTheory;
export const gapAcceptedConcept = data.gapAcceptedConcept;
export const gapDraftMethod = data.gapDraftMethod;
export const ALL_GAPS = data.ALL_GAPS;

export const scratchpadState = data.scratchpadState;
export const discoverAccepted = data.discoverAccepted;
export const verifyAccepted = data.verifyAccepted;
export const feasibilityAccepted = data.feasibilityAccepted;
export const SCRATCHPAD_TICKS = data.SCRATCHPAD_TICKS;
