# fct_analysis.R — bibliometrix 调用封装层（纯函数，不依赖 Shiny，便于 TDD）

#' 导入并解析文献语料
#' @param file 上传文件路径
#' @param dbsource "wos" 或 "scopus"
#' @param format "plaintext" / "bibtex" / "csv"
#' @return 标准语料 data.frame（bibliometrix 的 M）
import_corpus <- function(file, dbsource = "wos", format = "plaintext") {
  bibliometrix::convert2df(file = file, dbsource = dbsource, format = format)
}

#' 概览分析：主要信息 + 年度产出 + 三字段图
#' @param M 语料 data.frame
#' @param tf_fields 三字段图字段向量，默认 c("AU", "DE", "SO")
#' @param tf_n 三字段图每字段显示数量，默认 c(20, 20, 20)
#' @return list(results = bibliometrix 分析对象, annual_production = data.frame,
#'              three_fields = plotly 桑基图或 NULL（字段唯一值不足时）)
analyze_overview <- function(M,
                             tf_fields = c("AU", "DE", "SO"),
                             tf_n = c(20, 20, 20)) {
  results <- bibliometrix::biblioAnalysis(M, sep = ";")
  yr <- as.data.frame(table(M$PY), stringsAsFactors = FALSE)
  names(yr) <- c("year", "articles")
  yr$year <- as.integer(yr$year)
  yr <- yr[!is.na(yr$year), ]
  tf <- tryCatch(
    bibliometrix::threeFieldsPlot(M, fields = tf_fields, n = tf_n),
    error = function(e) {
      warning(sprintf("[降级] 概览 threeFieldsPlot: %s", conditionMessage(e)))
      NULL
    }
  )
  list(results = results, annual_production = yr, three_fields = tf)
}

#' 来源分析：最相关来源 + 来源 h 指数 + Bradford 定律
analyze_sources <- function(M) {
  missing_cols <- setdiff(c("SO", "TI", "PY", "TC"), names(M))
  if (length(missing_cols) > 0) {
    stop(sprintf("缺少必需字段：%s，无法进行来源分析",
                 paste(missing_cols, collapse = "、")))
  }
  so_tbl <- sort(table(M$SO), decreasing = TRUE)
  mr <- data.frame(source = names(so_tbl), articles = as.integer(so_tbl),
                   stringsAsFactors = FALSE, row.names = NULL)

  h <- bibliometrix::Hindex(M, field = "source", elements = NULL,
                            sep = ";", years = Inf)$H

  brad <- tryCatch(
    bibliometrix::bradford(M)$table,
    error = function(e) {
      warning(sprintf("[降级] 来源分析 bradford: %s", conditionMessage(e)))
      data.frame(SO = character(0), Rank = integer(0),
                 Freq = integer(0), stringsAsFactors = FALSE)
    }
  )

  list(most_relevant = mr, h_index = h, bradford = brad)
}

#' 作者分析：高产作者 + 产出时间线 + Lotka 定律 + 作者 h 指数
#'
#' @details
#' bibliometrix 5.x 注意事项：
#' - `biblioAnalysis()$Authors` 为 `table` 类型，`as.data.frame()` 后列名为 `AU`/`Freq`，
#'   需手动重命名为 `author`/`articles`。
#' - `lotka()` 5.x 要求传入带 `"bibliometrixDB"` 类的 data.frame（原始语料 M），
#'   而非 `biblioAnalysis()` 返回的 `"bibliometrix"` 对象；
#'   `scientometrics` 内置数据集缺失该类属性，需在调用前手动添加。
#' @param M 语料 data.frame（bibliometrix 的 M）
#' @return list(most_productive, production_over_time, lotka, h_index)
analyze_authors <- function(M) {
  missing_cols <- setdiff(c("AU", "TI", "PY", "TC"), names(M))
  if (length(missing_cols) > 0) {
    stop(sprintf("缺少必需字段：%s，无法进行作者分析",
                 paste(missing_cols, collapse = "、")))
  }
  results <- bibliometrix::biblioAnalysis(M, sep = ";")

  mp <- as.data.frame(results$Authors, stringsAsFactors = FALSE)
  names(mp) <- c("author", "articles")

  pot <- bibliometrix::authorProdOverTime(M, k = 10, graph = FALSE)$dfAU

  # lotka() 5.x 仅接受 "bibliometrixDB" 类；为兼容内置数据集，临时补充该类属性
  M_db <- M
  if (!inherits(M_db, "bibliometrixDB")) {
    class(M_db) <- c("bibliometrixDB", class(M_db))
  }
  lk <- tryCatch(
    bibliometrix::lotka(M_db),
    error = function(e) {
      warning(sprintf("[降级] 作者分析 lotka: %s", conditionMessage(e)))
      list(error = conditionMessage(e))
    }
  )

  h <- bibliometrix::Hindex(M, field = "author", elements = NULL,
                            sep = ";", years = Inf)$H

  list(most_productive = mp, production_over_time = pot,
       lotka = lk, h_index = h)
}

#' 文档与关键词分析：高被引文献/参考文献 + 词频 + 趋势主题
#'
#' @details
#' bibliometrix 5.x 注意事项：
#' - `citations()$Cited` 为 `table` 类型，`as.data.frame()` 后列名为 `CR`/`Freq`，
#'   需重命名为 `reference`/`cited` 以符合语义。
#' - `tableTag()` 返回 named table，用 `names()` + `as.integer()` 转为 data.frame。
#' - `fieldByYear()` 返回 list，包含 `$df`（tibble），列名为 `item`/`freq`/`year_*`。
#' @param M 语料 data.frame（bibliometrix 的 M）
#' @return list(most_cited_docs, most_cited_refs, word_freq, trend_topics)
analyze_documents <- function(M) {
  missing_cols <- setdiff(c("TC", "TI", "AU", "PY"), names(M))
  if (length(missing_cols) > 0) {
    stop(sprintf("缺少必需字段：%s，无法进行文档与关键词分析",
                 paste(missing_cols, collapse = "、")))
  }
  # 高被引文献：按 TC（被引次数）降序排列
  M2 <- M
  M2$TC <- as.numeric(M2$TC)
  ord <- order(M2$TC, decreasing = TRUE)
  mcd <- data.frame(
    title  = M2$TI[ord],
    author = M2$AU[ord],
    year   = M2$PY[ord],
    cited  = M2$TC[ord],
    stringsAsFactors = FALSE
  )

  # 高被引参考文献：citations()$Cited 是 table，as.data.frame 后列名为 CR/Freq
  cr_raw <- tryCatch(
    bibliometrix::citations(M, field = "article", sep = ";")$Cited,
    error = function(e) {
      warning(sprintf("[降级] 文档分析 citations: %s", conditionMessage(e)))
      NULL
    }
  )
  if (!is.null(cr_raw)) {
    mcr <- as.data.frame(cr_raw, stringsAsFactors = FALSE)
    names(mcr) <- c("reference", "cited")
  } else {
    mcr <- data.frame(reference = character(0), cited = integer(0),
                      stringsAsFactors = FALSE)
  }

  # 词频：tableTag() 返回 named table
  wf_raw <- tryCatch(
    bibliometrix::tableTag(M, Tag = "DE", sep = ";"),
    error = function(e) {
      warning(sprintf("[降级] 文档分析 tableTag: %s", conditionMessage(e)))
      NULL
    }
  )
  if (!is.null(wf_raw) && length(wf_raw) > 0) {
    word_freq <- data.frame(term = names(wf_raw), freq = as.integer(wf_raw),
                            stringsAsFactors = FALSE)
  } else {
    word_freq <- data.frame(term = character(0), freq = integer(0),
                            stringsAsFactors = FALSE)
  }

  # 趋势主题：fieldByYear()$df 为 tibble，列名含 item/freq/year_*
  tt <- tryCatch(
    bibliometrix::fieldByYear(M, field = "DE", min.freq = 2,
                              n.items = 5, graph = FALSE)$df,
    error = function(e) {
      warning(sprintf("[降级] 文档分析 fieldByYear: %s", conditionMessage(e)))
      data.frame(item = character(0), freq = integer(0),
                 year_q1 = numeric(0), year_med = numeric(0),
                 year_q3 = numeric(0),
                 stringsAsFactors = FALSE)
    }
  )

  list(most_cited_docs = mcd, most_cited_refs = mcr,
       word_freq = word_freq, trend_topics = tt)
}

#' 概念结构分析：关键词共现网络 + 主题图
#'
#' @details
#' bibliometrix 5.x 注意事项：
#' - `networkPlot()` 返回 list，igraph 对象在 `$graph` 字段（已在 5.x 确认）。
#' - `thematicMap()` 返回 list，包含 `$map`（ggplot 对象）与 `$clusters`（tbl_df/data.frame）。
#' - `minfreq = 5` 对 147 行测试语料有效；生产语料通常更大，该默认值合理。
#' @param M 语料 data.frame（bibliometrix 的 M）
#' @param n_nodes 共现网络保留节点数，默认 50
#' @param minfreq 主题图关键词最低频次阈值，默认 5
#' @return list(cooccurrence = networkPlot 结果, thematic_map = thematicMap 结果)
analyze_conceptual <- function(M, n_nodes = 50, minfreq = 5) {
  net_mat <- bibliometrix::biblioNetwork(M, analysis = "co-occurrences",
                                         network = "keywords", sep = ";")
  cooc <- bibliometrix::networkPlot(net_mat, n = n_nodes, type = "fruchterman",
                                    Title = "关键词共现网络", labelsize = 1,
                                    verbose = FALSE)

  tmap <- bibliometrix::thematicMap(M, field = "DE", n = 250, minfreq = minfreq,
                                    stemming = FALSE, size = 0.5,
                                    n.labels = 1, repel = TRUE)

  list(cooccurrence = cooc, thematic_map = tmap)
}

#' 知识结构分析：共被引网络 + 历史直接引用图
#' @param M bibliometrix 语料 data.frame
#' @param n_nodes 共被引网络保留节点数，默认 50
#' @return list(cocitation = networkPlot 结果, historiograph = list(hist = histNetwork 结果或 NULL))
analyze_intellectual <- function(M, n_nodes = 50) {
  net_mat <- bibliometrix::biblioNetwork(M, analysis = "co-citation",
                                         network = "references", sep = ";")
  coc <- bibliometrix::networkPlot(net_mat, n = n_nodes, type = "fruchterman",
                                   Title = "共被引网络", labelsize = 1,
                                   verbose = FALSE)

  hist <- tryCatch(
    bibliometrix::histNetwork(M, min.citations = 1, sep = ";", verbose = FALSE),
    error = function(e) {
      warning(sprintf("[降级] 知识结构 histNetwork: %s", conditionMessage(e)))
      NULL
    }
  )

  list(cocitation = coc, historiograph = list(hist = hist))
}

#' 社会结构分析：作者合作网络 + 国家合作矩阵
analyze_social <- function(M, n_nodes = 50) {
  a_mat <- bibliometrix::biblioNetwork(M, analysis = "collaboration",
                                       network = "authors", sep = ";")
  a_collab <- bibliometrix::networkPlot(a_mat, n = n_nodes, type = "fruchterman",
                                        Title = "作者合作网络", labelsize = 1,
                                        verbose = FALSE)

  M2 <- bibliometrix::metaTagExtraction(M, Field = "AU_CO", sep = ";")
  c_mat <- bibliometrix::biblioNetwork(M2, analysis = "collaboration",
                                       network = "countries", sep = ";")

  list(author_collab = a_collab, country_collab = c_mat)
}
