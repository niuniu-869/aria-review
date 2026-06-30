/**
 * AnalysisFrame.tsx — 分析区统一框架（四件套壳）
 *
 * 职责：提供一致的标题栏 + 容器，包裹 13 个现有分析 Panel。
 * KISS 原则：面板已自带图表/数据表，Frame 只提供标题与容器，
 *            不强塞 params/export 改写面板内部（导出增强留 M5）。
 *
 * props：
 *   title   — 视图标题（如"领域概览"）
 *   desc    — 一句话简述
 *   stale   — 是否显示「纳入集已变」提示条（AnalysisView 层已处理，Frame 不重复显示）
 *   children — 要渲染的 *Panel 组件
 */

import type { ReactNode } from "react";

interface AnalysisFrameProps {
  title: string;
  desc: string;
  children: ReactNode;
}

export function AnalysisFrame({ title, desc, children }: AnalysisFrameProps) {
  return (
    <div className="analysis-frame">
      {/* 标题栏 */}
      <div className="analysis-frame-header">
        <div>
          <h3 className="analysis-frame-title">{title}</h3>
          <p className="analysis-frame-desc">{desc}</p>
        </div>
      </div>

      {/* 面板主体 */}
      <div className="analysis-frame-body">
        {children}
      </div>
    </div>
  );
}
