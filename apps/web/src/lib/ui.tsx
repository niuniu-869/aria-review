// 共享的查询态展示 (DRY: 各分析页复用)
export function Loading({ label }: { label: string }) {
  return (
    <div className="state" aria-live="polite">
      <span className="spinner" /> {label}
    </div>
  );
}

export function ErrMsg({ error }: { error: unknown }) {
  return (
    <div className="state state-err" role="alert">
      {(error as Error)?.message ?? "出错了"}
    </div>
  );
}

// 作者格式化 (DRY): 兼容字符串与 CSL 对象 ({literal} / {family,given})
type CreatorLike = string | { family?: string; given?: string; literal?: string };
export function formatCreators(creators?: CreatorLike[]): string {
  if (!creators || creators.length === 0) return "";
  return creators
    .map((c) => {
      if (typeof c === "string") return c;
      if (c.literal) return c.literal;
      return [c.given, c.family].filter(Boolean).join(" ");
    })
    .filter(Boolean)
    .join("; ");
}

// 表格单元格样式 (沿用; 与全局 table.tbl 协同)
export const cell: React.CSSProperties = {
  borderBottom: "1px solid var(--line)",
  padding: "6px 10px",
  textAlign: "left",
};
