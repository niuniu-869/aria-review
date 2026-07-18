import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { GapRunTimeline } from "../GapRunTimeline";
import type { GapRunProgress } from "../../../hooks/useGapRunStream";

function mk(p: Partial<GapRunProgress>): GapRunProgress {
  return { phase: "idle", summarizeDone: 0, summarizeTotal: 0, activity: [], error: null, ...p };
}

describe("GapRunTimeline", () => {
  it("idle/done 阶段不渲染（不占位）", () => {
    const { container: c1 } = render(<GapRunTimeline progress={mk({ phase: "idle" })} />);
    expect(c1.firstChild).toBeNull();
    const { container: c2 } = render(<GapRunTimeline progress={mk({ phase: "done" })} />);
    expect(c2.firstChild).toBeNull();
  });

  it("summarizing 阶段显示精读 N/M 进度", () => {
    render(<GapRunTimeline progress={mk({ phase: "summarizing", summarizeDone: 3, summarizeTotal: 8 })} />);
    expect(screen.getByText(/精读 3\/8 篇/)).toBeInTheDocument();
    expect(screen.getByText("精读文献中")).toBeInTheDocument();
  });

  it("subagent 活动渲染为可读文案", () => {
    render(
      <GapRunTimeline
        progress={mk({
          phase: "discovering",
          activity: [
            {
              type: "subagent_event",
              skill: "gap-finder",
              child_type: "tools_start",
              tool_calls: [{ name: "scratchpad__add" }],
              seq: 5,
            },
          ],
        })}
      />,
    );
    expect(screen.getByText(/发现 agent 调用工具：scratchpad__add/)).toBeInTheDocument();
  });

  it("error 阶段显式呈现失败（不静默）", () => {
    render(<GapRunTimeline progress={mk({ phase: "error", error: "gap-finder 越界" })} />);
    expect(screen.getByRole("alert")).toHaveTextContent("gap-finder 越界");
  });

  it("verifying 阶段显示核验中 + value-evidence 活动文案", () => {
    render(
      <GapRunTimeline
        progress={mk({
          phase: "verifying",
          activity: [
            { type: "subagent_event", skill: "value-evidence", child_type: "round_complete", tool_results: [{ tool_id: "search", success: true }], seq: 2 },
          ],
        })}
      />,
    );
    expect(screen.getByText("价值核验中")).toBeInTheDocument();
    expect(screen.getByText(/核验 agent 完成一轮/)).toBeInTheDocument();
  });

  it("feasibility-scout 活动明确标注为可行性核验", () => {
    render(
      <GapRunTimeline
        progress={mk({
          phase: "verifying",
          activity: [{ type: "subagent_event", skill: "feasibility-scout", child_type: "run_complete" }],
        })}
      />,
    );
    expect(screen.getByText("可行性核验 agent 收束")).toBeInTheDocument();
  });
});
