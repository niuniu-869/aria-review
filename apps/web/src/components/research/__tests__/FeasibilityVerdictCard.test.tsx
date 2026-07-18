import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FeasibilityVerdictCard } from "../FeasibilityVerdictCard";
import { feasibilityResultG2 } from "../../../api/research.fixtures";
import type { GapFeasibilityVerdictResult } from "../../../types/research";

describe("FeasibilityVerdictCard", () => {
  it("按后端真实返回体展示 verdict、rationale 与关键证据摘要", () => {
    render(<FeasibilityVerdictCard result={feasibilityResultG2} />);
    expect(screen.getByRole("region", { name: "可行性裁决卡" })).toBeInTheDocument();
    expect(screen.getByText("可做")).toBeInTheDocument();
    expect(screen.getByText("数据可得")).toBeInTheDocument();
    expect(screen.getByText("方法有基座")).toBeInTheDocument();
    expect(screen.getByText("资源适中")).toBeInTheDocument();
    expect(screen.getByText(/数据有明确可访问证据/)).toBeInTheDocument();
    expect(screen.getByText("巨潮资讯年报语料")).toBeInTheDocument();
    expect(screen.getByText(/Sentence-BERT embeddings/)).toBeInTheDocument();
    expect(screen.getByText(/10k-50k reports/)).toBeInTheDocument();
    expect(screen.getByText(/确定性裁决/)).toBeInTheDocument();
  });

  it("blocked 时展示明确受阻与负证据计数", () => {
    const blocked = {
      ...feasibilityResultG2,
      verdict: {
        ...feasibilityResultG2.verdict,
        verdict: "blocked",
        data_status: "unavailable",
        rationale: "明确不可行 blocker：data_status=unavailable。",
      },
      pack: {
        ...feasibilityResultG2.pack,
        negative_evidence: [{ kind: "data_unavailable", note: "数据为专有库" }],
      },
    } as GapFeasibilityVerdictResult;
    render(<FeasibilityVerdictCard result={blocked} />);
    expect(screen.getByText("明确受阻")).toBeInTheDocument();
    expect(screen.getByText("数据不可得")).toBeInTheDocument();
    expect(screen.getByText(/已记录 1 条明确负证据/)).toBeInTheDocument();
  });
});
