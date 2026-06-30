# mod_authors.R — 作者分析模块

authorsUI <- function(id) {
  ns <- NS(id)
  tagList(
    page_header(LBL$menu_authors,
                "这页回答: 谁是这个领域的核心学者? 谁的论文最值得读? (h 指数: 用一个数字代表学者影响力)"),
    analysis_card("高产作者", DT::DTOutput(ns("most_productive")),
                  desc = "发文量最高的作者。"),
    analysis_card("作者产出时间线", DT::DTOutput(ns("production"))),
    analysis_card("作者 h 指数", DT::DTOutput(ns("h_index"))),
    analysis_card("Lotka 定律", verbatimTextOutput(ns("lotka")),
                  desc = "作者发文频次分布与 Lotka 定律拟合。")
  )
}

authorsServer <- function(id, corpus) {
  moduleServer(id, function(input, output, session) {
    res <- reactive({ req(corpus()); analyze_authors(corpus()) })
    dt <- function(d) DT::datatable(d, options = list(pageLength = 10,
                                                      scrollX = TRUE))
    output$most_productive <- DT::renderDT({ req(corpus()); dt(res()$most_productive) })
    output$production      <- DT::renderDT({ req(corpus()); dt(res()$production_over_time) })
    output$h_index         <- DT::renderDT({ req(corpus()); dt(res()$h_index) })
    output$lotka           <- renderPrint({ req(corpus()); str(res()$lotka) })
  })
}
