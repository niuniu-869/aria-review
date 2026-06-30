/**
 * AiInput.tsx — 统一输入区原语 (A7)
 *
 * 三 AI 面板共用的输入控件, 一致间距/边框/focus 态 (复用 .input/.btn 设计系统):
 *  - AiToolbar:  顶部控件行 (功能选择/方向/动作下拉等), 统一 flex/gap/wrap。
 *  - AiTextarea: 多行输入 (工具台粘贴文本 / 综述无), 统一 .input + 全宽。
 *  - AiTextInput: 单行输入 (对话提问 / 综述主题), 统一 .input。
 *  - AiActions:  操作按钮行 (运行/发送/生成), 统一右对齐 + .btn-primary。
 */
import type { ReactNode } from "react";

/** 顶部控件行 (下拉/切换等) */
export function AiToolbar({ children }: { children: ReactNode }) {
  return <div className="ai-toolbar">{children}</div>;
}

/** 一个带标签的字段 (label + 控件) */
export function AiField({ label, htmlFor, children }: { label: string; htmlFor?: string; children: ReactNode }) {
  return (
    <label className="ai-field" htmlFor={htmlFor}>
      <span className="ai-field-label">{label}</span>
      {children}
    </label>
  );
}

export function AiTextarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const { className, rows, ...rest } = props;
  return <textarea className={`input ai-textarea${className ? " " + className : ""}`} rows={rows ?? 6} {...rest} />;
}

export function AiTextInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  const { className, ...rest } = props;
  return <input className={`input ai-text-input${className ? " " + className : ""}`} {...rest} />;
}

/** 操作按钮行 (右对齐) */
export function AiActions({ children }: { children: ReactNode }) {
  return <div className="ai-actions">{children}</div>;
}
