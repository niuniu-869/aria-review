/**
 * AiKeyNotice.tsx — 统一 LLM key 缺失温和提示 (A7)
 *
 * 三 AI 面板 (对话/工具/综述) 共用同一温和提示, 风格对齐 A6 ScreenPanel:
 * "未配置 LLM key, 将使用占位评分(仍可体验流程)。可在「设置」中填入 key 获得真实 AI 输出。"
 * 复用 .muted, 不散落 inline hex。
 */
export function AiKeyNotice({ hasKey }: { hasKey: boolean }) {
  // 内测部署：后端已注入 DeepSeek / Sciverse / MinerU 服务端 key（VITE_SERVER_KEYS=1 构建），
  // 用户无需自行配置，AI 输出为真实模型结果 —— 此提示在该模式下不显示（否则误导为"占位输出"）。
  if (import.meta.env.VITE_SERVER_KEYS === "1") return null;
  if (hasKey) return null;
  return (
    <p className="muted ai-key-notice">
      未配置 LLM key，将使用占位输出（仍可体验流程）。可在「设置」中填入 key 获得真实 AI 输出。
    </p>
  );
}
