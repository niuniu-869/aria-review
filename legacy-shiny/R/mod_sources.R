# mod_sources.R — 来源分析模块

sourcesUI <- function(id) {
  ns <- NS(id)
  tagList(
    page_header(LBL$menu_sources,
                "这页回答: 我应该重点读哪几本期刊? (Bradford 定律: 一个领域 80% 的核心论文集中在少数期刊)"),
    analysis_card("最相关来源", DT::DTOutput(ns("most_relevant")),
                  desc = "发文量最高的期刊/来源。"),
    analysis_card("来源 h 指数", DT::DTOutput(ns("h_index")),
                  desc = "各来源的 h/g/m 指数与被引情况。"),
    analysis_card("Bradford 定律", DT::DTOutput(ns("bradford")),
                  desc = "核心区/相关区/边缘区的期刊分布。")
  )
}

sourcesServer <- function(id, corpus) {
  moduleServer(id, function(input, output, session) {
    res <- reactive({ req(corpus()); analyze_sources(corpus()) })
    dt <- function(d) DT::datatable(d, options = list(pageLength = 10,
                                                      scrollX = TRUE))
    output$most_relevant <- DT::renderDT({ req(corpus()); dt(res()$most_relevant) })
    output$h_index       <- DT::renderDT({ req(corpus()); dt(res()$h_index) })
    output$bradford      <- DT::renderDT({ req(corpus()); dt(res()$bradford) })
  })
}
