/**
 * FeasibilityVerdictCard.tsx — 可行性状态机裁决卡。
 *
 * 与 ValueVerdictCard 共用 vv-* 可信视觉 tokens；只展示后端真实返回的状态、理由与
 * 三类关键证据摘要，不在前端二次打分或改写裁决。
 */
import type {
  FeasibilityPack,
  FeasibilityVerdict,
  GapFeasibilityVerdictResult,
} from "../../types/research";

type VerdictKind = FeasibilityVerdict["verdict"];

const VERDICT_META: Record<VerdictKind, { label: string; cls: string }> = {
  buildable: { label: "可做", cls: "fv-buildable" },
  hard: { label: "有难度", cls: "fv-hard" },
  blocked: { label: "明确受阻", cls: "fv-blocked" },
};

const STATUS_LABEL = {
  data: { available: "数据可得", unknown: "数据待确认", unavailable: "数据不可得" },
  method: { supported: "方法有基座", unknown: "方法待确认", blocked: "方法受阻" },
  resource: { modest: "资源适中", heavy: "资源较重", unknown: "资源待确认" },
} as const;

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function namedItems(block: unknown, key: string): string[] {
  const record = asRecord(block);
  const items = record?.[key];
  if (!Array.isArray(items)) return [];
  return items
    .map((item) => asRecord(item)?.name)
    .filter((name): name is string => typeof name === "string" && name.trim().length > 0);
}

function stringField(block: unknown, key: string): string | null {
  const value = asRecord(block)?.[key];
  return typeof value === "string" && value.trim() ? value : null;
}

function evidenceSummary(pack: FeasibilityPack | null): Array<{ label: string; value: string }> {
  if (!pack) return [];
  const datasets = namedItems(pack.data_availability, "datasets");
  const methods = namedItems(pack.method_base, "building_blocks");
  const sampleSize = stringField(pack.resource_scale, "typical_sample_size");
  const compute = stringField(pack.resource_scale, "typical_compute");
  const resourceNote = stringField(pack.resource_scale, "note");
  const resource = [sampleSize, compute, resourceNote].filter(Boolean).join(" · ");

  return [
    datasets.length ? { label: "数据证据", value: datasets.slice(0, 3).join("、") } : null,
    methods.length ? { label: "方法基座", value: methods.slice(0, 3).join("、") } : null,
    resource ? { label: "资源规模", value: resource } : null,
  ].filter((item): item is { label: string; value: string } => item != null);
}

export interface FeasibilityVerdictCardProps {
  result: GapFeasibilityVerdictResult;
}

function ShieldIcon() {
  return (
    <span className="vv-shield" aria-hidden="true">
      <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8">
        <path d="M12 2 4 5v6c0 5 3.5 8.5 8 11 4.5-2.5 8-6 8-11V5l-8-3Z" />
        <path d="m8.5 14.5 3.5-6 3.5 6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </span>
  );
}

export function FeasibilityVerdictCard({ result }: FeasibilityVerdictCardProps) {
  const { verdict, pack } = result;
  const meta = VERDICT_META[verdict.verdict];
  const evidence = evidenceSummary(pack);

  return (
    <section className={`card vv-card fv-card ${meta.cls}`} aria-label="可行性裁决卡">
      <div className="vv-head">
        <ShieldIcon />
        <h3 className="vv-title">研究方向可行性裁决</h3>
        <span className="vv-decided" title="裁决由确定性状态机给出，非 LLM 生成">
          确定性裁决 · 非 LLM
        </span>
      </div>

      <div className="vv-verdict-row">
        <span className={`vv-verdict-pill ${meta.cls}`}>{meta.label}</span>
        <span className="fv-status">{STATUS_LABEL.data[verdict.data_status]}</span>
        <span className="fv-status">{STATUS_LABEL.method[verdict.method_status]}</span>
        <span className="fv-status">{STATUS_LABEL.resource[verdict.resource_status]}</span>
      </div>
      {verdict.rationale && <p className="vv-rationale">{verdict.rationale}</p>}

      {evidence.length > 0 && (
        <div className="vv-section">
          <div className="vv-section-label">关键证据摘要</div>
          <dl className="fv-evidence">
            {evidence.map((item) => (
              <div className="fv-evidence-row" key={item.label}>
                <dt>{item.label}</dt>
                <dd>{item.value}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}

      {!!pack?.negative_evidence?.length && (
        <div className="fv-negative" role="note">
          已记录 {pack.negative_evidence.length} 条明确负证据，请结合裁决理由复核。
        </div>
      )}
    </section>
  );
}
