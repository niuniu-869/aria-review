/**
 * AiEmpty.tsx — 统一空态引导 (A7)
 *
 * 未输入/未运行时的引导文案, 三面板一致排版 (复用 .muted)。
 */
export function AiEmpty({ children }: { children: React.ReactNode }) {
  return <p className="muted ai-empty">{children}</p>;
}
