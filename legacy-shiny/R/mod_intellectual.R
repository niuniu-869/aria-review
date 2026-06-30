# mod_intellectual.R — 知识结构模块
#
# reactive 拆分说明：
#   res_corpus：仅依赖 corpus()，计算 co-citation 矩阵与 histNetwork（重型操作，跑一次）
#   output$cocit：依赖 res_corpus()$net_mat + input$n_nodes，用 networkPlot 单独渲染
#   output$hist：依赖 res_corpus()$hist，与 n_nodes 无关，无需重算

intellectualUI <- function(id) {
  ns <- NS(id)
  tagList(
    page_header(LBL$menu_intellectual,
                "这页回答: 这个领域的知识基础是什么? 哪些经典论文被反复引用? (共被引: 两篇论文一起被很多人引就说明它们是这个领域的双柱石)"),
    # FINDING-007 修复: 给可视化输出显式 height.
    analysis_card(
      "共被引网络",
      sliderInput(ns("n_nodes"), "显示节点数", min = 10, max = 100,
                  value = 50, step = 10),
      visNetwork::visNetworkOutput(ns("cocit"), height = "500px"),
      desc = "被同时引用的参考文献构成的网络。"),
    analysis_card("历史引文图",
                  plotOutput(ns("hist"), height = "500px"),
                  desc = "文献间的直接引用脉络（需语料含被引参考文献字段）。")
  )
}

intellectualServer <- function(id, corpus) {
  moduleServer(id, function(input, output, session) {

    # 重型 corpus 级计算：biblioNetwork 矩阵与 histNetwork，仅依赖 corpus()
    res_corpus <- reactive({
      req(corpus())
      M <- corpus()
      net_mat <- tryCatch(
        bibliometrix::biblioNetwork(M, analysis = "co-citation",
                                    network = "references", sep = ";"),
        error = function(e) {
          warning(sprintf("[降级] 知识结构 biblioNetwork: %s", conditionMessage(e)))
          NULL
        }
      )
      hist <- tryCatch(
        bibliometrix::histNetwork(M, min.citations = 1, sep = ";", verbose = FALSE),
        error = function(e) {
          warning(sprintf("[降级] 知识结构 histNetwork: %s", conditionMessage(e)))
          NULL
        }
      )
      list(net_mat = net_mat, hist = hist)
    })

    # 共被引网络：corpus 级矩阵已缓存，仅 networkPlot 依赖 n_nodes
    output$cocit <- visNetwork::renderVisNetwork({
      req(corpus())
      net_mat <- res_corpus()$net_mat
      tryCatch({
        if (is.null(net_mat)) stop("net_mat 为 NULL")
        coc <- bibliometrix::networkPlot(net_mat, n = input$n_nodes,
                                         type = "fruchterman",
                                         Title = "共被引网络",
                                         labelsize = 1, verbose = FALSE)
        visNetwork::visIgraph(coc$graph)
      },
      error = function(e) {
        warning(sprintf("[降级] 知识结构 networkPlot: %s", conditionMessage(e)))
        visNetwork::visNetwork(
          data.frame(id = 1, label = "语料过小或字段不足，无法生成共被引网络"),
          data.frame(from = integer(0), to = integer(0))
        )
      })
    })

    # 历史引文图：仅依赖 corpus 级缓存，不受 n_nodes 影响
    output$hist <- renderPlot({
      req(corpus())
      hist <- res_corpus()$hist
      if (is.null(hist)) {
        plot.new()
        text(0.5, 0.5, "当前语料缺少被引参考文献字段，无法生成历史引文图。",
             cex = 1.2)
      } else {
        tryCatch(
          bibliometrix::histPlot(hist, n = 20, size = 5, labelsize = 4,
                                 verbose = FALSE),
          error = function(e) {
            warning(sprintf("[降级] 知识结构 histPlot: %s", conditionMessage(e)))
            plot.new()
            text(0.5, 0.5, "历史引文图渲染失败，请检查语料。", cex = 1.2)
          }
        )
      }
    })
  })
}
