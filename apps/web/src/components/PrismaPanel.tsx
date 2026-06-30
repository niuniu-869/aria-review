/**
 * PrismaPanel.tsx — PRISMA 2020 文献筛选流程图（A6）
 *
 * 纯前端 + 现有 API：
 *  - 输入区：5 个计数（识别/去重/筛选/排除/纳入）+ 排除理由 textarea。
 *  - 「从当前语料自动填充」：拉 GET /projects/{pid}/papers，按 inclusionStatus 推导计数。
 *  - 流程图：自绘 SVG（PRISMA 2020 标准版式：纵向主流程 + 箭头 + 右侧排除旁支）。
 *  - 一致性提示：本地即时校验 + 可选 buildPrisma 后端 warnings。
 *  - 导出：SVG（序列化 DOM）/ PNG（SVG→canvas）/ PDF（打印新窗口）。
 *
 * 设计基调：宣纸（朱砂主流程 / 红系排除旁支 / 墨色箭头）。
 * 接口约束：仅接 { projectId }（与 AnalysisView 的 prisma 分发保持一致）。
 */
import { useMemo, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { buildPrisma, type PrismaRequest } from "../api/client";
import { useProjectPapers } from "../api/agentHooks";
import { ChartCard, timestamp } from "./viz";

// ---- 计数字段定义（顺序即流程顺序）----
const FIELDS: { key: keyof PrismaRequest; label: string; hint: string }[] = [
  { key: "identified", label: "识别记录数", hint: "数据库检索获得的全部记录" },
  { key: "duplicates", label: "去重移除", hint: "去重后移除的重复记录" },
  { key: "screened", label: "筛选记录数", hint: "进入标题/摘要筛选的记录" },
  { key: "excluded", label: "排除记录数", hint: "全文复核后排除的记录" },
  { key: "included", label: "纳入研究数", hint: "最终纳入综述的研究" },
];

const ZERO: PrismaRequest = {
  identified: 0,
  duplicates: 0,
  screened: 0,
  excluded: 0,
  included: 0,
};

// ============================================================
// 纯函数：自动填充计数推导（可单测）
// ============================================================

/** 论文条目最小形状（仅用到 inclusionStatus） */
export interface PrismaPaperLike {
  inclusionStatus: "candidate" | "included" | "excluded" | "maybe";
}

/**
 * 从语料论文列表推导 PRISMA 计数。
 *  - identified = 总数
 *  - duplicates = 0（语料已去重；用户可改）
 *  - screened   = 总数 − duplicates
 *  - included   = count(included)
 *  - excluded   = count(excluded)
 * 注：candidate/maybe 既未纳入也未明确排除，不计入 included/excluded。
 */
export function deriveCounts(papers: PrismaPaperLike[]): PrismaRequest {
  const total = papers.length;
  const included = papers.filter((p) => p.inclusionStatus === "included").length;
  const excluded = papers.filter((p) => p.inclusionStatus === "excluded").length;
  const duplicates = 0;
  return {
    identified: total,
    duplicates,
    screened: total - duplicates,
    excluded,
    included,
  };
}

// ============================================================
// 纯函数：本地一致性校验（可单测）
// ============================================================

/** 返回客户端即时校验的告警文案（黄色提示）。空数组表示一致。 */
export function validateCounts(c: PrismaRequest): string[] {
  const w: string[] = [];
  // 先校验去重>识别（否则下面的 identified-duplicates 会算出负的期望筛选数，提示不合业务语义）
  if (c.duplicates > c.identified) {
    w.push(`去重数 (${c.duplicates}) 不应大于 识别记录数 (${c.identified})`);
  } else if (c.screened !== c.identified - c.duplicates) {
    w.push(
      `筛选记录数 (${c.screened}) 应等于 识别 (${c.identified}) − 去重 (${c.duplicates}) = ${c.identified - c.duplicates}`,
    );
  }
  if (c.included + c.excluded !== c.screened) {
    w.push(
      `纳入 (${c.included}) + 排除 (${c.excluded}) = ${c.included + c.excluded}，应等于 筛选记录数 (${c.screened})`,
    );
  }
  return w;
}

/** 把 textarea 文本拆为排除理由列表（每行一条，去空行/去前缀符号） */
export function parseReasons(text: string): string[] {
  return text
    .split("\n")
    .map((l) => l.replace(/^[\s·•\-*]+/, "").trim())
    .filter((l) => l.length > 0);
}

/** 全 0 视为空数据（不显示流程图/导出） */
function isEmpty(c: PrismaRequest): boolean {
  return FIELDS.every((f) => c[f.key] === 0);
}

// ============================================================
// SVG 版式常量
// ============================================================
const VB_W = 760; // viewBox 宽
const BOX_W = 300; // 主流程框宽
const BOX_X = 40; // 主流程框左边距
const SIDE_W = 320; // 旁支框宽
const SIDE_X = 400; // 旁支框左边距

/** 简易按字符宽换行（中文按 1，英文/数字按 0.55），用于旁支理由不溢出 */
function wrapText(s: string, maxChars: number): string[] {
  const lines: string[] = [];
  let cur = "";
  let w = 0;
  for (const ch of s) {
    const cw = /[\x00-\xff]/.test(ch) ? 0.55 : 1;
    if (w + cw > maxChars && cur) {
      lines.push(cur);
      cur = "";
      w = 0;
    }
    cur += ch;
    w += cw;
  }
  if (cur) lines.push(cur);
  return lines;
}

// ============================================================
// PRISMA SVG 流程图（自绘）
// ============================================================
interface PrismaFlowProps {
  counts: PrismaRequest;
  reasons: string[];
}

/** 一个主流程框（阶段名 + 大号计数） */
function StageBox({
  x,
  y,
  w,
  h,
  title,
  count,
}: {
  x: number;
  y: number;
  w: number;
  h: number;
  title: string;
  count: number;
}) {
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} rx={10} className="prisma-box prisma-box-main" />
      <text x={x + w / 2} y={y + 26} textAnchor="middle" className="prisma-stage-title">
        {title}
      </text>
      <text x={x + w / 2} y={y + 54} textAnchor="middle" className="prisma-stage-count tnum">
        {count}
      </text>
    </g>
  );
}

/** 一个右侧排除旁支框（标题 + 计数 + 理由列表） */
function SideBox({
  x,
  y,
  w,
  title,
  count,
  reasons,
}: {
  x: number;
  y: number;
  w: number;
  title: string;
  count: number;
  reasons: string[];
}) {
  // 理由换行后逐行排版（防超长/超多：单条截断 60 字、最多 8 条，余者折叠为一行提示，避免框无限拉高与导出超大）
  const MAX_REASONS = 8;
  const shown = reasons.slice(0, MAX_REASONS);
  const reasonLines = shown.flatMap((r) =>
    wrapText(`· ${r.length > 60 ? `${r.slice(0, 60)}…` : r}`, 30),
  );
  if (reasons.length > MAX_REASONS) reasonLines.push(`…（共 ${reasons.length} 条排除理由）`);
  const headH = 50;
  const lineH = 18;
  const padB = 14;
  const h = headH + reasonLines.length * lineH + (reasonLines.length ? padB : 0);
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} rx={10} className="prisma-box prisma-box-side" />
      <text x={x + 14} y={y + 24} className="prisma-side-title">
        {title}
      </text>
      <text x={x + w - 14} y={y + 24} textAnchor="end" className="prisma-side-count tnum">
        n = {count}
      </text>
      {reasonLines.map((ln, i) => (
        <text key={i} x={x + 14} y={y + headH + i * lineH} className="prisma-side-reason">
          {ln}
        </text>
      ))}
    </g>
  );
}

/** 箭头标记 + 连线 helper：竖直向下箭头 */
function DownArrow({ x, y1, y2 }: { x: number; y1: number; y2: number }) {
  return <line x1={x} y1={y1} x2={x} y2={y2} className="prisma-arrow" markerEnd="url(#prisma-arrowhead)" />;
}
/** 横向（主流程→右侧旁支）箭头 */
function RightArrow({ x1, x2, y }: { x1: number; x2: number; y: number }) {
  return <line x1={x1} y1={y} x2={x2} y2={y} className="prisma-arrow" markerEnd="url(#prisma-arrowhead)" />;
}

function PrismaFlow({ counts, reasons }: PrismaFlowProps) {
  const cx = BOX_X + BOX_W / 2; // 主流程竖直中线
  const stageH = 72;
  const gap = 56; // 框间竖直间距（容纳箭头）

  // 各主流程框 y 坐标
  const yId = 20;
  const yScreen = yId + stageH + gap;
  const yInc = yScreen + stageH + gap;

  // 旁支竖直中点对齐到对应箭头中段
  const yDupArrow = yId + stageH + gap / 2;
  const yExcArrow = yScreen + stageH + gap / 2;

  // 旁支框（高度依理由行数动态算，用 SideBox 内部逻辑，这里给起始 y）
  const sideDupY = yDupArrow - 25;
  const sideExcY = yExcArrow - 25;

  const totalH = yInc + stageH + 24;

  return (
    <svg
      role="img"
      aria-label="PRISMA 2020 流程图"
      viewBox={`0 0 ${VB_W} ${totalH}`}
      width="100%"
      preserveAspectRatio="xMidYMin meet"
      className="prisma-svg"
    >
      <defs>
        <marker
          id="prisma-arrowhead"
          markerWidth="9"
          markerHeight="9"
          refX="7"
          refY="4"
          orient="auto"
          markerUnits="userSpaceOnUse"
        >
          <path d="M0,0 L8,4 L0,8 Z" className="prisma-arrowhead-fill" />
        </marker>
      </defs>

      {/* 阶段大标签（左侧竖排锚点用横排小标即可） */}
      {/* 主流程框 */}
      <StageBox x={BOX_X} y={yId} w={BOX_W} h={stageH} title="识别 Identification" count={counts.identified} />
      <StageBox x={BOX_X} y={yScreen} w={BOX_W} h={stageH} title="筛选 Screening" count={counts.screened} />
      <StageBox x={BOX_X} y={yInc} w={BOX_W} h={stageH} title="纳入 Included" count={counts.included} />

      {/* 竖直主流程箭头 */}
      <DownArrow x={cx} y1={yId + stageH} y2={yScreen} />
      <DownArrow x={cx} y1={yScreen + stageH} y2={yInc} />

      {/* 横向旁支箭头（主流程中线 → 旁支框左缘） */}
      <RightArrow x1={cx} x2={SIDE_X} y={yDupArrow} />
      <RightArrow x1={cx} x2={SIDE_X} y={yExcArrow} />

      {/* 右侧排除旁支框 */}
      <SideBox x={SIDE_X} y={sideDupY} w={SIDE_W} title="去重移除 Duplicates" count={counts.duplicates} reasons={[]} />
      <SideBox
        x={SIDE_X}
        y={sideExcY}
        w={SIDE_W}
        title="排除记录 Excluded"
        count={counts.excluded}
        reasons={reasons}
      />
    </svg>
  );
}

// ============================================================
// 导出 helper（PRISMA 自定义：SVG / PNG / PDF）
// ============================================================

/**
 * 导出用内联样式：把 .prisma-* 类的样式以「解析后的具体色值」内联进 SVG。
 * 必要性：导出的独立 SVG/PNG/PDF 不带页面外部 stylesheet，若仍依赖 class 会丢全部样式（变黑/无样式）。
 * 读 :root 的 CSS 变量计算值，无 document 时用宣纸亮色兜底。
 */
function exportStyleCss(): string {
  const root = typeof document !== "undefined" && document.documentElement
    ? getComputedStyle(document.documentElement)
    : null;
  const v = (name: string, fb: string) => (root?.getPropertyValue(name).trim() || fb);
  const cinnabar = v("--cinnabar", "#c0432b");
  const cinnabar2 = v("--cinnabar-2", "#a8351f");
  const cinnabarSoft = v("--cinnabar-soft", "#f3ddd5");
  const danger = v("--danger", "#c0432b");
  const ink = v("--ink", "#1f1c17");
  const ink2 = v("--ink-2", "#4a443b");
  const ink3 = v("--ink-3", "#8a8276");
  const serif = v("--serif", '"Songti SC","STSong","SimSun",Georgia,serif');
  const sans = v("--sans", '"PingFang SC","Microsoft YaHei",system-ui,sans-serif');
  return (
    `.prisma-box{stroke-width:1.5;}` +
    `.prisma-box-main{fill:${cinnabarSoft};stroke:${cinnabar};}` +
    `.prisma-box-side{fill:rgba(192,67,43,0.06);stroke:${danger};stroke-dasharray:4 3;}` +
    `.prisma-stage-title{font-family:${serif};font-weight:700;font-size:15px;fill:${ink};}` +
    `.prisma-stage-count{font-family:${sans};font-weight:700;font-size:22px;fill:${cinnabar2};}` +
    `.prisma-side-title{font-family:${serif};font-weight:700;font-size:13px;fill:${ink};}` +
    `.prisma-side-count{font-family:${sans};font-weight:700;font-size:13px;fill:${danger};}` +
    `.prisma-side-reason{font-family:${sans};font-size:12px;fill:${ink2};}` +
    `.prisma-arrow{stroke:${ink3};stroke-width:1.5;fill:none;}` +
    `.prisma-arrowhead-fill{fill:${ink3};}`
  );
}

/** 克隆 <svg> 并注入内联 <style>，使其样式自包含（导出/打印用）。 */
function selfContainedSvgClone(svg: SVGSVGElement): SVGSVGElement {
  const clone = svg.cloneNode(true) as SVGSVGElement;
  if (!clone.getAttribute("xmlns")) clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  const styleEl = document.createElementNS("http://www.w3.org/2000/svg", "style");
  styleEl.textContent = exportStyleCss();
  clone.insertBefore(styleEl, clone.firstChild);
  return clone;
}

/** 把 <svg> DOM 序列化为带 XML 声明、样式自包含的字符串 */
export function serializeSvg(svg: SVGSVGElement): string {
  const clone = selfContainedSvgClone(svg);
  return `<?xml version="1.0" encoding="UTF-8"?>\n${new XMLSerializer().serializeToString(clone)}`;
}

function triggerDownload(href: string, filename: string) {
  const a = document.createElement("a");
  a.href = href;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function exportSvgFile(svg: SVGSVGElement, notify?: (m: string) => void) {
  try {
    const str = serializeSvg(svg);
    const blob = new Blob([str], { type: "image/svg+xml;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    triggerDownload(url, `prisma_${timestamp()}.svg`);
    // 延后释放：部分浏览器在下载接管前 revoke 会中断下载（typeof 守卫兼容无该 API 的环境）
    setTimeout(() => {
      if (typeof URL.revokeObjectURL === "function") URL.revokeObjectURL(url);
    }, 0);
  } catch {
    notify?.("SVG 导出失败");
  }
}

function exportPngFile(svg: SVGSVGElement, notify?: (m: string) => void) {
  let str: string;
  let svgUrl: string;
  let w: number;
  let h: number;
  try {
    str = serializeSvg(svg);
    const vb = svg.viewBox.baseVal;
    w = vb && vb.width ? vb.width : 760;
    h = vb && vb.height ? vb.height : 600;
    svgUrl = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(str)}`;
  } catch {
    notify?.("PNG 导出失败，请改用 SVG 导出");
    return;
  }
  const scale = 2;
  const img = new Image();
  const cleanup = () => {
    img.onload = null;
    img.onerror = null;
  };
  img.onload = () => {
    try {
      const canvas = document.createElement("canvas");
      canvas.width = w * scale;
      canvas.height = h * scale;
      const ctx = canvas.getContext("2d");
      if (!ctx) {
        notify?.("PNG 导出失败：无法创建画布");
        return;
      }
      ctx.fillStyle = "#fffdf8"; // 宣纸卡底，避免透明 PNG 黑底
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      triggerDownload(canvas.toDataURL("image/png"), `prisma_${timestamp()}.png`);
    } catch {
      notify?.("PNG 导出失败，请改用 SVG 导出");
    } finally {
      cleanup();
    }
  };
  img.onerror = () => {
    notify?.("PNG 导出失败，请改用 SVG 导出");
    cleanup();
  };
  img.src = svgUrl;
}

function exportPdfPrint(svg: SVGSVGElement, notify?: (m: string) => void) {
  // 用 DOM API 构建打印窗口（不做 SVG 字符串拼接，缩小注入面）；样式自包含
  let win: Window | null = null;
  try {
    win = window.open("", "_blank");
    if (!win) {
      notify?.("无法打开打印窗口（可能被浏览器拦截）");
      return;
    }
    const doc = win.document;
    doc.title = "PRISMA 流程图";
    const style = doc.createElement("style");
    style.textContent =
      "body{margin:0;padding:24px;font-family:serif;}h1{font-size:18px;}" +
      "svg{width:100%;height:auto;}@media print{@page{margin:12mm;}}";
    doc.head.appendChild(style);
    const h1 = doc.createElement("h1");
    h1.textContent = "PRISMA 2020 文献筛选流程图";
    doc.body.appendChild(h1);
    // 深克隆+样式自包含后跨文档导入，避免字符串注入
    const imported = doc.importNode(selfContainedSvgClone(svg), true);
    doc.body.appendChild(imported);
    let printed = false;
    const doPrint = () => {
      if (printed) return; // 防 onload 与兜底 setTimeout 双触发重复打印
      printed = true;
      try {
        win?.focus();
        win?.print();
      } catch {
        /* 打印失败静默；窗口已显示图供用户手动打印 */
      }
    };
    win.onload = doPrint;
    // 兜底：about:blank 可能已 load 完，onload 不触发
    setTimeout(doPrint, 300);
  } catch {
    notify?.("PDF 导出失败");
    try {
      win?.close();
    } catch {
      /* ignore */
    }
  }
}

// ============================================================
// 主组件
// ============================================================
export function PrismaPanel({ projectId }: { projectId: string }) {
  const pidNum = Number(projectId);
  const [counts, setCounts] = useState<PrismaRequest>(ZERO);
  const [reasonsText, setReasonsText] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const svgWrapRef = useRef<HTMLDivElement>(null);

  // 自动填充用：拉项目论文（pidNum<=0 时 hook 自动 disabled）
  const papersQuery = useProjectPapers(pidNum);

  // 可选：调后端拿权威 warnings（流程图本身用本地 counts 渲染）
  const mut = useMutation({ mutationFn: (c: PrismaRequest) => buildPrisma(projectId, c) });

  const reasons = useMemo(() => parseReasons(reasonsText), [reasonsText]);
  const localWarnings = useMemo(() => validateCounts(counts), [counts]);
  const empty = isEmpty(counts);

  function setField(k: keyof PrismaRequest, v: number) {
    setCounts((c) => ({ ...c, [k]: Math.max(0, Math.floor(v) || 0) }));
  }

  function autofill() {
    const papers = papersQuery.data?.papers;
    if (!papers || papers.length === 0) {
      setNotice("当前语料暂无文献，无法自动填充。");
      return;
    }
    setCounts(deriveCounts(papers));
    setNotice(
      `已从当前语料（共 ${papers.length} 篇）填充计数，去重数默认 0，可手动调整。`,
    );
  }

  function getSvg(): SVGSVGElement | null {
    return svgWrapRef.current?.querySelector("svg") ?? null;
  }

  return (
    <ChartCard
      title="PRISMA 流程图"
      subtitle="系统综述文献筛选流程（PRISMA 2020）"
      hint="主流程为朱砂框，右侧红框为排除/去重旁支。计数可手填或从语料自动填充；流程图按当前计数实时绘制。"
    >
      {/* ---- 输入区 ---- */}
      <div className="prisma-inputs">
        {FIELDS.map((f) => (
          <div key={f.key} className="prisma-field">
            <label htmlFor={`prisma-${f.key}`}>{f.label}</label>
            <input
              id={`prisma-${f.key}`}
              className="input"
              type="number"
              min={0}
              value={counts[f.key]}
              onChange={(e) => setField(f.key, Number(e.target.value))}
            />
            <span className="prisma-field-hint">{f.hint}</span>
          </div>
        ))}
      </div>

      <div className="prisma-reasons-field">
        <label htmlFor="prisma-reasons">排除理由（每行一条）</label>
        <textarea
          id="prisma-reasons"
          className="input"
          rows={3}
          placeholder={"如：\n研究类型不符\n非同行评审\n全文不可获取"}
          value={reasonsText}
          onChange={(e) => setReasonsText(e.target.value)}
        />
      </div>

      {/* ---- 操作行 ---- */}
      <div className="prisma-actions">
        <button
          type="button"
          className="btn"
          onClick={autofill}
          disabled={papersQuery.isLoading}
        >
          {papersQuery.isLoading ? "读取语料…" : "从当前语料自动填充"}
        </button>
        <button
          type="button"
          className="btn btn-ghost"
          onClick={() => mut.mutate(counts)}
          disabled={mut.isPending || empty}
        >
          {mut.isPending ? "校验中…" : "后端一致性校验"}
        </button>
        {!empty && (
          <div className="prisma-export">
            <button type="button" className="btn btn-ghost" onClick={() => { const s = getSvg(); if (s) exportSvgFile(s, setNotice); }}>
              导出 SVG
            </button>
            <button type="button" className="btn btn-ghost" onClick={() => { const s = getSvg(); if (s) exportPngFile(s, setNotice); }}>
              导出 PNG
            </button>
            <button type="button" className="btn btn-ghost" onClick={() => { const s = getSvg(); if (s) exportPdfPrint(s, setNotice); }}>
              导出 PDF
            </button>
          </div>
        )}
      </div>

      {/* ---- 提示 / 告警 ---- */}
      {notice && <p className="prisma-notice">{notice}</p>}
      {localWarnings.length > 0 && (
        <ul className="prisma-warn" role="alert">
          {localWarnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      )}
      {mut.isError && (
        <p className="prisma-warn" role="alert">
          {(mut.error as Error).message}
        </p>
      )}
      {mut.data && mut.data.warnings.length > 0 && (
        <ul className="prisma-warn" role="alert">
          {mut.data.warnings.map((w, i) => (
            <li key={`be-${i}`}>{w}</li>
          ))}
        </ul>
      )}

      {/* ---- 流程图 ---- */}
      {empty ? (
        <p className="muted prisma-empty">输入计数或点击「从当前语料自动填充」后，此处将绘制 PRISMA 流程图。</p>
      ) : (
        <div className="prisma-flow-wrap" ref={svgWrapRef}>
          <PrismaFlow counts={counts} reasons={reasons} />
        </div>
      )}
    </ChartCard>
  );
}
