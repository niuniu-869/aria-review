/**
 * provenance.ts — 可信溯源契约 TS 类型（前端真相，与后端 Pydantic 一一对应）
 *
 * 来源：packages/contracts/openapi.yaml 中的文档结构与溯源契约。
 * 杀手锏数据链：综述里的 [[anchor:id]] → provenance_map[id] → StructureBlock(block_idx)
 *   → SourceViewer 按 md_line_start/end 行级高亮（必达档）/ bbox 像素级（增强档）。
 *
 * 所有字段以契约为唯一真相；fixture(F1 手造 / F6 真实) 只换数据不换形状。
 */

/** 文档结构块（来自 MinerU content_list 接入）— 契约 §1.1 */
export interface StructureBlock {
  /** content_list 中的块序号（0-based, 稳定 ID） */
  block_idx: number;
  type: "text" | "title" | "table" | "image";
  /** 标题层级（1/2/3…）；null=正文段落 */
  text_level: number | null;
  /** 1-based PDF 页码（content_list.page_idx + 1） */
  page_no: number;
  /** 该块在 full.md 中的起始行（1-based）；无法精确定位时为 null → 前端降级不伪造（契约 §5.3 v2，Track A B移交） */
  md_line_start: number | null;
  /** 该块在 full.md 中的结束行（1-based，含）；同上可为 null */
  md_line_end: number | null;
  /** [x0,y0,x1,y1] 归一化到 0-1000；无坐标时 null */
  bbox: [number, number, number, number] | null;
  /** 该块所属最近标题（供"在哪一节"展示） */
  section_title: string;
  /** 文本前 120 字（表/图为 caption 或占位） */
  text_preview: string;
}

/** 表格单元格（展开后网格 + 文本）— 契约 §1.2 */
export interface StructureCell {
  /** 展开后网格行（0-based） */
  row: number;
  /** 展开后网格列（0-based） */
  col: number;
  /** 单元格文本（逐字，不改写） */
  text: string;
}

/** 表格（网格 + 单元格坐标）— 契约 §1.2 */
export interface StructureTable {
  /** 第几张表（0-based） */
  table_idx: number;
  /** 对应的 StructureBlock.block_idx（用于定位/高亮） */
  block_idx: number;
  page_no: number;
  bbox: [number, number, number, number] | null;
  n_rows: number;
  n_cols: number;
  /** 展开后的网格（colspan/rowspan 已处理） */
  grid: string[][];
  caption: string;
}

/**
 * GET /projects/{pid}/papers/{paperId}/structure 响应 — 契约 §2.1 + §5.3。
 * markdown_sha256 = full.md 内容 hash（≠ Attachment.sha256=PDF hash）。
 */
export interface StructureResponse {
  paper_id: number;
  attachment_id: number;
  page_count: number;
  blocks: StructureBlock[];
  tables: StructureTable[];
  /** content_list 是否带 bbox（前端据此决定像素高亮档） */
  has_bbox: boolean;
  /** 与 markdown 端点一致，供前端校验（full.md hash）；旧/降级数据未算出时为 null（后端 str|None，与 markdown 端点对齐，契约 §2.1 v2） */
  markdown_sha256: string | null;
  /** 契约 §5.3 新增字段（真实后端：schema_version 为整数，其余可为 null） */
  schema_version?: number;
  source_pdf_sha256?: string | null;
  /** bbox 坐标系校准元信息（契约 §5.3/§5.4）。!= 原 PDF（如 mineru_1000）时降像素档。 */
  bbox_coord_space?: string | null;
  page_width?: number | null;
  page_height?: number | null;
  rotation?: number | null;
}

/**
 * 单条溯源定位（provenance_map 的 value）— 契约 §2.3 + §1.3。
 * anchor_id 为 occurrence 级（同一引用多处出现各一条，不去重，契约 §5.5）。
 */
export interface ProvenanceRef {
  paper_id: number;
  /** 无可读附件时后端为 null（load.py:attachment?.id），前端不消费但类型与后端对齐（契约 §2.3 v2） */
  attachment_id: number | null;
  page_no: number | null;
  block_idx: number | null;
  bbox: [number, number, number, number] | null;
  table_idx: number | null;
  cell_row: number | null;
  cell_col: number | null;
  section_title: string | null;
  /** 命中的原文片段（source_quote） */
  quote: string;
}

/** anchor_id → 溯源定位 的映射 — 契约 §2.3 */
export type ProvenanceMap = Record<string, ProvenanceRef>;

/**
 * 带溯源的综述结果 — 契约 §2.3。
 * review_md 正文里可溯源片段用 [[anchor:<id>]]…[[/anchor]] 包裹。
 */
export interface ReviewWithProvenanceData {
  review_md: string;
  provenance_map: ProvenanceMap;
}

/** GET /projects/{pid}/papers/{paperId}/markdown 响应 — 契约 §2.2（复用既有端点） */
export interface MarkdownResponse {
  markdown: string;
  length: number;
  truncated: boolean;
  sha256: string | null;
  /** 既有后端附带；mock 可缺省 */
  available?: boolean;
}
