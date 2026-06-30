function safeFilenamePart(value: string): string {
  const cleaned = value.trim().replace(/[\\/:*?"<>|]+/g, "-").replace(/\s+/g, "-");
  return cleaned ? cleaned.slice(0, 80) : "export";
}

export function downloadMarkdown(filenameBase: string, content: string) {
  // 加 UTF-8 BOM：导出本身是合法 UTF-8，但中文系统(记事本/Word/Excel)默认按 GBK 打开会乱码；BOM 让其自动识别。
  const blob = new Blob(["\uFEFF" + content], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${safeFilenamePart(filenameBase)}.md`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
