test_that(".analysis_endpoint: DATA_QUALITY → 422, 真异常 → 502", {
  withr::local_dir(file.path("..", ".."))
  source("plumber.R")  # 只定义函数；plumber 注解均为注释，无副作用

  id <- new_corpus_id()
  # 无 PY 的"就绪"语料 → overview_dto 抛 DATA_QUALITY|
  save_corpus(data.frame(AU = "X", SO = "Y", stringsAsFactors = FALSE),
              id, "wos", status = "ready")
  on.exit(unlink(.corpus_path(id)), add = TRUE)

  res <- new.env(); res$status <- 200L
  out <- .analysis_endpoint(id, res, overview_dto)
  expect_equal(res$status, 422)
  expect_equal(out$code, "ANALYSIS_FAILED")
  expect_match(out$message, "出版年")
  expect_false(grepl("DATA_QUALITY\\|", out$message))

  res2 <- new.env(); res2$status <- 200L
  out2 <- .analysis_endpoint(id, res2, function(M) stop("boom-internal"))
  expect_equal(res2$status, 502)
  expect_equal(out2$code, "ANALYSIS_FAILED")
  expect_match(out2$message, "boom-internal")
})
