# R/mod_report.R — 一键导出综述报告页 (spec §7 / F2)
#
# 用户勾选要纳入的章节 + 选格式 → compile_report 生成 MD/DOCX/HTML(/PDF).
# AI 综述章节与 PRISMA 计数从 shared 读取 (由 mod_ai_review / mod_prisma 写入).

reportUI <- function(id) {
  ns <- NS(id)
  tagList(
    page_header("导出报告",
                "这页能做: 把语料概览 + PRISMA 流程图 + AI 综述初稿 + 参考文献一键打包成可投稿/可提交的报告 (Word / 网页 / Markdown)"),
    fluidRow(
      column(5,
        analysis_card(
          "报告内容",
          desc = "勾选要纳入报告的部分; AI 综述需先在『AI 综述』页生成.",
          textInput(ns("title"), "报告标题", value = "文献综述报告"),
          textInput(ns("author"), "作者", value = ""),
          checkboxGroupInput(ns("sections"), "纳入章节",
            choices = c("PRISMA 流程图" = "prisma",
                        "文献计量概览"  = "overview",
                        "AI 综述初稿"   = "ai_review",
                        "参考文献"      = "references",
                        "可复现附录"    = "appendix"),
            selected = c("prisma", "overview", "ai_review", "references", "appendix"))
        )
      ),
      column(7,
        analysis_card(
          "导出",
          desc = "DOCX 适合 Word 编辑; HTML 可在浏览器 Ctrl+P 打印为 PDF (中文不乱码); MD 适合版本控制.",
          radioButtons(ns("fmt"), "格式",
            choices = c("Word (.docx)" = "docx",
                        "网页 (.html, 可打印 PDF)" = "html",
                        "Markdown (.md)" = "md",
                        "PDF (需服务器装 LaTeX)" = "pdf"),
            selected = "docx"),
          uiOutput(ns("status")),
          br(),
          downloadButton(ns("dl"), "生成并下载报告", class = "btn-primary")
        )
      )
    )
  )
}

reportServer <- function(id, corpus, shared) {
  moduleServer(id, function(input, output, session) {

    output$status <- renderUI({
      msgs <- list()
      if (is.null(tryCatch(corpus(), error = function(e) NULL)))
        msgs <- c(msgs, list(div(class = "text-warning",
          icon("triangle-exclamation"), " 尚未加载语料, 报告内容会不完整.")))
      if (identical(input$fmt, "pdf") && !report_pdf_available())
        msgs <- c(msgs, list(div(class = "text-danger",
          icon("circle-xmark"),
          " 当前服务器未装 LaTeX, PDF 不可用. 建议选 Word 或 网页(浏览器打印 PDF).")))
      n_ch <- length(shared$review_chapters %||% list())
      msgs <- c(msgs, list(div(class = "text-muted",
        sprintf("可用 AI 综述章节: %d 章%s", n_ch,
                if (!n_ch) " (去『AI 综述』页生成后再回来)" else ""))))
      do.call(tagList, msgs)
    })

    output$dl <- downloadHandler(
      filename = function() {
        ext <- if (identical(input$fmt, "md")) "md" else input$fmt
        sprintf("report_%s.%s", format(Sys.time(), "%Y%m%d_%H%M%S"), ext)
      },
      content = function(file) {
        req(corpus())
        sel <- input$sections %||% character(0)
        sections <- list(
          prisma     = "prisma"     %in% sel,
          overview   = "overview"   %in% sel,
          ai_review  = "ai_review"  %in% sel,
          references = "references" %in% sel,
          appendix   = "appendix"   %in% sel
        )
        prisma <- shared$prisma_state %||% NULL
        chs    <- shared$review_chapters %||% list()
        meta <- list(title = input$title %||% "文献综述报告",
                     author = input$author %||% "",
                     model = shared$model %||% "deepseek-v4-flash",
                     version = "v0.6")
        withProgress(message = "正在生成报告……", value = 0.5, {
          out <- tryCatch(
            compile_report(corpus(), sections = sections, ai_chapters = chs,
                           prisma = prisma, meta = meta, fmt = input$fmt,
                           out_file = file),
            error = function(e) {
              showNotification(sprintf("报告生成失败: %s", conditionMessage(e)),
                               type = "error", duration = 10)
              # 降级: 写一个最小 md 说明, 避免下载到空文件
              writeLines(sprintf("# 报告生成失败\n\n%s", conditionMessage(e)), file)
              file
            })
        })
        invisible(out)
      }
    )
  })
}

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0L) b else a
