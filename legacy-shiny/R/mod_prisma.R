# R/mod_prisma.R — PRISMA 2020 流程图页 (spec §5 / F1)
#
# 五段计数 (识别/去重/筛选/排除/纳入) 可自动填充 + 手动编辑,
# 实时渲染 ggplot 流程图, 支持 PNG(300dpi)/SVG/PDF 导出 (SCI 投稿规格).
# 绘图逻辑在 fct_prisma.R, 本模块只管 UI 与状态.

prismaUI <- function(id) {
  ns <- NS(id)
  tagList(
    page_header("PRISMA 流程图",
                "这页能做: 生成系统综述/Meta 分析必备的 PRISMA 2020 文献筛选流程图 (识别→去重→筛选→排除→纳入), 可一键导出投稿用图"),
    fluidRow(
      column(5,
        analysis_card(
          "筛选计数 (可自动填充 + 手动编辑)",
          desc = "数字可从已上传语料/去重/筛选结果自动带入, 也可手动修改; 排除理由逐行填写.",
          actionButton(ns("autofill"), "从当前语料自动填充",
                       class = "btn-primary", icon = icon("wand-magic-sparkles")),
          br(), br(),
          numericInput(ns("identified"), "① 识别: 检索获得记录数", value = 0, min = 0),
          numericInput(ns("duplicates"), "② 移除的重复记录数",     value = 0, min = 0),
          numericInput(ns("screened"),   "③ 筛选: 去重后进入筛选数", value = 0, min = 0),
          numericInput(ns("excluded"),   "④ 排除: 筛选中排除数",     value = 0, min = 0),
          numericInput(ns("included"),   "⑤ 纳入: 最终纳入研究数",   value = 0, min = 0),
          textAreaInput(ns("reasons"), "排除理由 (每行一条)",
                        rows = 4,
                        placeholder = "例:\n与主题不相关\n非实证研究\n非中英文文献"),
          uiOutput(ns("warnings"))
        )
      ),
      column(7,
        analysis_card(
          "PRISMA 流程图",
          plotOutput(ns("flow"), height = "520px"),
          br(),
          fluidRow(
            column(4, downloadButton(ns("dl_png"), "导出 PNG (300dpi)")),
            column(4, downloadButton(ns("dl_svg"), "导出 SVG")),
            column(4, downloadButton(ns("dl_pdf"), "导出 PDF"))
          )
        )
      )
    )
  )
}

prismaServer <- function(id, corpus, shared) {
  moduleServer(id, function(input, output, session) {

    # 当前计数 (reactive, 来自 5 个 numericInput)
    counts <- reactive({
      list(
        identified = input$identified %||% 0L,
        duplicates = input$duplicates %||% 0L,
        screened   = input$screened   %||% 0L,
        excluded   = input$excluded   %||% 0L,
        included   = input$included   %||% 0L
      )
    })

    # 自动填充: 从 shared / corpus 推导后写回各 numericInput
    observeEvent(input$autofill, {
      af <- prisma_autofill(shared, tryCatch(corpus(), error = function(e) NULL))
      updateNumericInput(session, "identified", value = af$identified)
      updateNumericInput(session, "duplicates", value = af$duplicates)
      updateNumericInput(session, "screened",   value = af$screened)
      updateNumericInput(session, "excluded",   value = af$excluded)
      updateNumericInput(session, "included",   value = af$included)
      showNotification("已从当前语料自动填充, 可手动微调.", type = "message")
    })

    # 一致性提示
    output$warnings <- renderUI({
      w <- prisma_validate(counts())$warnings
      if (!length(w)) return(NULL)
      div(class = "text-warning", style = "margin-top:8px;",
          lapply(w, function(x) div(icon("triangle-exclamation"), x)))
    })

    # 把计数 + 理由同步到 shared, 供『导出报告』页嵌入 PRISMA 图
    observe({
      shared$prisma_state <- list(counts = counts(),
                                  reasons = input$reasons %||% "")
    })

    plot_obj <- reactive({
      prisma_flow_plot(counts(), reasons = input$reasons %||% "")
    })

    output$flow <- renderPlot({ print(plot_obj()) }, res = 96)

    # 导出: ggsave 任意格式/DPI
    .ts <- function() format(Sys.time(), "%Y%m%d_%H%M%S")
    output$dl_png <- downloadHandler(
      filename = function() sprintf("prisma_%s.png", .ts()),
      content  = function(file)
        ggplot2::ggsave(file, plot = plot_obj(), width = 8, height = 7,
                        dpi = 300, bg = "white")
    )
    output$dl_svg <- downloadHandler(
      filename = function() sprintf("prisma_%s.svg", .ts()),
      # 用内置 Cairo svg device (svglite 未装), 避免 ggsave device="svg" 报错
      content  = function(file)
        ggplot2::ggsave(file, plot = plot_obj(), width = 8, height = 7,
                        device = grDevices::svg, bg = "white")
    )
    output$dl_pdf <- downloadHandler(
      filename = function() sprintf("prisma_%s.pdf", .ts()),
      content  = function(file)
        ggplot2::ggsave(file, plot = plot_obj(), width = 8, height = 7,
                        device = grDevices::cairo_pdf, bg = "white")
    )
  })
}

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0L) b else a
