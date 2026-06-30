# mod_social.R — 社会结构模块
#
# reactive 拆分说明：
#   res_corpus：仅依赖 corpus()，计算 biblioNetwork 矩阵 + metaTagExtraction（重型操作，跑一次）
#   output$author：依赖 res_corpus()$a_mat + input$n_nodes，networkPlot 单独渲染
#   output$country：依赖 res_corpus()$c_mat + input$n_nodes，networkPlot 单独渲染

socialUI <- function(id) {
  ns <- NS(id)
  tagList(
    page_header(LBL$menu_social,
                "这页回答: 哪些学者/机构/国家是合作中心? 我能跟谁找合作? (合作网络: 共同发文越多, 节点越靠近)"),
    # FINDING-007 修复: 给可视化输出显式 height.
    analysis_card(
      "作者合作网络",
      sliderInput(ns("n_nodes"), "显示节点数", min = 10, max = 100,
                  value = 50, step = 10),
      visNetwork::visNetworkOutput(ns("author"), height = "500px")),
    analysis_card("国家合作网络",
                  visNetwork::visNetworkOutput(ns("country"), height = "500px"),
                  desc = "国家间的科研合作关系。")
  )
}

socialServer <- function(id, corpus) {
  moduleServer(id, function(input, output, session) {

    # 重型 corpus 级计算：biblioNetwork 矩阵与 metaTagExtraction，仅依赖 corpus()
    res_corpus <- reactive({
      req(corpus())
      M <- corpus()
      a_mat <- tryCatch(
        bibliometrix::biblioNetwork(M, analysis = "collaboration",
                                    network = "authors", sep = ";"),
        error = function(e) {
          warning(sprintf("[降级] 社会结构 authors biblioNetwork: %s", conditionMessage(e)))
          NULL
        }
      )
      c_mat <- tryCatch({
        M2 <- bibliometrix::metaTagExtraction(M, Field = "AU_CO", sep = ";")
        bibliometrix::biblioNetwork(M2, analysis = "collaboration",
                                    network = "countries", sep = ";")
      },
      error = function(e) {
        warning(sprintf("[降级] 社会结构 countries biblioNetwork: %s", conditionMessage(e)))
        NULL
      })
      list(a_mat = a_mat, c_mat = c_mat)
    })

    # 作者合作网络：corpus 矩阵已缓存，仅 networkPlot 依赖 n_nodes
    output$author <- visNetwork::renderVisNetwork({
      req(corpus())
      a_mat <- res_corpus()$a_mat
      tryCatch({
        if (is.null(a_mat)) stop("a_mat 为 NULL")
        a_collab <- bibliometrix::networkPlot(a_mat, n = input$n_nodes,
                                              type = "fruchterman",
                                              Title = "作者合作网络",
                                              labelsize = 1, verbose = FALSE)
        visNetwork::visIgraph(a_collab$graph)
      },
      error = function(e) {
        warning(sprintf("[降级] 社会结构 author networkPlot: %s", conditionMessage(e)))
        visNetwork::visNetwork(
          data.frame(id = 1, label = "语料过小或字段不足，无法生成作者合作网络"),
          data.frame(from = integer(0), to = integer(0))
        )
      })
    })

    # 国家合作网络：corpus 矩阵已缓存，仅 networkPlot 依赖 n_nodes
    output$country <- visNetwork::renderVisNetwork({
      req(corpus())
      c_mat <- res_corpus()$c_mat
      tryCatch({
        if (is.null(c_mat)) stop("c_mat 为 NULL")
        cp <- bibliometrix::networkPlot(c_mat, n = input$n_nodes,
                                        type = "fruchterman",
                                        Title = "国家合作网络",
                                        labelsize = 1, verbose = FALSE)
        visNetwork::visIgraph(cp$graph)
      },
      error = function(e) {
        warning(sprintf("[降级] 社会结构 country networkPlot: %s", conditionMessage(e)))
        visNetwork::visNetwork(
          data.frame(id = 1, label = "语料过小或字段不足，无法生成国家合作网络"),
          data.frame(from = integer(0), to = integer(0))
        )
      })
    })
  })
}
