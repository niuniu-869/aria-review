# R/fct_report.R — 一键导出综述报告 (spec §7 / F2)
#
# v0.6 设计决议:
#   · 三种稳健格式: MD (pandoc gfm) / DOCX (pandoc word_document, 无需 officer/LaTeX,
#     中文友好) / HTML (浏览器 Ctrl+P 打印成 PDF, 中文零乱码).
#   · PDF 作为可选: 仅当系统装了 LaTeX (xelatex) 时尝试, 否则优雅降级并提示.
#     这绕开 spec §11 "中文 PDF 乱码 / 无 xelatex" 风险, 又不丢可投稿产出.
#   · 报告内容: 元信息 + PRISMA 流程图 + 概览统计 + AI 综述章节 + 参考文献 +
#     可复现附录 (参数/时间戳/语料维度), 兑现 spec §0 "可复现" 口号.
#   · 绘图复用 fct_prisma.R::prisma_flow_plot 与 fct_analysis.R::analyze_overview.

#' 定位报告 Rmd 模板 (本项目非 R 包, 用候选路径查找)
#' @return 模板绝对路径; 找不到则报错
report_template_path <- function() {
  candidates <- c(
    file.path(getwd(), "inst", "report-template.Rmd"),
    file.path("inst", "report-template.Rmd"),
    file.path(dirname(getwd()), "biblio_cn", "inst", "report-template.Rmd")
  )
  hit <- candidates[file.exists(candidates)]
  if (!length(hit))
    stop("找不到报告模板 inst/report-template.Rmd")
  normalizePath(hit[1])
}

#' 报告支持的格式
report_formats <- function() c("html", "docx", "md", "pdf")

#' 当前环境是否能导出 PDF (需 LaTeX)
report_pdf_available <- function() {
  nzchar(Sys.which("xelatex")) ||
    nzchar(Sys.which("pdflatex")) ||
    (requireNamespace("tinytex", quietly = TRUE) && tinytex::is_tinytex())
}

#' 编译综述报告
#'
#' @param corpus       data.frame; bibliometrix 语料
#' @param sections     list(prisma=, overview=, ai_review=, references=, appendix=) 逻辑值
#' @param ai_chapters  named list; 名=章节标题, 值=markdown 正文 (来自 mod_ai_review)
#' @param prisma       list(counts=list(...), reasons="...") 或 NULL
#' @param meta         list(title=, author=, ...) 报告元信息
#' @param fmt          "html" | "docx" | "md" | "pdf"
#' @param out_file     输出文件路径 (downloadHandler 提供)
#' @return out_file (不可用格式时抛错, 由调用方 tryCatch 提示)
compile_report <- function(corpus,
                           sections = list(prisma = TRUE, overview = TRUE,
                                           ai_review = TRUE, references = TRUE,
                                           appendix = TRUE),
                           ai_chapters = list(),
                           prisma = NULL,
                           meta = list(),
                           fmt = "html",
                           out_file = NULL) {
  fmt <- match.arg(fmt, report_formats())
  if (fmt == "pdf" && !report_pdf_available())
    stop("当前服务器未安装 LaTeX (xelatex), 无法导出 PDF. 请改用 DOCX 或 HTML (浏览器可打印为 PDF), 或运行 tinytex::install_tinytex().")

  tpl <- report_template_path()
  if (is.null(out_file))
    out_file <- tempfile(fileext = paste0(".", if (fmt == "md") "md" else fmt))

  output_format <- switch(fmt,
    html = rmarkdown::html_document(toc = TRUE, toc_depth = 2,
                                    theme = "cosmo", self_contained = TRUE),
    docx = rmarkdown::word_document(toc = TRUE, toc_depth = 2),
    md   = rmarkdown::md_document(variant = "gfm", toc = TRUE),
    pdf  = rmarkdown::pdf_document(latex_engine = "xelatex", toc = TRUE,
                                   pandoc_args = c("-V", "CJKmainfont=Noto Sans CJK SC"))
  )

  params <- list(
    corpus      = corpus,
    sections    = sections,
    ai_chapters = ai_chapters,
    prisma      = prisma,
    meta        = meta
  )

  # 在 tempdir 渲染, 避免污染项目目录; intermediates 也放 tempdir
  render_dir <- tempfile("biblio_report_")
  dir.create(render_dir, showWarnings = FALSE, recursive = TRUE)
  tmp_tpl <- file.path(render_dir, "report.Rmd")
  file.copy(tpl, tmp_tpl, overwrite = TRUE)

  rmarkdown::render(
    input         = tmp_tpl,
    output_format = output_format,
    output_file   = basename(out_file),
    output_dir    = dirname(normalizePath(out_file, mustWork = FALSE)),
    params        = params,
    envir         = new.env(parent = globalenv()),
    quiet         = TRUE,
    intermediates_dir = render_dir
  )
  out_file
}

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0L) b else a
