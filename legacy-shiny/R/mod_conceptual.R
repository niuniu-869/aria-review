# mod_conceptual.R — 概念结构模块
#
# reactive 拆分说明：
#   res_corpus：仅依赖 corpus()，计算 biblioNetwork 矩阵与 thematicMap（重型操作，跑一次）
#   output$cooc：依赖 res_corpus()$net_mat + input$n_nodes，用 networkPlot 单独渲染
#   output$tmap：依赖 res_corpus()$tmap，与 n_nodes 无关，无需重算

conceptualUI <- function(id) {
  ns <- NS(id)
  tagList(
    page_header(LBL$menu_conceptual,
                "这页回答: 这个领域有哪些主流话题? 哪些是热门、哪些是 niche? (用关键词共现聚类)"),
    # FINDING-007 修复: 给可视化输出显式 height, 避免空容器塌陷.
    analysis_card(
      "关键词共现网络",
      sliderInput(ns("n_nodes"), "显示节点数", min = 10, max = 100,
                  value = 50, step = 10),
      visNetwork::visNetworkOutput(ns("cooc"), height = "500px"),
      desc = "关键词同时出现关系构成的网络。"),
    analysis_card("主题图",
                  plotOutput(ns("tmap"), height = "440px"),
                  desc = "基于 Callon 中心度与密度的主题战略坐标图。"),
    analysis_card("主题演进",
                  plotOutput(ns("tevo"), height = "440px"),
                  desc = "主题随时间的演化（语料过小或时间跨度不足时不可用）。")
  )
}

conceptualServer <- function(id, corpus) {
  moduleServer(id, function(input, output, session) {

    # 重型 corpus 级计算：仅依赖 corpus()，与 n_nodes 无关
    res_corpus <- reactive({
      req(corpus())
      M <- corpus()
      net_mat <- tryCatch(
        bibliometrix::biblioNetwork(M, analysis = "co-occurrences",
                                    network = "keywords", sep = ";"),
        error = function(e) {
          warning(sprintf("[降级] 概念结构 biblioNetwork: %s", conditionMessage(e)))
          NULL
        }
      )
      tmap <- tryCatch(
        bibliometrix::thematicMap(M, field = "DE", n = 250, minfreq = 5,
                                  stemming = FALSE, size = 0.5,
                                  n.labels = 1, repel = TRUE),
        error = function(e) {
          warning(sprintf("[降级] 概念结构 thematicMap: %s", conditionMessage(e)))
          NULL
        }
      )
      list(net_mat = net_mat, tmap = tmap)
    })

    # 共现网络：corpus 级矩阵已缓存，仅 networkPlot 依赖 n_nodes
    output$cooc <- visNetwork::renderVisNetwork({
      req(corpus())
      net_mat <- res_corpus()$net_mat
      tryCatch({
        if (is.null(net_mat)) stop("net_mat 为 NULL")
        cooc <- bibliometrix::networkPlot(net_mat, n = input$n_nodes,
                                          type = "fruchterman",
                                          Title = "关键词共现网络",
                                          labelsize = 1, verbose = FALSE)
        visNetwork::visIgraph(cooc$graph)
      },
      error = function(e) {
        warning(sprintf("[降级] 概念结构 networkPlot: %s", conditionMessage(e)))
        visNetwork::visNetwork(
          data.frame(id = 1, label = "语料过小或字段不足，无法生成共现网络"),
          data.frame(from = integer(0), to = integer(0))
        )
      })
    })

    # 主题图：仅依赖 corpus 级缓存，不受 n_nodes 影响
    output$tmap <- renderPlot({
      req(corpus())
      tryCatch(
        print(res_corpus()$tmap$map),
        error = function(e) {
          warning(sprintf("[降级] 概念结构 tmap 渲染: %s", conditionMessage(e)))
          plot.new()
          text(0.5, 0.5, "语料过小或关键词不足，无法生成主题图", cex = 1.2)
        }
      )
    })

    output$tevo <- renderPlot({
      req(corpus())
      evo <- tryCatch(
        bibliometrix::thematicEvolution(corpus(), field = "DE", n = 250,
                                        minFreq = 2),
        error = function(e) {
          warning(sprintf("[降级] 概念结构 thematicEvolution: %s", conditionMessage(e)))
          NULL
        }
      )
      if (is.null(evo)) {
        plot.new()
        text(0.5, 0.5, "当前语料无法计算主题演进\n（时间跨度或关键词数量不足）",
             cex = 1.2)
      } else {
        print(evo$TM)
      }
    })
  })
}
