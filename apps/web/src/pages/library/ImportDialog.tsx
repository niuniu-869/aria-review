/**
 * ImportDialog.tsx — 文献导入弹层
 *
 * 接受 PDF 多选或 ZIP，调用 POST /projects/{pid}/papers/import，
 * 显示 imported/skipped/failed 结果，成功后刷新列表（由父组件 invalidate query）。
 */
import { useRef, useState } from "react";
import type { ImportResult } from "../../api/client";

interface Props {
  importing: boolean;
  result?: ImportResult;
  error?: Error | null;
  onImport: (files: File[]) => void;
  onClose: () => void;
}

export function ImportDialog({ importing, result, error, onImport, onClose }: Props) {
  const [dragover, setDragover] = useState(false);
  const [localFiles, setLocalFiles] = useState<File[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFiles = (files: FileList | null) => {
    if (!files) return;
    const arr = Array.from(files);
    setLocalFiles(arr);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragover(false);
    handleFiles(e.dataTransfer.files);
  };

  const handleSubmit = () => {
    if (localFiles.length > 0) onImport(localFiles);
  };

  // 是否已有结果
  const hasDone = !!result;

  return (
    <div className="import-dialog" role="dialog" aria-modal="true" aria-label="导入文献">
      <div className="import-dialog-card">
        <h3 className="import-dialog-title">导入文献</h3>
        <p style={{ fontSize: "0.88rem", color: "var(--ink-2)", margin: "0 0 0.75rem" }}>
          支持 PDF 多选或一个 ZIP（含多个 PDF）。导入幂等：重复文献自动跳过。
        </p>

        {/* 拖放区 */}
        {!hasDone && (
          <>
            <div
              className={`import-dropzone${dragover ? " dragover" : ""}`}
              onDragOver={(e) => { e.preventDefault(); setDragover(true); }}
              onDragLeave={() => setDragover(false)}
              onDrop={handleDrop}
              onClick={() => inputRef.current?.click()}
              role="button"
              tabIndex={0}
              aria-label="点击或拖放 PDF/ZIP 文件"
              onKeyDown={(e) => {
                // P2-b：键盘可达 — Enter/Space 触发文件选择，Esc 关闭弹层
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  inputRef.current?.click();
                } else if (e.key === "Escape") {
                  e.preventDefault();
                  onClose();
                }
              }}
            >
              {localFiles.length > 0 ? (
                <div>
                  <div style={{ fontWeight: 600, color: "var(--ink)" }}>已选 {localFiles.length} 个文件</div>
                  <ul style={{ listStyle: "none", padding: 0, margin: "0.4rem 0 0", fontSize: "0.82rem", maxHeight: 80, overflowY: "auto" }}>
                    {localFiles.map((f) => <li key={f.name}>{f.name}</li>)}
                  </ul>
                </div>
              ) : (
                <div>
                  <div style={{ fontSize: "1.5rem", marginBottom: "0.4rem" }}>📄</div>
                  点击选择或拖放 PDF / ZIP 文件到此处
                </div>
              )}
            </div>
            <input
              ref={inputRef}
              type="file"
              accept=".pdf,.zip"
              multiple
              style={{ display: "none" }}
              onChange={(e) => handleFiles(e.target.files)}
            />
          </>
        )}

        {/* 导入结果 */}
        {hasDone && result && (
          <div className="import-result">
            <div className="ok" style={{ fontWeight: 600, marginBottom: "0.25rem" }}>
              ✓ 导入完成
            </div>
            <div>新导入：<strong>{result.imported}</strong> 篇</div>
            {result.skipped > 0 && <div className="warn">重复跳过：{result.skipped} 篇</div>}
            {result.failed.length > 0 && (
              <div className="danger">
                失败：{result.failed.length} 篇
                <ul className="import-failed-list">
                  {result.failed.map((f, i) => (
                    <li key={i}>{f.name}：{f.reason}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {/* 错误 */}
        {error && (
          <div className="import-result danger" style={{ fontWeight: 600 }}>
            导入失败：{error.message}
          </div>
        )}

        {/* 导入中提示：MinerU 全文 OCR 较慢，大文件/扫描件需数分钟(配合 Caddy 600s 超时，避免用户以为卡死) */}
        {importing && (
          <div
            className="import-result"
            role="status"
            style={{ color: "var(--ink-3)", fontSize: "0.85rem" }}
          >
            正在上传并解析全文（MinerU OCR）。大文件或扫描件可能需要数分钟，请耐心等待；若关闭或刷新页面将看不到本次结果，可稍后到文献库查看解析状态。
          </div>
        )}

        {/* 操作按钮 */}
        <div className="import-dialog-actions">
          <button className="btn btn-ghost" onClick={onClose} disabled={importing}>
            {hasDone ? "关闭" : "取消"}
          </button>
          {!hasDone && (
            <button
              className="btn btn-primary"
              onClick={handleSubmit}
              disabled={importing || localFiles.length === 0}
            >
              {importing ? "导入中…" : "开始导入"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
