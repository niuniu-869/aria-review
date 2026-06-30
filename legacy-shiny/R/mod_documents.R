# mod_documents.R — 文档与关键词模块

documentsUI <- function(id) {
  ns <- NS(id)
  tagList(
    page_header(LBL$menu_documents,
                "这页回答: 这个领域有哪些必读的经典文献? 关键词怎么演变的?"),
    analysis_card("高被引文献", DT::DTOutput(ns("mcd"))),
    analysis_card("高被引参考文献", DT::DTOutput(ns("mcr"))),
    # FINDING-007 修复: 给 plot 输出显式 height, 避免空容器塌陷.
    analysis_card("关键词词云",
                  plotOutput(ns("cloud"), height = "420px"),
                  desc = "关键词词频可视化。"),
    analysis_card("趋势主题", DT::DTOutput(ns("trend")))
  )
}

documentsServer <- function(id, corpus) {
  moduleServer(id, function(input, output, session) {
    res <- reactive({ req(corpus()); analyze_documents(corpus()) })
    dt <- function(d) DT::datatable(d, options = list(pageLength = 10,
                                                      scrollX = TRUE))
    output$mcd   <- DT::renderDT({ req(corpus()); dt(res()$most_cited_docs) })
    output$mcr   <- DT::renderDT({ req(corpus()); dt(res()$most_cited_refs) })
    output$trend <- DT::renderDT({ req(corpus()); dt(res()$trend_topics) })
    output$cloud <- renderPlot({
      req(corpus())
      wf <- res()$word_freq
      wf <- utils::head(wf[order(-wf$freq), ], 100)
      ggplot2::ggplot(wf, ggplot2::aes(label = term, size = freq, color = freq)) +
        ggwordcloud::geom_text_wordcloud(rm_outside = TRUE) +
        ggplot2::scale_size_area(max_size = 18) +
        ggplot2::theme_minimal()
    })
  })
}
