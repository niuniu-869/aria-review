# R/mod_ai_summary.R — 文献批量摘要提炼

aiSummaryUI <- function(id) {
  ns <- NS(id)
  tagList(
    page_header(LBL$menu_ai_summary,
                "这页能做: AI 把每篇论文的「研究问题 / 方法 / 主要结论」提炼出来, 写综述前一眼扫完几十篇"),
    analysis_card(
      "文献总结",
      desc = "提炼语料前 N 篇 (按被引降序) 的研究问题/方法/结论, 中文 200 字以内.",
      fluidRow(
        column(4, numericInput(ns("top_n"), "处理前 N 条",
                                value = 10, min = 1, max = 200)),
        column(4, actionButton(ns("go"), "开始总结",
                                status = "primary", icon = icon("compress"))),
        column(4, downloadButton(ns("dl"), "下载 csv"))
      ),
      br(),
      verbatimTextOutput(ns("status"))
    ),
    analysis_card("总结结果", DT::DTOutput(ns("table")))
  )
}

aiSummaryServer <- function(id, corpus, shared,
                            provider = "deepseek",
                            model = "deepseek-v4-flash") {
  moduleServer(id, function(input, output, session) {

    # FINDING-014: 状态文案改为指向下一步动作
    results <- reactiveVal(NULL)
    status  <- reactiveVal("→ 调整处理条数后点击『开始总结』")

    observeEvent(input$go, {
      req(corpus())
      M <- corpus()
      M$TC <- suppressWarnings(as.numeric(M$TC))
      idx <- order(-M$TC, na.last = TRUE)[seq_len(min(input$top_n, nrow(M)))]
      sub <- M[idx, , drop = FALSE]

      withProgress(message = "AI 总结中……", value = 0, {
        out_text <- vapply(seq_len(nrow(sub)), function(i) {
          msg <- prompt_summary(list(ti = sub$TI[i] %||% "",
                                      ab = sub$AB[i] %||% ""))
          r <- tryCatch(
            llm_call(provider, messages = msg, model = model,
                     max_tokens = 400L,
                     api_key = resolve_llm_key(provider, session)),
            error = function(e) {
              warning(sprintf("[降级] 总结: %s", safe_log_error(e)))
              list(text = NA_character_,
                   usage = list(prompt_tokens_hit=0L,
                                prompt_tokens_miss=0L,
                                completion_tokens=0L))
            })
          shiny::isolate(cost_add(shared, provider, model, r$usage))
          shiny::incProgress(1 / nrow(sub),
                             detail = sprintf("%d / %d", i, nrow(sub)))
          r$text %||% NA_character_
        }, character(1))
      })
      results(data.frame(title   = sub$TI,
                          year    = sub$PY,
                          cited   = sub$TC,
                          summary = out_text,
                          stringsAsFactors = FALSE))
      status(sprintf("完成: %d 条", nrow(sub)))
    })

    output$status <- renderText(status())
    output$table  <- DT::renderDT(
      DT::datatable(results(),
                    options = list(pageLength = 10, scrollX = TRUE)))
    output$dl <- downloadHandler(
      filename = function() sprintf("summary_%s.csv",
                                     format(Sys.time(), "%Y%m%d_%H%M%S")),
      content  = function(file) {
        utils::write.csv(results(), file, row.names = FALSE, fileEncoding = "UTF-8")
      }
    )
  })
}

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0L) b else a
