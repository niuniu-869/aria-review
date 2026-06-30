# mod_overview.R — 概览模块

overviewUI <- function(id) {
  ns <- NS(id)
  tagList(
    page_header(LBL$menu_overview,
                "这页回答: 这个领域到底有多大? 增长趋势怎么样? 谁在写? 写啥? 发在哪?"),
    analysis_card("主要信息", verbatimTextOutput(ns("main_info")),
                  desc = "语料的基本统计信息。"),
    # FINDING-007 修复: 给 plot 输出显式 height, 避免数据加载前容器塌陷为空白.
    analysis_card("年度产出趋势",
                  plotly::plotlyOutput(ns("annual"), height = "320px"),
                  desc = "按发表年份统计的文献数量。"),
    analysis_card("三字段图",
                  plotly::plotlyOutput(ns("three_fields"), height = "440px"),
                  desc = "作者—关键词—来源三者关系的桑基图。")
  )
}

overviewServer <- function(id, corpus) {
  moduleServer(id, function(input, output, session) {
    res <- reactive({ req(corpus()); analyze_overview(corpus()) })

    output$main_info <- renderPrint({
      if (is.null(corpus())) return(cat(LBL$no_data))
      tryCatch(
        summary(res()$results, k = 10, pause = FALSE),
        error = function(e) {
          warning(sprintf("[降级] 概览 summary: %s", conditionMessage(e)))
          cat("主要信息暂不可用：当前语料规模过小或字段不完整。")
        }
      )
    })

    output$annual <- plotly::renderPlotly({
      req(corpus())
      ap <- res()$annual_production
      p <- ggplot(ap, aes(x = year, y = articles)) +
        geom_line(color = "#3c8dbc") + geom_point(color = "#3c8dbc") +
        labs(x = "年份", y = "文献数量", title = "年度产出趋势") +
        theme_minimal()
      plotly::ggplotly(p)
    })

    output$three_fields <- plotly::renderPlotly({
      req(corpus())
      tf <- res()$three_fields
      validate(need(!is.null(tf), "三字段图不可用（部分字段唯一值不足）。"))
      tf
    })
  })
}
