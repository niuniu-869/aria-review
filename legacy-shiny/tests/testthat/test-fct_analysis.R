test_that("import_corpus 解析 WoS 纯文本为标准语料 data.frame", {
  M <- import_corpus(sample_txt_path(), dbsource = "wos", format = "plaintext")
  expect_s3_class(M, "data.frame")
  expect_gt(nrow(M), 0)
  expect_true(all(c("AU", "TI", "PY", "SO") %in% names(M)))
})

test_that("analyze_overview 返回 bibliometrix 分析结果、年度产出与三字段图", {
  res <- analyze_overview(test_corpus())
  expect_type(res, "list")
  expect_s3_class(res$results, "bibliometrix")
  expect_s3_class(res$annual_production, "data.frame")
  expect_true(all(c("year", "articles") %in% names(res$annual_production)))
  expect_gt(nrow(res$annual_production), 0)
  # three_fields 键必须存在于返回列表（值可为 NULL：当某字段唯一值不足时 threeFieldsPlot 会失败）
  expect_true("three_fields" %in% names(res))
})

test_that("analyze_overview 对多值字段返回 plotly 三字段图", {
  # 用 AU/DE/ID 替代 SO（scientometrics 中 SO 只有 1 个唯一值，threeFieldsPlot 会出错）
  res <- analyze_overview(test_corpus(), tf_fields = c("AU", "DE", "ID"))
  expect_s3_class(res$three_fields, "plotly")
})

test_that("analyze_sources 返回最相关来源、来源 h 指数、Bradford 定律", {
  res <- analyze_sources(test_corpus())
  expect_s3_class(res$most_relevant, "data.frame")
  expect_true(all(c("source", "articles") %in% names(res$most_relevant)))
  expect_s3_class(res$h_index, "data.frame")
  expect_s3_class(res$bradford, "data.frame")
})

test_that("analyze_authors 返回高产作者、产出时间线、Lotka 定律、作者 h 指数", {
  res <- analyze_authors(test_corpus())
  expect_s3_class(res$most_productive, "data.frame")
  expect_s3_class(res$production_over_time, "data.frame")
  expect_type(res$lotka, "list")
  expect_null(res$lotka$error)          # 确保未降级为 tryCatch 兜底的错误 list
  expect_true("Beta" %in% names(res$lotka))  # 验证真实的 Lotka 拟合结果字段
  expect_s3_class(res$h_index, "data.frame")
})

test_that("analyze_documents 返回高被引文献、高被引参考文献、词频、趋势主题", {
  res <- analyze_documents(test_corpus())
  expect_s3_class(res$most_cited_docs, "data.frame")
  expect_s3_class(res$most_cited_refs, "data.frame")
  expect_s3_class(res$word_freq, "data.frame")
  expect_true(all(c("term", "freq") %in% names(res$word_freq)))
  expect_s3_class(res$trend_topics, "data.frame")
  expect_gt(nrow(res$most_cited_docs), 0)
  expect_true(all(c("title","author","year","cited") %in% names(res$most_cited_docs)))
  expect_true(all(diff(res$most_cited_docs$cited) <= 0))   # TC 降序
  expect_gt(nrow(res$most_cited_refs), 0)
  expect_gt(nrow(res$word_freq), 0)
  expect_gt(nrow(res$trend_topics), 0)
})

test_that("analyze_conceptual 返回共现网络、主题图", {
  res <- analyze_conceptual(test_corpus())
  expect_s3_class(res$cooccurrence$graph, "igraph")
  expect_true(!is.null(res$thematic_map$map))
  expect_s3_class(res$thematic_map$clusters, "data.frame")
})

test_that("analyze_intellectual 返回共被引网络与历史引文数据", {
  res <- analyze_intellectual(test_corpus())
  expect_s3_class(res$cocitation$graph, "igraph")
  expect_type(res$historiograph, "list")
  # 夹具含 CR 字段，histNetwork 应成功返回；验证 tryCatch 未掩盖真实失败
  expect_false(is.null(res$historiograph$hist))
})

test_that("analyze_social 返回作者合作网络与国家合作矩阵", {
  res <- analyze_social(test_corpus())
  expect_s3_class(res$author_collab$graph, "igraph")
  expect_true(inherits(res$country_collab, c("matrix", "Matrix")))
  # 验证国家合作矩阵确有内容，而非空 0×0 稀疏矩阵
  expect_gt(nrow(res$country_collab), 0)
})
