/**
 * PresetLauncher.tsx — 预设提示词启动器
 *
 * 预设列表：检索文献 / 筛选纳排 / 计量分析 / 综述（各论型）
 * 点击 chip → onFill({prompt, paperType?}) 填入 AgentChat 输入框（可编辑，不自动发送）
 *
 * 综述预设内嵌 ReviewTemplatePicker，选论型后组装规范提示词：
 *   「请使用论型模板【博士论文综述】(paper_type: phd)，基于当前项目已纳入语料生成一份分章文献综述，
 *     按章节大纲用 ## 标题展开，并标注真实文献引用。」
 */
import { useState } from "react";
import { ReviewTemplatePicker, TEMPLATE_LIST } from "./ReviewTemplatePicker";

export interface FillPayload {
  prompt: string;
  paperType?: string;
}

interface PresetLauncherProps {
  onFill: (payload: FillPayload) => void;
}

/** 综述预设：按 paperType 组装规范提示词，确保 agent 能可靠识别 paper_type。
 *  章节大纲由后端模板驱动，前端不硬编码具体章节标题。 */
function buildReviewPrompt(paperType: string): string {
  const tpl = TEMPLATE_LIST.find((t) => t.key === paperType);
  if (!tpl) return "";
  return (
    `请使用论型模板【${tpl.name}】(paper_type: ${paperType})，` +
    `基于当前项目已纳入语料生成一份分章文献综述。` +
    `请严格按该论型的章节大纲，用 ## 标题分章输出综述正文（一次性输出全文），` +
    `并标注真实文献引用（[n] 为语料行号）。` +
    `\n【抗幻觉约束】每个论点必须来自真实文献，严禁编造；` +
    `语料不足请写"（语料未覆盖，需补充检索）"。`
  );
}

// 简单预设（非综述类）
const SIMPLE_PRESETS = [
  {
    label: "检索文献",
    prompt:
      "请帮我检索与当前研究主题相关的文献。请先确认研究主题，然后设计检索式，搜索文献并导入到项目文献库。",
  },
  {
    label: "筛选纳排",
    prompt:
      "请对当前项目的候选文献进行批量筛选评估，按照文献综述的纳入/排除标准，给出每篇文献的筛选建议并说明理由。",
  },
  {
    label: "计量分析",
    prompt:
      "请对已纳入文献进行文献计量分析：(1) 年度发文趋势；(2) 关键词聚类与热点；(3) 高被引文献；(4) 研究演化脉络。",
  },
];

// 综述快捷按钮（最常用 3 个）
const REVIEW_SHORTCUTS = [
  { label: "写【博士】综述", paperType: "phd" },
  { label: "写【硕士】综述", paperType: "master" },
  { label: "写【本科】综述", paperType: "undergrad" },
];

export function PresetLauncher({ onFill }: PresetLauncherProps) {
  const [showPicker, setShowPicker] = useState(false);

  function handleSimple(prompt: string) {
    onFill({ prompt });
  }

  function handleReviewShortcut(paperType: string) {
    onFill({ prompt: buildReviewPrompt(paperType), paperType });
  }

  function handlePickTemplate(paperType: string) {
    setShowPicker(false);
    onFill({ prompt: buildReviewPrompt(paperType), paperType });
  }

  return (
    <div className="preset-launcher" aria-label="预设提示词启动器">
      {/* 简单预设 chips */}
      <div className="preset-group">
        <span className="preset-group-label">快速开始</span>
        <div className="preset-chips">
          {SIMPLE_PRESETS.map((p) => (
            <button
              key={p.label}
              className="preset-chip"
              onClick={() => handleSimple(p.prompt)}
              aria-label={p.label}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* 综述快捷 */}
      <div className="preset-group">
        <span className="preset-group-label">写综述（快捷）</span>
        <div className="preset-chips">
          {REVIEW_SHORTCUTS.map((s) => (
            <button
              key={s.label}
              className="preset-chip preset-chip--review"
              onClick={() => handleReviewShortcut(s.paperType)}
              aria-label={s.label}
            >
              {s.label}
            </button>
          ))}
          <button
            className="preset-chip preset-chip--more"
            onClick={() => setShowPicker((v) => !v)}
            aria-expanded={showPicker}
            aria-label="更多论型…"
          >
            更多论型…
          </button>
        </div>
      </div>

      {/* 论型详细选择器（展开/折叠） */}
      {showPicker && (
        <div className="preset-picker-panel" role="region" aria-label="选择综述论型">
          <div className="preset-picker-header">
            <span>选择综述论型</span>
            <button
              className="btn btn-ghost"
              onClick={() => setShowPicker(false)}
              aria-label="收起论型选择器"
            >
              收起
            </button>
          </div>
          <ReviewTemplatePicker onPick={handlePickTemplate} />
        </div>
      )}
    </div>
  );
}
