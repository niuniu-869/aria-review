# R/fct_pdf.R — PDF 文本抽取 (纯函数 / 同步 / 无外部 IO 之外的副作用)

#' 抽取 PDF 文本 (前 max_pages 页)
#'
#' @param path PDF 路径
#' @param max_pages 默认 10 (限制单文件文本量, 防超大 PDF 把 LLM 上下文撑爆)
#' @return 单个字符串 (页与页用 \\n\\n 分隔); 失败时返回 "" + warning
pdf_extract_text <- function(path, max_pages = 10L) {
  if (!file.exists(path)) {
    warning(sprintf("[降级] PDF 不存在: %s", path))
    return("")
  }
  if (!requireNamespace("pdftools", quietly = TRUE)) {
    warning("[降级] pdftools 包未安装")
    return("")
  }
  tryCatch({
    pages <- pdftools::pdf_text(path)
    paste(utils::head(pages, as.integer(max_pages)), collapse = "\n\n")
  },
  error = function(e) {
    msg <- if (exists("safe_log_error", inherits = TRUE)) safe_log_error(e)
           else conditionMessage(e)
    warning(sprintf("[降级] PDF 抽取失败 %s: %s", path, msg))
    ""
  })
}
