/**
 * CapabilityCards.tsx — 5 张能力卡（🔍检索建库 / 🗂筛选纳排 / 📊计量分析 / 📝一键综述 / 🧭研究空白）
 *
 * 每张卡点击触发 onFill 填充对应预设，或（带 to 的导航卡）跳转到对应功能区。
 * 无障碍：每张卡为 button 角色，支持键盘 Enter/Space 触发。
 */

interface CapabilityCardsProps {
  /** 点击能力卡时填入预设提示词 */
  onFill: (opts: { prompt: string; paperType?: string }) => void;
  /** 导航型能力卡（如「研究空白」）点击时跳转到项目内子区（相对路径，如 "research"） */
  onNavigate?: (to: string) => void;
}

interface Capability {
  icon: string;
  title: string;
  desc: string;
  prompt: string;
  /** 若设置，点击走导航（onNavigate）而非填入提示词 */
  to?: string;
}

const CAPABILITIES: Capability[] = [
  {
    icon: "🔍",
    title: "检索建库",
    desc: "上传 PDF/ZIP 或检索文献，构建项目文献库",
    prompt:
      "请帮我检索并导入相关文献。我正在研究的主题是：（请补充研究主题）。请先搜索现有文献，然后建议合适的检索词，帮我把结果导入到项目文献库。",
  },
  {
    icon: "🗂",
    title: "筛选纳排",
    desc: "按纳排标准自动/半自动批量筛选文献",
    prompt:
      "请帮我筛选文献，按照纳入/排除标准对候选文献进行批量评估。请先查看项目中的候选文献，然后根据研究主题和常用纳排标准，给出每篇文献的纳排建议（纳入/排除/需人工复核）并说明理由。",
  },
  {
    icon: "📊",
    title: "计量分析",
    desc: "年度趋势、共被引网络、主题聚类等文献计量图",
    prompt:
      "请对当前已纳入文献进行文献计量分析，包括：(1) 年度发文趋势；(2) 高频关键词与主题聚类；(3) 核心期刊与高被引文献；(4) 研究热点演变。请给出分析结果并提供可视化建议。",
  },
  {
    icon: "📝",
    title: "一键综述",
    desc: "选择综述论型模板，生成分章文献综述初稿",
    prompt:
      "请基于当前项目已纳入语料，生成一份分章文献综述。请先确认研究主题，然后选择合适的论型模板，按章节大纲展开综述正文，并标注真实文献引用。",
  },
  {
    icon: "🧭",
    title: "研究空白",
    desc: "发现结构化研究空白并确定性核验其价值（HITL）",
    prompt: "",
    to: "research",
  },
];

export function CapabilityCards({ onFill, onNavigate }: CapabilityCardsProps) {
  return (
    <div className="capability-cards" role="list">
      {CAPABILITIES.map((cap) => (
        <button
          key={cap.title}
          data-testid="capability-card"
          className="capability-card"
          // 导航卡缺 onNavigate 时禁用，避免可点击但无行为的死卡（codex B5-P2）
          disabled={!!cap.to && !onNavigate}
          onClick={() => (cap.to ? onNavigate?.(cap.to) : onFill({ prompt: cap.prompt }))}
          aria-label={`${cap.icon} ${cap.title}：${cap.desc}`}
        >
          <span className="cap-icon" aria-hidden="true">
            {cap.icon}
          </span>
          <span className="cap-title">{cap.title}</span>
          <span className="cap-desc">{cap.desc}</span>
        </button>
      ))}
    </div>
  );
}
