import { useState } from "react";
import { downloadReport, getCite, ApiError, type ReportFormat, type ReportSection } from "../api/client";

type CiteStyle = "apa" | "gbt7714" | "mla";

// 可由现有 DTO 组装的章节 (prisma/review 需外部内容, 此处不在主流程提供 → 后端渲染"未提供"提示;
// A7 YAGNI: 报告面板默认只勾选数据已就位的章节)。
const SECTION_OPTIONS: { id: ReportSection; label: string }[] = [
  { id: "overview", label: "领域概览" },
  { id: "sources", label: "核心期刊" },
  { id: "authors", label: "核心作者" },
  { id: "documents", label: "关键词 / 高被引" },
  { id: "references", label: "参考文献" },
];

const DEFAULT_SECTIONS: ReportSection[] = ["overview", "sources", "authors", "documents", "references"];

export function ReportPanel({ projectId, corpusId }: { projectId: string; corpusId: string }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [style, setStyle] = useState<CiteStyle>("apa");

  // A7: 报告元数据 + 章节多选
  const [title, setTitle] = useState("文献计量分析报告");
  const [author, setAuthor] = useState("");
  const [sections, setSections] = useState<Set<ReportSection>>(new Set(DEFAULT_SECTIONS));
  // DOCX 可用性: 默认可用, 捕获 503 后降级隐藏 DOCX 按钮
  const [docxAvailable, setDocxAvailable] = useState(true);

  function toggleSection(id: ReportSection) {
    setSections((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function dlReport(format: ReportFormat) {
    setBusy(format);
    setErr(null);
    try {
      const chosen = SECTION_OPTIONS.filter((s) => sections.has(s.id)).map((s) => s.id);
      await downloadReport(projectId, corpusId, format, {
        title: title.trim() || "文献计量分析报告",
        author: author.trim() || undefined,
        sections: chosen,
      });
    } catch (e) {
      // 仅 pandoc 永久缺失(PANDOC_UNAVAILABLE) 才隐藏 DOCX 按钮; 转换超时(PANDOC_TIMEOUT)
      // 是可重试故障, 保留按钮并展示后端文案 (codex A7 P2: 勿把超时当缺失永久降级)。
      if (e instanceof ApiError && e.code === "PANDOC_UNAVAILABLE") {
        setDocxAvailable(false);
        setErr("服务端暂不支持 DOCX 导出（缺少 pandoc），已切换为仅 Markdown / HTML。");
      } else {
        setErr((e as Error).message);
      }
    } finally {
      setBusy(null);
    }
  }

  async function dlCite() {
    setBusy("cite");
    setErr(null);
    try {
      const r = await getCite(projectId, corpusId, style);
      // 加 UTF-8 BOM：导出本身是合法 UTF-8，但中文系统(记事本/Excel)默认按 GBK 打开会乱码；
      // BOM 让其自动识别 UTF-8。
      const blob = new Blob(["\uFEFF" + r.citations.join("\n")], { type: "text/plain;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `citations-${style}.txt`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  const noSection = sections.size === 0;

  return (
    <section className="report-panel">
      <h2 className="report-title">导出报告</h2>
      <p className="muted report-intro">汇总领域概览、核心期刊/作者、关键词与高被引文献。</p>

      {/* 报告元数据 */}
      <div className="card report-form">
        <div className="report-form-row">
          <label className="report-field">
            <span className="report-field-label">报告标题</span>
            <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="文献计量分析报告" />
          </label>
          <label className="report-field">
            <span className="report-field-label">作者（可选）</span>
            <input className="input" value={author} onChange={(e) => setAuthor(e.target.value)} placeholder="例：张三" />
          </label>
        </div>

        <fieldset className="report-sections">
          <legend className="report-field-label">包含章节</legend>
          <div className="report-section-grid">
            {SECTION_OPTIONS.map((s) => (
              <label key={s.id} className="report-section-item">
                <input
                  type="checkbox"
                  checked={sections.has(s.id)}
                  onChange={() => toggleSection(s.id)}
                />
                <span>{s.label}</span>
              </label>
            ))}
          </div>
          {noSection && <p className="muted report-hint">请至少勾选一个章节。</p>}
        </fieldset>

        <div className="report-actions">
          <button type="button" className="btn btn-primary" disabled={busy !== null || noSection} onClick={() => dlReport("md")}>
            {busy === "md" ? "生成中…" : "导出 Markdown"}
          </button>
          <button type="button" className="btn" disabled={busy !== null || noSection} onClick={() => dlReport("html")}>
            {busy === "html" ? "生成中…" : "导出 HTML"}
          </button>
          {docxAvailable && (
            <button type="button" className="btn" disabled={busy !== null || noSection} onClick={() => dlReport("docx")}>
              {busy === "docx" ? "生成中…" : "导出 DOCX"}
            </button>
          )}
        </div>
        {!docxAvailable && (
          <p className="muted report-hint">DOCX 导出不可用（服务端缺少 pandoc），可改用 Markdown / HTML。</p>
        )}
      </div>

      {/* 引用导出 */}
      <div className="card report-cite">
        <h3 className="report-subtitle">引用导出</h3>
        <div className="report-cite-row">
          <label className="report-field report-field-inline">
            <span className="report-field-label">格式</span>
            <select className="input" value={style} onChange={(e) => setStyle(e.target.value as CiteStyle)}>
              <option value="apa">APA-7</option>
              <option value="gbt7714">GB/T 7714</option>
              <option value="mla">MLA-9</option>
            </select>
          </label>
          <button type="button" className="btn" disabled={busy !== null} onClick={dlCite}>
            {busy === "cite" ? "生成中…" : "导出引用 (.txt)"}
          </button>
        </div>
      </div>

      {err && <p className="state-err report-err" role="alert">{err}</p>}
    </section>
  );
}
