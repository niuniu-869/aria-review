/**
 * AiResult.tsx — 统一结果区 + 面板外壳 (A7)
 *
 *  - AiPanel:  统一面板外壳 (标题宋体 + 简介 + 内容), 三 AI 面板一致间距。
 *  - AiResultBox: 统一结果容器 (流式区/结果区共用边框+内边距+滚动)。
 */
import type { ReactNode } from "react";

export function AiPanel({
  title,
  intro,
  children,
}: {
  title: string;
  intro?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="ai-panel">
      <h2 className="ai-panel-title">{title}</h2>
      {intro && <p className="muted ai-panel-intro">{intro}</p>}
      {children}
    </section>
  );
}

/** 结果/流式容器 (统一边框/内边距/滚动) */
export function AiResultBox({
  children,
  scroll = false,
  live = false,
}: {
  children: ReactNode;
  /** 是否限高滚动 (对话历史用) */
  scroll?: boolean;
  live?: boolean;
}) {
  return (
    <div
      className={`card ai-result-box${scroll ? " ai-result-scroll" : ""}`}
      aria-live={live ? "polite" : undefined}
    >
      {children}
    </div>
  );
}
