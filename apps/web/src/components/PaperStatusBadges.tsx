/**
 * PaperStatusBadges.tsx — 文献逐篇 PDF/OCR/元数据状态徽章（Task 6）
 *
 * 按 paper 的 hasPdf/ocrStatus 渲染徽章：
 *   📄 PDF      — hasPdf=true
 *   已OCR       — ocrStatus="done"
 *   解析中      — ocrStatus="processing"
 *   待OCR       — ocrStatus="pending"
 *   OCR失败     — ocrStatus="failed"
 *   仅元数据    — hasPdf=false（无全文）
 *
 * 无障碍: title + aria-label 双保险，屏幕阅读器可达。
 */

type OcrStatus = "none" | "pending" | "processing" | "done" | "failed";

interface Props {
  hasPdf: boolean;
  ocrStatus: OcrStatus;
}

export function PaperStatusBadges({ hasPdf, ocrStatus }: Props) {
  return (
    <span className="paper-status-badges">
      {hasPdf ? (
        <>
          {/* PDF 附件标志 */}
          <span
            className="paper-badge paper-badge--pdf"
            title="已上传 PDF 全文附件"
            aria-label="已上传 PDF 全文附件"
          >
            📄 PDF
          </span>
          {/* OCR 状态 */}
          {ocrStatus === "done" && (
            <span
              className="paper-badge paper-badge--ocr-done"
              title="PDF 已完成 OCR 解析，可用作综述语料"
              aria-label="PDF 已完成 OCR 解析，可用作综述语料"
            >
              已OCR
            </span>
          )}
          {ocrStatus === "processing" && (
            <span
              className="paper-badge paper-badge--ocr-pending"
              title="PDF 正在 OCR 解析中，请稍候"
              aria-label="PDF 正在 OCR 解析中，请稍候"
            >
              解析中
            </span>
          )}
          {ocrStatus === "pending" && (
            <span
              className="paper-badge paper-badge--ocr-pending"
              title="PDF 等待 OCR 解析队列"
              aria-label="PDF 等待 OCR 解析队列"
            >
              待OCR
            </span>
          )}
          {ocrStatus === "failed" && (
            <span
              className="paper-badge paper-badge--ocr-failed"
              title="OCR 解析失败，可删除后重新上传 PDF"
              aria-label="OCR 解析失败，可删除后重新上传 PDF"
            >
              OCR失败
            </span>
          )}
        </>
      ) : (
        /* 无 PDF — 仅元数据 */
        <span
          className="paper-badge paper-badge--meta-only"
          title="仅题录元数据，无全文 PDF"
          aria-label="仅题录元数据，无全文 PDF"
        >
          仅元数据
        </span>
      )}
    </span>
  );
}
