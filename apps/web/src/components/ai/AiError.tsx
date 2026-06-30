/**
 * AiError.tsx — 统一错误样式 (A7)
 *
 * 取代三面板散落的 inline { color: "crimson" }; 统一用 .state-err + role="alert"。
 */
export function AiError({ message }: { message?: string | null }) {
  if (!message) return null;
  return (
    <p className="state-err ai-error" role="alert">
      {message}
    </p>
  );
}
