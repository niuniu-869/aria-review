/**
 * EmptyGuide.tsx — AI 对话页空状态引导
 *
 * 在 events.length===0 && !running 时渲染，代替对话区的空白：
 *   1. 助手自我介绍文案
 *   2. CapabilityCards — 4 张能力卡
 *   3. PresetLauncher — 预设提示词启动器
 *
 * 点击能力卡 / 预设 → 调 onFill(payload) 填入 AgentChat 输入框（不自动发送）
 */
import { CapabilityCards } from "./CapabilityCards";
import { PresetLauncher } from "./PresetLauncher";
import type { FillPayload } from "./PresetLauncher";
import type { ProjectLibraryStats } from "../api/agentHooks";

interface EmptyGuideProps {
  onFill: (payload: FillPayload) => void;
  stats?: ProjectLibraryStats | null;
  /** 导航型能力卡（研究空白）跳转回调，透传给 CapabilityCards */
  onNavigate?: (to: string) => void;
}

export function EmptyGuide({ onFill, stats, onNavigate }: EmptyGuideProps) {
  const hasIncluded = (stats?.inclusion?.included ?? 0) > 0;

  return (
    <div className="empty-guide" role="region" aria-label="AI 工作台功能引导">
      {/* 助手自我介绍 */}
      <div className="empty-guide-intro">
        <div className="empty-guide-avatar" aria-hidden="true">
          📖
        </div>
        <div className="empty-guide-text">
          <h2 className="empty-guide-title">我是文献综述助手</h2>
          <p className="empty-guide-sub">
            我能检索、筛选、计量分析并生成<strong>分章文献综述</strong>——我写的每一句都标注真实文献来源，内置<strong>零伪造</strong>约束，整个过程<strong>可哈希验证</strong>。
            {hasIncluded
              ? " 项目已有纳入文献，可直接选论型开始综述。"
              : " 从导入或检索文献开始，构建属于你的综述语料库。"}
          </p>
        </div>
      </div>

      {/* 5 张能力卡（含「研究空白」导航卡） */}
      <CapabilityCards onFill={onFill} onNavigate={onNavigate} />

      {/* 预设提示词启动器 */}
      <PresetLauncher onFill={onFill} />
    </div>
  );
}
