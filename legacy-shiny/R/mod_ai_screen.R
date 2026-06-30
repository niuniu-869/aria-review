# R/mod_ai_screen.R — 相关性筛选 UI/Server

aiScreenUI <- function(id) {
  ns <- NS(id)
  tagList(
    page_header(LBL$menu_ai_screen,
                "这页能做: AI 从大堆文献里找出跟你研究最相关的几十篇 (每篇打 0-10 分附中文理由, 按阈值筛掉无关的)"),
    analysis_card(
      "相关性筛选",
      desc = "输入研究主题, LLM 对语料逐文打 0-10 分并给中文理由. 同步串行执行, 200 篇约 5-10 分钟.",
      textInput(ns("topic"), "研究主题 (≤ 200 字)", value = "",
                placeholder = "例: 区块链在 ESG 信息披露中的应用"),
      sliderInput(ns("threshold"), "相关性阈值 (≥ N 视为通过)",
                  min = 0, max = 10, value = 5, step = 1),
      fluidRow(
        column(3, actionButton(ns("start"),  "开始筛选", status = "primary",
                                icon = icon("play"))),
        column(3, actionButton(ns("cancel"), "取消",     status = "danger",
                                icon = icon("stop"))),
        # FINDING-004 修复: push_pdf 是次要路由动作 (跳到下一步流程),
        # 不应用 info 青色 - 那是状态色, 不是动作色. 改 secondary.
        column(3, actionButton(ns("push_pdf"), "通过项推送到 PDF 获取",
                                status = "secondary", icon = icon("share"))),
        column(3, downloadButton(ns("dl"), "下载 csv"))
      ),
      br(),
      verbatimTextOutput(ns("status"))
    ),
    analysis_card("筛选结果", DT::DTOutput(ns("table")))
  )
}

aiScreenServer <- function(id, corpus, shared,
                           provider = "deepseek",
                           model = "deepseek-v4-flash") {
  moduleServer(id, function(input, output, session) {

    results <- reactiveVal(NULL)
    # FINDING-014: 状态文案改为指向下一步动作, 提供 affordance
    status  <- reactiveVal("→ 输入研究主题后点击『开始筛选』")
    cancel  <- reactiveVal(FALSE)

    observeEvent(input$start, {
      req(corpus())
      req(nchar(input$topic) > 0, nchar(input$topic) <= 200)
      cancel(FALSE)
      status(sprintf("正在筛选 0/%d", nrow(corpus())))

      job <- screen_job_new(corpus(), topic = input$topic, shared = shared,
                             provider = provider, model = model,
                             cancel_flag = function() cancel(),
                             api_key = resolve_llm_key(provider, session))

      withProgress(message = "AI 筛选中……", value = 0, {
        out <- screen_job_run(job, on_progress = function(i, n) {
          shiny::incProgress(1 / n, detail = sprintf("%d / %d", i, n))
          status(sprintf("已处理 %d / %d", i, n))
        })
        results(out)
        status(sprintf("完成: 共 %d 条 (%d 失败)",
                       nrow(out), sum(out$status == "failed")))
      })
    })

    observeEvent(input$cancel, {
      cancel(TRUE)
      status("已取消")
    })

    filtered <- reactive({
      req(results())
      df <- results()
      df[!is.na(df$relevance) & df$relevance >= input$threshold, , drop = FALSE]
    })

    observeEvent(input$push_pdf, {
      req(filtered())
      passing_dois <- unique(filtered()$doi)
      passing_dois <- passing_dois[nzchar(passing_dois)]
      shared$screen_passed_dois <- passing_dois
      showNotification(sprintf("已推送 %d 个 DOI 到『PDF 全文获取』tab",
                                length(passing_dois)),
                       type = "message")
    })

    output$status <- renderText(status())
    output$table  <- DT::renderDT(
      DT::datatable(filtered(),
                    options = list(pageLength = 15, scrollX = TRUE)))
    output$dl <- downloadHandler(
      filename = function() sprintf("screen_%s.csv",
                                     format(Sys.time(), "%Y%m%d_%H%M%S")),
      content  = function(file) {
        utils::write.csv(filtered(), file, row.names = FALSE, fileEncoding = "UTF-8")
      }
    )
  })
}
