# R/mod_pdf_fetch.R — PDF 全文获取 UI/Server
#
# reactive 拆分:
#   · log_lines, hits_rv, misses_rv  事件流增量更新
#   · job_rv                          当前 Job 句柄
#   · cancel_flag                     reactiveVal(FALSE), 取消时翻转

pdfFetchUI <- function(id) {
  ns <- NS(id)
  tagList(
    page_header(LBL$menu_pdf_fetch,
                "这页能做: 给一份 DOI 清单, 自动从开放获取 (OA) 渠道下载 PDF 全文 (Crossref/Unpaywall/OpenAlex 多源回退, 不需要校园账号)"),
    analysis_card(
      "PDF 全文获取",
      desc = paste(
        "粘贴 DOI 列表 (每行一个), 自动尝试合法 OA 全文链路 (Crossref/Unpaywall/OpenAlex/S2/arXiv).",
        "命中的展示文件路径; 未命中的提示请用学校账户访问."
      ),
      textAreaInput(ns("dois"), "DOI 列表",
                    rows = 6,
                    placeholder = "10.1016/j.joi.2017.08.007\n10.1093/comnet/cnv013"),
      fluidRow(
        column(4, actionButton(ns("start"),  "开始获取", status = "primary",
                                icon = icon("play"))),
        column(4, actionButton(ns("cancel"), "取消",     status = "danger",
                                icon = icon("stop")))
      ),
      br(),
      verbatimTextOutput(ns("log"))
    ),
    analysis_card("命中文献",   DT::DTOutput(ns("hits"))),
    analysis_card("未命中清单", DT::DTOutput(ns("misses")))
  )
}

pdfFetchServer <- function(id, corpus, shared, session_dir) {
  moduleServer(id, function(input, output, session) {

    log_lines <- reactiveVal(character(0))
    hits_rv   <- reactiveVal(
      data.frame(doi = character(0), title = character(0),
                 journal = character(0), stage = character(0),
                 path = character(0), stringsAsFactors = FALSE))
    misses_rv <- reactiveVal(
      data.frame(doi = character(0), title = character(0),
                 journal = character(0), reason = character(0),
                 ill_link = character(0), stringsAsFactors = FALSE))
    job_rv      <- reactiveVal(NULL)
    cancel_flag <- reactiveVal(FALSE)

    # 触发增量刷新 (because reactiveVal 不会自动随子进程事件触发)
    refresh_tick <- reactiveVal(0L)

    observeEvent(input$start, {
      req(nzchar(input$dois))
      dois <- trimws(strsplit(input$dois, "\n", fixed = TRUE)[[1]])
      dois <- dois[nzchar(dois)]
      if (!length(dois)) return()

      # 每次开始用一个新的 session 子目录, 避免覆盖之前的结果
      sd <- file.path(session_dir, format(Sys.time(), "%Y%m%d%H%M%S"))
      job <- tryCatch(pdf_job_new(dois, session_dir = sd),
                      error = function(e) {
                        log_lines(c(log_lines(),
                                    sprintf("[错误] %s",
                                            safe_log_error(e))))
                        NULL
                      })
      if (is.null(job)) return()

      cancel_flag(FALSE)
      log_lines(character(0))
      hits_rv(hits_rv()[0, ])
      misses_rv(misses_rv()[0, ])
      refresh_tick(refresh_tick() + 1L)

      on_event <- function(ev) {
        t <- ev$type %||% "?"
        msg <- switch(t,
          stage    = sprintf("─ %s", ev$label %||% ""),
          progress = sprintf("[%s/%s] %s",
                              ev$done %||% "?", ev$total %||% "?",
                              ev$current %||% ""),
          hit      = sprintf("  ✓ [%s] %s", ev$stage %||% "?", ev$key %||% ""),
          miss     = sprintf("  ✗ %s : %s", ev$doi %||% "?", ev$reason %||% ""),
          done     = sprintf("完成: 命中 %s / 未命中 %s",
                              ev$hits %||% 0, ev$misses %||% 0),
          log      = ev$msg %||% "",
          sprintf("[?] %s", jsonlite::toJSON(ev, auto_unbox = TRUE))
        )
        log_lines(c(log_lines(), msg))
        if (identical(t, "hit")) {
          hits_rv(rbind(hits_rv(),
                        data.frame(doi = ev$doi %||% "",
                                   title = ev$title %||% "",
                                   journal = ev$journal %||% "",
                                   stage = ev$stage %||% "",
                                   path = ev$path %||% "",
                                   stringsAsFactors = FALSE)))
        } else if (identical(t, "miss")) {
          misses_rv(rbind(misses_rv(),
                          data.frame(doi = ev$doi %||% "",
                                     title = ev$title %||% "",
                                     journal = ev$journal %||% "",
                                     reason = ev$reason %||% "",
                                     ill_link = ev$ill_link %||% "",
                                     stringsAsFactors = FALSE)))
        }
        refresh_tick(refresh_tick() + 1L)
      }

      job_rv(pdf_job_run(job,
                          on_event = on_event,
                          cancel_flag = function() cancel_flag()))
    })

    observeEvent(input$cancel, {
      cancel_flag(TRUE)
      log_lines(c(log_lines(), "[取消] 已发送终止信号"))
    })

    output$log <- renderText({
      refresh_tick()  # 触发刷新
      paste(utils::tail(log_lines(), 80L), collapse = "\n")
    })
    output$hits <- DT::renderDT({
      refresh_tick()
      DT::datatable(hits_rv(),
                    options = list(pageLength = 10, scrollX = TRUE))
    })
    output$misses <- DT::renderDT({
      refresh_tick()
      DT::datatable(misses_rv(),
                    options = list(pageLength = 10, scrollX = TRUE))
    })
  })
}

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0L) b else a
