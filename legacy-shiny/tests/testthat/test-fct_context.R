test_that("build_context 汇聚分析产出为 LLM 可消费的结构化上下文", {
  ctx <- build_context(test_corpus())
  expect_type(ctx, "list")
  expect_true(all(c("corpus_summary", "theme_clusters",
                    "top_docs", "trend_topics") %in% names(ctx)))

  expect_type(ctx$corpus_summary, "list")
  expect_true(all(c("n_docs", "year_range") %in% names(ctx$corpus_summary)))
  expect_equal(ctx$corpus_summary$n_docs, nrow(test_corpus()))

  expect_s3_class(ctx$theme_clusters, "data.frame")
  expect_s3_class(ctx$top_docs, "data.frame")
  expect_true(all(c("title", "abstract", "cited") %in% names(ctx$top_docs)))
  expect_s3_class(ctx$trend_topics, "data.frame")
})

test_that("build_context 的 top_docs 取前 top_n 篇且按被引降序", {
  ctx <- build_context(test_corpus(), top_n = 15)
  expect_equal(nrow(ctx$top_docs), 15)
  # top_docs 必须按被引数降序——这间接保证 abstract 与 title 的排序一致
  expect_true(all(diff(ctx$top_docs$cited) <= 0))
})

test_that("build_context 的 corpus_summary 含 n_sources 且字段值合理", {
  ctx <- build_context(test_corpus())
  expect_true("n_sources" %in% names(ctx$corpus_summary))
  expect_length(ctx$corpus_summary$year_range, 2)
  expect_lte(ctx$corpus_summary$year_range[1], ctx$corpus_summary$year_range[2])
})
