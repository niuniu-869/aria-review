/**
 * ReviewTemplatePicker.tsx — 6 论型卡（综述模板选择器）
 *
 * 论型：undergrad(本科3章) / master(硕士4章) / phd(博士5章) /
 *       grant(基金3章) / proposal(开题3章) / sci_intro(SCI英文3章)
 *
 * 每张卡展示：论型名称 + 适用场景 + 章节数预览
 * 点击 → onPick(paperType)
 *
 * 无障碍：每张卡为 button，支持键盘 Enter/Space。
 */

interface ReviewTemplatePickerProps {
  onPick: (paperType: string) => void;
  /** 当前选中（可选，高亮） */
  selected?: string | null;
}

interface TemplateInfo {
  key: string;
  name: string;
  scene: string;
  chapters: number;
  chapterNames: string[];
}

export const TEMPLATE_LIST: TemplateInfo[] = [
  {
    key: "undergrad",
    name: "本科毕业论文综述",
    scene: "本科毕设，3000-5000 字",
    chapters: 3,
    chapterNames: ["研究背景与意义", "国内外研究现状", "研究述评与展望"],
  },
  {
    key: "master",
    name: "硕士论文综述",
    scene: "硕士毕业论文文献综述，6000-10000 字",
    chapters: 4,
    chapterNames: ["研究背景与理论基础", "核心概念与研究进展", "方法论述评", "研究不足与展望"],
  },
  {
    key: "phd",
    name: "博士论文综述",
    scene: "博士学位论文，12000-20000 字",
    chapters: 5,
    chapterNames: [
      "研究背景与理论基础",
      "主题聚类与方法学进展",
      "核心争议与知识断层",
      "跨学科视角与创新路径",
      "研究述评与展望",
    ],
  },
  {
    key: "grant",
    name: "基金申报综述",
    scene: "国家自然科学基金、省级课题立项",
    chapters: 3,
    chapterNames: ["研究现状", "存在问题", "研究切入点"],
  },
  {
    key: "proposal",
    name: "开题报告综述",
    scene: "研究生开题答辩文献综述部分",
    chapters: 3,
    chapterNames: ["研究现状与主要进展", "理论框架梳理", "研究问题与创新点"],
  },
  {
    key: "sci_intro",
    name: "SCI 论文引言综述",
    scene: "英文期刊论文 Introduction 段落（中英双语输出）",
    chapters: 3,
    chapterNames: ["Background & Motivation", "Related Work", "Gap & Contribution"],
  },
];

export function ReviewTemplatePicker({ onPick, selected }: ReviewTemplatePickerProps) {
  return (
    <div className="review-template-picker" role="list" aria-label="选择论型模板">
      {TEMPLATE_LIST.map((tpl) => (
        <button
          key={tpl.key}
          className={`review-template-card${selected === tpl.key ? " review-template-card--selected" : ""}`}
          onClick={() => onPick(tpl.key)}
          aria-label={`${tpl.name}，${tpl.scene}，共 ${tpl.chapters} 章`}
          aria-pressed={selected === tpl.key}
        >
          <div className="rtc-header">
            <span className="rtc-name">{tpl.name}</span>
            <span className="rtc-chapters">{tpl.chapters} 章</span>
          </div>
          <p className="rtc-scene">{tpl.scene}</p>
          <ol className="rtc-chapters-list">
            {tpl.chapterNames.map((ch, i) => (
              <li key={i} className="rtc-chapter-item">
                {ch}
              </li>
            ))}
          </ol>
        </button>
      ))}
    </div>
  );
}
