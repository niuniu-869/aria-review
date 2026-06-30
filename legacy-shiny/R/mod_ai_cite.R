# R/mod_ai_cite.R — 引用导出 + Crossref DOI 校验

aiCiteUI <- function(id) {
  ns <- NS(id)
  tagList(
    page_header(LBL$menu_ai_cite,
                "这页能做: 把语料导出成标准引用格式 (国标 GB/T 7714 / APA-7 / MLA-9), 可直接粘进开题报告参考文献列表"),
    analysis_card(
      "引用导出",
      desc = "把语料以 GB/T 7714 / APA-7 / MLA-9 三种格式导出为 .txt.",
      fluidRow(
        column(6, selectInput(ns("style"), "格式",
                              choices = c("GB/T 7714-2015 (中文学术常用)" = "gbt7714",
                                          "APA-7"                          = "apa",
                                          "MLA-9"                          = "mla"))),
        # FINDING-004 修复: 移除 btn-primary class. 导出是次要主操作,
        # 用 downloadButton 默认灰白底, 避免与开始/校验等真正 primary 抢焦点.
        column(6, downloadButton(ns("dl"), "下载参考文献表 (.txt)"))
      )
    ),
    analysis_card(
      "Crossref DOI 校验",
      desc = "对语料中前 50 个 DOI 查 Crossref, 验证真实性. 公开 API, 无需 key. 耗时 30-60 秒.",
      actionButton(ns("verify"), "校验全部 DOI (前 50)",
                    status = "primary", icon = icon("check-double")),
      br(), br(),
      DT::DTOutput(ns("verified"))
    )
  )
}

aiCiteServer <- function(id, corpus, shared) {
  moduleServer(id, function(input, output, session) {
    verified <- reactiveVal(NULL)

    output$dl <- downloadHandler(
      filename = function() sprintf("bibliography_%s.txt", input$style),
      content  = function(file) {
        req(corpus())
        export_bibliography(corpus(), style = input$style, path = file)
      }
    )

    observeEvent(input$verify, {
      req(corpus())
      dois <- corpus()$DI
      dois <- dois[!is.na(dois) & nzchar(dois)]
      dois <- utils::head(unique(dois), 50L)
      if (!length(dois)) {
        showNotification("语料中无可用 DOI 字段 (DI)", type = "warning")
        return()
      }
      withProgress(message = "Crossref 校验中……", value = 0, {
        n <- length(dois)
        res <- vector("list", n)
        for (i in seq_len(n)) {
          res[[i]] <- tryCatch(
            list(doi = dois[i], valid = TRUE,
                 meta = crossref_lookup(dois[i])),
            error = function(e)
              list(doi = dois[i], valid = FALSE, meta = NULL))
          shiny::incProgress(1 / n, detail = sprintf("%d / %d", i, n))
        }
        verified(data.frame(
          doi       = vapply(res, `[[`, character(1), "doi"),
          valid     = vapply(res, `[[`, logical(1),   "valid"),
          year      = vapply(res, function(r) {
            if (is.null(r$meta)) NA_integer_
            else as.integer(r$meta$year %||% NA_integer_)
          }, integer(1)),
          title     = vapply(res, function(r) {
            if (is.null(r$meta)) NA_character_
            else as.character(r$meta$title %||% "")
          }, character(1)),
          stringsAsFactors = FALSE))
      })
    })

    output$verified <- DT::renderDT({
      DT::datatable(verified(),
                    options = list(pageLength = 15, scrollX = TRUE))
    })
  })
}

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0L) b else a
