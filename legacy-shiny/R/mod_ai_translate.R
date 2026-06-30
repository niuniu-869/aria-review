# R/mod_ai_translate.R — 文献批量翻译 UI/Server

aiTranslateUI <- function(id) {
  ns <- NS(id)
  tagList(
    page_header(LBL$menu_ai_translate,
                "这页能做: 批量把英文文献的标题和摘要翻译成中文 (或反过来), 写综述时方便快速扫读"),
    analysis_card(
      "文献翻译",
      desc = "对语料中的标题/摘要批量翻译, 默认英→中, 支持英中双向.",
      fluidRow(
        column(4, selectInput(ns("dir"), "方向",
                              choices = c("英文 → 中文" = "en2zh",
                                          "中文 → 英文" = "zh2en"))),
        column(4, selectInput(ns("field"), "字段",
                              choices = c("仅标题 TI"   = "TI",
                                          "仅摘要 AB"   = "AB",
                                          "标题+摘要"   = "BOTH"))),
        column(4, numericInput(ns("top_n"),
                                "处理前 N 条 (按被引降序, 控制成本)",
                                value = 20, min = 1, max = 500))
      ),
      fluidRow(
        column(4, actionButton(ns("go"), "开始翻译", status = "primary",
                                icon = icon("language"))),
        column(4, downloadButton(ns("dl"), "下载结果 csv"))
      ),
      br(),
      verbatimTextOutput(ns("status"))
    ),
    analysis_card("翻译结果", DT::DTOutput(ns("table")))
  )
}

aiTranslateServer <- function(id, corpus, shared,
                              provider = "deepseek",
                              model = "deepseek-v4-flash") {
  moduleServer(id, function(input, output, session) {

    # FINDING-014: 状态文案改为指向下一步动作
    results <- reactiveVal(NULL)
    status  <- reactiveVal("→ 设定方向 / 字段 / 数量后点击『开始翻译』")

    observeEvent(input$go, {
      req(corpus())
      M <- corpus()
      M$TC <- suppressWarnings(as.numeric(M$TC))
      idx <- order(-M$TC, na.last = TRUE)[seq_len(min(input$top_n, nrow(M)))]
      sub <- M[idx, , drop = FALSE]
      fields <- if (input$field == "BOTH") c("TI", "AB") else input$field

      out <- sub
      withProgress(message = "AI 翻译中……", value = 0, {
        total_steps <- length(fields) * nrow(sub)
        step <- 0L
        for (f in fields) {
          col_tr <- paste0(f, "_tr")
          out[[col_tr]] <- vapply(sub[[f]], function(txt) {
            step <<- step + 1L
            if (!nzchar(txt %||% "")) return(NA_character_)
            msg <- prompt_translate(txt, direction = input$dir)
            r <- tryCatch(
              llm_call(provider, messages = msg, model = model,
                       max_tokens = 1024L,
                       api_key = resolve_llm_key(provider, session)),
              error = function(e) {
                warning(sprintf("[降级] 翻译: %s", safe_log_error(e)))
                list(text = NA_character_,
                     usage = list(prompt_tokens_hit=0L,
                                  prompt_tokens_miss=0L,
                                  completion_tokens=0L))
              })
            shiny::isolate(cost_add(shared, provider, model, r$usage))
            shiny::incProgress(1 / total_steps,
                               detail = sprintf("%d / %d", step, total_steps))
            r$text %||% NA_character_
          }, character(1))
        }
      })
      results(out)
      status(sprintf("完成: %d 条", nrow(out)))
    })

    output$status <- renderText(status())
    output$table <- DT::renderDT({
      req(results())
      keep <- intersect(c("TI","TI_tr","AB","AB_tr","PY","SO","TC"),
                        names(results()))
      DT::datatable(results()[, keep, drop = FALSE],
                    options = list(pageLength = 10, scrollX = TRUE))
    })
    output$dl <- downloadHandler(
      filename = function() sprintf("translate_%s.csv",
                                     format(Sys.time(), "%Y%m%d_%H%M%S")),
      content  = function(file) {
        utils::write.csv(results(), file, row.names = FALSE, fileEncoding = "UTF-8")
      }
    )
  })
}

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0L) b else a
